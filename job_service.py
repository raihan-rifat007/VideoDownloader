"""Task lifecycle orchestration for durable and resumable downloads."""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Callable

from download_plan import (
    build_download_command,
    resolve_plan,
    validate_final_file,
    validate_resume_plan,
)
from download_filename import job_download_filename
from download_process import (
    DeadlineTracker,
    ProcessCancelled,
    ProcessStopError,
    run_streaming_process,
)
from job_store import DOWNLOAD_ACTIVE_STATES, JobStore
from progress import normalize_download_progress, parse_progress_line


def _empty_progress() -> dict[str, Any]:
    return normalize_download_progress({}, now=None)


def _safe_source_url(url: Any) -> bool:
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not url.startswith(("-", "--"))
    )


def load_video_info(source_url: str) -> dict[str, Any]:
    """Fetch one metadata object without invoking a shell or saving output."""
    result = subprocess.run(
        ["yt-dlp", "--ignore-config", "--no-playlist", "-j", "--", source_url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Unable to fetch media information")
    for line in result.stdout.splitlines():
        if line.strip():
            try:
                info = json.loads(line)
            except ValueError:
                break
            if isinstance(info, dict):
                return info
            break
    raise ValueError("Media information was empty")


class CancelUnavailableError(RuntimeError):
    """The requested attempt is active in storage but has no local control."""


class JobService:
    def __init__(
        self,
        store: JobStore,
        download_root: str | Path,
        runtime_epoch: str,
        *,
        metadata_loader: Callable[[str], dict[str, Any]] = load_video_info,
        runner: Callable[..., int] = run_streaming_process,
        thread_factory: Callable[..., Any] = threading.Thread,
        probe_runner: Callable[[Path, str], Any] | None = None,
        prepare_timeout: int = 120,
        idle_timeout: int = 300,
        process_timeout: int = 1800,
        max_attempt_seconds: int = 21600,
    ):
        self.store = store
        self.download_root = Path(download_root).resolve()
        self.runtime_epoch = runtime_epoch
        self.metadata_loader = metadata_loader
        self.runner = runner
        self.thread_factory = thread_factory
        self.probe_runner = probe_runner
        self.prepare_timeout = prepare_timeout
        self.idle_timeout = idle_timeout
        self.process_timeout = process_timeout
        self.max_attempt_seconds = max_attempt_seconds
        self._attempt_controls: dict[tuple[str, int], threading.Event] = {}
        self._control_lock = threading.RLock()

    def create(self, request_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request_data, dict):
            raise ValueError("Invalid request body")
        source_url = request_data.get("url", "")
        format_choice = request_data.get("format", "video")
        requested_format_id = request_data.get("format_id")
        if not _safe_source_url(source_url):
            raise ValueError("Invalid URL")
        if format_choice not in {"audio", "video"}:
            raise ValueError("Invalid format choice")
        if requested_format_id is not None and not isinstance(requested_format_id, str):
            raise ValueError("Invalid format selection")
        info = self.metadata_loader(source_url)
        plan = resolve_plan(
            source_url,
            format_choice,
            requested_format_id,
            info=info,
        )
        job_id = uuid.uuid4().hex
        title = request_data.get("title") or info.get("title") or ""
        if not isinstance(title, str):
            title = ""
        title = title[:500]
        now = time.time()
        record = {
            "job_id": job_id,
            "source_url": source_url,
            "title": title,
            "format_choice": format_choice,
            "requested_format_id": requested_format_id,
            "state": "preparing",
            "attempt_no": 1,
            "resource_json": plan,
            "progress_json": _empty_progress(),
            "last_progress_json": None,
            "error_code": None,
            "error_message": None,
            "final_relpath": None,
            "filename": None,
            "created_at": now,
            "updated_at": now,
        }
        self._task_dir(job_id).mkdir(parents=True, exist_ok=False)
        try:
            self.store.insert_job(record)
        except Exception:
            self._task_dir(job_id).rmdir()
            raise
        self._start_attempt(job_id, 1, plan)
        return {"job_id": job_id, "attempt_no": 1}

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        if job["state"] not in {"failed", "interrupted", "cancelled"}:
            raise RuntimeError("Job cannot be resumed in its current state")
        current_plan = resolve_plan(
            job["source_url"],
            job["format_choice"],
            job["requested_format_id"],
            info=self.metadata_loader(job["source_url"]),
        )
        validate_resume_plan(job["resource_json"], current_plan)
        attempt_no = self.store.claim_retry(job_id, job["attempt_no"], self.runtime_epoch)
        if attempt_no is None:
            raise RuntimeError("Job changed before it could be resumed")
        self._start_attempt(job_id, attempt_no, job["resource_json"])
        return {"job_id": job_id, "attempt_no": attempt_no}

    def cancel(self, job_id: str, attempt_no: int) -> dict[str, Any]:
        if not isinstance(attempt_no, int) or isinstance(attempt_no, bool) or attempt_no <= 0:
            raise ValueError("Invalid attempt number")
        job = self._require_job(job_id)
        state = job["state"]
        if state in {"cancelled", "cancelling"} and job["attempt_no"] == attempt_no:
            return self.status(job_id)
        if state in {"cancelled", "cancelling"}:
            raise RuntimeError("Job changed before it could be cancelled")
        if state not in DOWNLOAD_ACTIVE_STATES:
            raise RuntimeError("Job cannot be cancelled in its current state")
        if job["attempt_no"] != attempt_no:
            raise RuntimeError("Job changed before it could be cancelled")

        key = (job_id, attempt_no)
        with self._control_lock:
            cancel_event = self._attempt_controls.get(key)
        if cancel_event is None:
            raise CancelUnavailableError("Active download control is unavailable")

        if not self.store.request_cancel(job_id, attempt_no):
            current = self._require_job(job_id)
            if current["state"] in {"cancelling", "cancelled"}:
                return self.status(job_id)
            raise RuntimeError("Job changed before it could be cancelled")

        cancel_event.set()
        return self.status(job_id)

    def restart(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        if job["state"] in {"preparing", "downloading", "processing", "deleting"}:
            raise RuntimeError("Job is still active")
        return self.create(
            {
                "url": job["source_url"],
                "format": job["format_choice"],
                "format_id": job["requested_format_id"],
                "title": job["title"],
            }
        )

    def status(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        status = {
            "preparing": "downloading",
            "downloading": "downloading",
            "processing": "downloading",
            "cancelling": "downloading",
            "completed": "done",
            "failed": "error",
            "interrupted": "error",
            "deleting": "error",
        }.get(job["state"], "error")
        phase = {
            "preparing": "preparing",
            "downloading": "downloading",
            "processing": "processing",
            "cancelling": "cancelling",
            "cancelled": "cancelled",
            "completed": "complete",
            "failed": "failed",
            "interrupted": "interrupted",
            "deleting": "deleting",
        }.get(job["state"], "failed")
        return {
            "status": status,
            "state": job["state"],
            "attempt_no": job["attempt_no"],
            "error": job["error_message"],
            "error_code": job["error_code"],
            "filename": job_download_filename(job),
            "phase": phase,
            "progress": job["progress_json"],
            "last_progress": job["last_progress_json"],
            "can_retry": job["state"] in {"failed", "interrupted", "cancelled"},
            "resume_candidate": job["state"] in {"failed", "interrupted", "cancelled"}
            and bool(job["resource_json"]),
            "resume_result": "unknown",
            "job_id": job["job_id"],
            "can_cancel": job["state"] in DOWNLOAD_ACTIVE_STATES,
        }

    def list_jobs(self, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        result = self.store.list_jobs(limit, cursor)
        result["items"] = [self._public_job(item) for item in result["items"]]
        return result

    def file_path(self, job_id: str) -> tuple[Path, str]:
        job = self._require_job(job_id)
        if job["state"] != "completed" or not job["final_relpath"]:
            raise RuntimeError("File is not ready")
        root = self.download_root.resolve()
        candidate = (root / Path(job["final_relpath"])).resolve(strict=False)
        task_dir = self._task_dir(job_id).resolve()
        if not candidate.is_relative_to(task_dir) or candidate.is_symlink() or not candidate.is_file():
            raise FileNotFoundError("File is no longer available")
        filename = job_download_filename(job)
        if filename is None:
            raise RuntimeError("Download filename is unavailable")
        return candidate, filename

    def delete(self, job_id: str) -> None:
        job = self._require_job(job_id)
        if job["state"] == "deleted":
            return
        if not self.store.begin_delete(job_id):
            raise RuntimeError("Job is active or cannot be deleted")
        try:
            task_dir = self._task_dir(job_id)
            if task_dir.exists():
                import shutil

                shutil.rmtree(task_dir)
            self.store.finish_delete(job_id)
        except Exception:
            raise

    def recover(self) -> dict[str, Any]:
        result = self.store.recover_active_jobs(self.runtime_epoch)
        if result.get("restart_required"):
            raise RuntimeError("Service restart required before recovering active jobs")
        return result

    def run_attempt(
        self,
        job_id: str,
        attempt_no: int,
        plan: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> None:
        if cancel_event is None:
            with self._control_lock:
                cancel_event = self._attempt_controls.get((job_id, attempt_no))
            if cancel_event is None:
                cancel_event = threading.Event()
        task_dir = self._task_dir(job_id)
        command = build_download_command(plan, task_dir)
        final_path: str | None = None
        last_error_lines: list[str] = []
        last_persisted = 0.0
        deadline_tracker = DeadlineTracker(
            prepare_timeout=self.prepare_timeout,
            idle_timeout=self.idle_timeout,
            process_timeout=self.process_timeout,
            hard_timeout=self.max_attempt_seconds,
        )

        def handle_line(line: str) -> None:
            nonlocal final_path, last_persisted
            if cancel_event.is_set():
                return
            event = parse_progress_line(line)
            if event is None:
                if line.startswith("ERROR:") or line.startswith("WARNING:"):
                    last_error_lines.append(self._clean_error(line))
                    del last_error_lines[:-20]
                return
            deadline_tracker.observe(event)
            now = time.time()
            if event["kind"] == "final":
                final_path = event["path"]
                return
            if event["kind"] == "download":
                data = event["data"]
                if data.get("status") == "downloading":
                    progress = normalize_download_progress(data, now)
                    if now - last_persisted >= 2.0:
                        updated = self.store.update_attempt(
                            job_id,
                            attempt_no,
                            {"state": "downloading", "progress_json": progress},
                            expected_states=DOWNLOAD_ACTIVE_STATES,
                        )
                        if updated:
                            last_persisted = now
                elif data.get("status") == "finished":
                    progress = normalize_download_progress(data, now)
                    self.store.update_attempt(
                        job_id,
                        attempt_no,
                        {"state": "processing", "progress_json": None, "last_progress_json": progress},
                        expected_states=DOWNLOAD_ACTIVE_STATES,
                    )
                    last_persisted = now
            elif event["kind"] == "postprocess":
                self.store.update_attempt(
                    job_id,
                    attempt_no,
                    {"state": "processing", "progress_json": None},
                    expected_states=DOWNLOAD_ACTIVE_STATES,
                )

        try:
            returncode = self.runner(
                command,
                handle_line,
                timeout_seconds=self.max_attempt_seconds,
                deadline_tracker=deadline_tracker,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                self._cancelled(job_id, attempt_no, returncode)
                return
            if returncode != 0:
                self._fail(
                    job_id,
                    attempt_no,
                    "downloader_failed",
                    last_error_lines[-1] if last_error_lines else "Download failed",
                    returncode,
                )
                return
            if final_path is None:
                self._fail(job_id, attempt_no, "final_file_missing", "Download completed without a final file", returncode)
                return
            if cancel_event.is_set():
                self._cancelled(job_id, attempt_no, returncode)
                return
            result = validate_final_file(
                task_dir,
                final_path,
                plan["format_choice"],
                probe_runner=self.probe_runner,
            )
            final_relpath = (Path("jobs") / job_id / result["relative_path"]).as_posix()
            complete_progress = _empty_progress()
            complete_progress["percent"] = 100.0
            if cancel_event.is_set():
                self._cancelled(job_id, attempt_no, returncode)
                return
            if not self.store.finalize_attempt(
                job_id,
                attempt_no,
                "completed",
                {
                    "progress_json": complete_progress,
                    "final_relpath": final_relpath,
                    "filename": result["filename"],
                },
                returncode,
            ) and cancel_event.is_set():
                self._cancelled(job_id, attempt_no, returncode)
        except ProcessCancelled as exc:
            if cancel_event.is_set():
                self._cancelled(job_id, attempt_no, exc.exit_code)
            else:
                self._fail(job_id, attempt_no, "download_failed", "Download failed", exc.exit_code)
        except subprocess.TimeoutExpired as exc:
            if cancel_event.is_set():
                self._cancelled(job_id, attempt_no, None)
            else:
                reason = getattr(exc, "reason", "attempt_timeout")
                self._fail(job_id, attempt_no, reason, f"Download timed out ({reason})", None)
        except (OSError, ValueError) as exc:
            if cancel_event.is_set():
                self._cancelled(job_id, attempt_no, None)
            else:
                self._fail(job_id, attempt_no, "download_failed", str(exc)[:1000], None)
        except ProcessStopError:
            if not cancel_event.is_set():
                self._fail(job_id, attempt_no, "download_failed", "Unable to stop download process", None)
        except Exception:
            if cancel_event.is_set():
                self._cancelled(job_id, attempt_no, None)
            else:
                self._fail(job_id, attempt_no, "download_failed", "Download failed", None)
        finally:
            self._remove_attempt_control(job_id, attempt_no, cancel_event)

    def _start_attempt(self, job_id: str, attempt_no: int, plan: dict[str, Any]) -> None:
        try:
            cancel_event = threading.Event()
            with self._control_lock:
                self._attempt_controls[(job_id, attempt_no)] = cancel_event
            thread = self.thread_factory(
                target=self.run_attempt,
                args=(job_id, attempt_no, plan, cancel_event),
                name=f"reclip-download-{job_id}",
                daemon=True,
            )
            thread.start()
        except Exception:
            with self._control_lock:
                self._attempt_controls.pop((job_id, attempt_no), None)
            self._fail(job_id, attempt_no, "thread_start_failed", "Download could not be started", None)

    def _remove_attempt_control(
        self, job_id: str, attempt_no: int, cancel_event: threading.Event
    ) -> None:
        with self._control_lock:
            if self._attempt_controls.get((job_id, attempt_no)) is cancel_event:
                self._attempt_controls.pop((job_id, attempt_no), None)

    def _cancelled(self, job_id: str, attempt_no: int, exit_code: int | None) -> bool:
        return self.store.finalize_attempt(
            job_id,
            attempt_no,
            "cancelled",
            {"progress_json": None},
            exit_code,
        )

    def _fail(
        self,
        job_id: str,
        attempt_no: int,
        error_code: str,
        message: str,
        exit_code: int | None,
    ) -> bool:
        return self.store.finalize_attempt(
            job_id,
            attempt_no,
            "failed",
            {
                "progress_json": None,
                "error_code": error_code,
                "error_message": message,
            },
            exit_code,
        )

    def _require_job(self, job_id: str) -> dict[str, Any]:
        if not isinstance(job_id, str) or not reclip_job_id(job_id):
            raise KeyError("Job not found")
        job = self.store.get_job(job_id)
        if job is None or job["state"] == "deleted":
            raise KeyError("Job not found")
        return job

    def _task_dir(self, job_id: str) -> Path:
        if not reclip_job_id(job_id):
            raise ValueError("Invalid job ID")
        return self.download_root / "jobs" / job_id

    @staticmethod
    def _clean_error(line: str) -> str:
        return line.split(":", 1)[1].strip()[:1000]

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job["job_id"],
            "title": job["title"],
            "format": job["format_choice"],
            "state": job["state"],
            "attempt_no": job["attempt_no"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "filename": job_download_filename(job),
            "error": job["error_message"],
            "progress": job["progress_json"],
            "last_progress": job["last_progress_json"],
            "can_retry": job["state"] in {"failed", "interrupted", "cancelled"},
            "can_cancel": job["state"] in DOWNLOAD_ACTIVE_STATES,
        }


def reclip_job_id(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 32:
        return False
    return all(char in "0123456789abcdef" for char in value)
