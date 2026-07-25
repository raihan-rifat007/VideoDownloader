"""Deterministic, restart-safe media preparation and ASR orchestration.

The web process only persists jobs.  This module is deliberately worker-only: it
claims no work itself and processes exactly one already-claimed job at a time.
GPU-heavy enhancement and transcription are executed as child processes so CUDA
allocations disappear before the next stage starts.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .profiles import TranscriptionProfile, get_profile
from .utils import validate_public_url


SCHEMA_VERSION = 1
LARGE_V3_REPOSITORY = "Systran/faster-whisper-large-v3"
LARGE_V3_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
MEDIUM_REPOSITORY = "Systran/faster-whisper-medium"
MEDIUM_REVISION = "08e178d48790749d25932bbc082711ddcfdfbc4f"


MODEL_IDENTITIES = {
    "medium": (MEDIUM_REPOSITORY, MEDIUM_REVISION),
    "large-v3": (LARGE_V3_REPOSITORY, LARGE_V3_REVISION),
}

_STAGE_PROGRESS = {
    "acquiring": 5,
    "uploading": 5,
    "probing": 15,
    "preparing": 30,
    "enhancing": 50,
    "transcribing": 70,
    "validating": 95,
    "completed": 100,
}


class PipelineError(RuntimeError):
    """A user-visible, sanitized pipeline failure."""


class JobCanceled(PipelineError):
    """Raised when cancellation is observed between or during stages."""


class WorkerStopping(PipelineError):
    """Raised to stop without changing the recoverable leased job state."""


@dataclass
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_profile(name: str | None) -> TranscriptionProfile:
    try:
        return get_profile(name)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc


def model_identity(model: str) -> tuple[str, str]:
    try:
        return MODEL_IDENTITIES[model]
    except KeyError as exc:
        raise PipelineError(f"No pinned model identity exists for {model}") from exc


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def sha256_file(path: Path, tick: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    bytes_since_tick = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            bytes_since_tick += len(block)
            if tick and bytes_since_tick >= 64 * 1024 * 1024:
                tick()
                bytes_since_tick = 0
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_error(value: Any, limit: int = 4000) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(authorization|cookie|token|password|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=<redacted>", text)
    text = re.sub(r"https?://[^\s'\"]+", lambda match: _redact_log_url(match.group(0)), text)
    text = text.replace("\x00", "").strip()
    return text[-limit:] if text else "Unknown pipeline error"


def _redact_log_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "<redacted>", ""))
    except (TypeError, ValueError):
        return "<redacted-url>"


def sanitize_source_url(value: str) -> str:
    """Keep source identity while redacting credential-like query parameters."""
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        sensitive = re.compile(r"(?i)(token|sig(nature)?|auth|key|cookie|session|password|credential)")
        query = [
            (key, "<redacted>" if sensitive.search(key) else item)
            for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urllib.parse.urlunsplit(
            (parsed.scheme, host, parsed.path, urllib.parse.urlencode(query), "")
        )
    except (TypeError, ValueError):
        raise PipelineError("Only public http/https media URLs are supported")


def validate_wav(path: Path, sample_rate: int) -> bool:
    try:
        with wave.open(str(path), "rb") as wav:
            return (
                wav.getnchannels() == 1
                and wav.getframerate() == sample_rate
                and wav.getsampwidth() == 2
                and wav.getnframes() > 0
            )
    except (OSError, EOFError, wave.Error):
        return False


def validate_json_file(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return path.stat().st_size > 2
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def read_segments(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PipelineError(f"Invalid JSONL at line {line_number}") from exc
            if not isinstance(row, dict):
                raise PipelineError(f"Invalid segment at line {line_number}")
            rows.append(row)
    return rows


def validate_transcript_outputs(
    transcript_path: Path,
    segments_path: Path,
    asr_metadata_path: Path,
    expected_duration: float | None = None,
) -> dict[str, Any]:
    try:
        transcript = transcript_path.read_text(encoding="utf-8").strip()
        metadata = json.loads(asr_metadata_path.read_text(encoding="utf-8"))
        segments = read_segments(segments_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError("Transcription outputs are unreadable") from exc

    if not transcript or not segments:
        raise PipelineError("Transcription produced no speech segments")
    declared_count = metadata.get("segment_count", metadata.get("segments"))
    if declared_count != len(segments):
        raise PipelineError("Transcript segment count does not match metadata")

    previous_start = -1.0
    previous_end = -1.0
    for index, segment in enumerate(segments, start=1):
        try:
            start = float(segment["start"])
            end = float(segment["end"])
            text = str(segment["text"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError(f"Invalid segment fields at line {index}") from exc
        if start < 0 or end < start or start < previous_start or end < previous_end or not text:
            raise PipelineError(f"Non-monotonic or empty segment at line {index}")
        previous_start, previous_end = start, end

    duration = metadata.get("duration_seconds", metadata.get("duration"))
    try:
        duration_value = float(duration)
    except (TypeError, ValueError) as exc:
        raise PipelineError("ASR metadata has no valid duration") from exc
    if duration_value <= 0 or previous_end > duration_value + max(2.0, duration_value * 0.02):
        raise PipelineError("Transcript timestamps are outside the ASR duration")
    if expected_duration and expected_duration > 0:
        tolerance = max(15.0, expected_duration * 0.20)
        if abs(duration_value - expected_duration) > tolerance:
            raise PipelineError("ASR duration is inconsistent with the source media")
    if not metadata.get("detected_language"):
        raise PipelineError("ASR metadata has no detected language")
    return {"metadata": metadata, "segments": segments}


class DatabaseFacade:
    """Small compatibility boundary around :class:`reclip_asr.db.Database`.

    Database methods are intentionally centralized here so the SQLite layer can
    evolve without allowing persistence details into media processing code.
    """

    def __init__(self, database: Any, worker_id: str | None = None):
        self.database = database
        self.worker_id = worker_id

    @staticmethod
    def _invoke(method: Callable[..., Any], variants: Sequence[tuple[tuple[Any, ...], dict[str, Any]]]) -> Any:
        signature = None
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            pass
        last_bind_error: TypeError | None = None
        for args, kwargs in variants:
            if signature is not None:
                try:
                    signature.bind(*args, **kwargs)
                except TypeError as exc:
                    last_bind_error = exc
                    continue
            return method(*args, **kwargs)
        if last_bind_error:
            raise last_bind_error
        return None

    def update(self, job_id: str, **fields: Any) -> None:
        for name in ("update_job", "set_job_fields"):
            method = getattr(self.database, name, None)
            if method:
                self._invoke(method, (((job_id,), fields), ((job_id, fields), {})))
                return
        transition = getattr(self.database, "transition_job", None)
        if transition:
            requested_status = fields.get("status")
            stage = fields.get("stage")
            status = stage if requested_status == "running" and stage else requested_status or stage
            if status:
                kwargs = {
                    "status": status,
                    "worker_id": self.worker_id,
                    "progress": fields.get("progress", _STAGE_PROGRESS.get(status)),
                    "error": fields.get("error"),
                    "metadata": fields.get("metadata"),
                }
                self._invoke(
                    transition,
                    (
                        ((job_id,), kwargs),
                        (
                            (job_id, status),
                            {key: value for key, value in kwargs.items() if key != "status"},
                        ),
                    ),
                )
                return
        stage = fields.get("stage")
        if stage and hasattr(self.database, "set_job_stage"):
            self.database.set_job_stage(job_id, stage)

    def artifact(self, job_id: str, key: str, record: Mapping[str, Any]) -> None:
        method = getattr(self.database, "register_artifact", None) or getattr(
            self.database, "add_artifact", None
        )
        if not method:
            return
        kwargs = {
            "key": key,
            "path": record["path"],
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "mime_type": record.get("mime_type"),
        }
        self._invoke(
            method,
            (
                (
                    (job_id, key, record["path"]),
                    {
                        "filename": Path(str(record["path"])).name,
                        "media_type": record.get("mime_type"),
                        "sha256": record["sha256"],
                    },
                ),
                ((job_id,), kwargs),
                ((job_id, key), {key_: value for key_, value in kwargs.items() if key_ != "key"}),
                ((job_id, key, record["path"], record["sha256"], record["size_bytes"], record.get("mime_type")), {}),
            ),
        )

    def canceled(self, job_id: str) -> bool:
        method = getattr(self.database, "is_cancel_requested", None)
        if method:
            return bool(method(job_id))
        method = getattr(self.database, "get_job", None)
        if not method:
            return False
        job = method(job_id)
        if not job:
            return False
        if not isinstance(job, Mapping) and hasattr(job, "keys"):
            job = {key: job[key] for key in job.keys()}
        return bool(job.get("cancel_requested") or job.get("status") == "cancel_requested")

    def touch(self, job_id: str) -> None:
        for name in ("touch_job_heartbeat", "heartbeat_job"):
            method = getattr(self.database, name, None)
            if method:
                self._invoke(method, (((job_id, self.worker_id), {}), ((job_id,), {})))
                return

    def checkpoint(
        self, job_id: str, stage: str, duration_seconds: float, artifacts: Sequence[str]
    ) -> None:
        method = getattr(self.database, "record_stage_checkpoint", None)
        if method:
            self._invoke(
                method,
                (
                    (
                        (job_id, stage),
                        {"duration_seconds": duration_seconds, "details": {"artifacts": list(artifacts)}},
                    ),
                    ((job_id, stage, duration_seconds), {}),
                ),
            )


class ArtifactStore:
    def __init__(
        self,
        data_root: Path,
        job_dir: Path,
        job_id: str,
        database: DatabaseFacade,
        tick: Callable[[], None],
    ) -> None:
        self.data_root = data_root.resolve()
        self.job_dir = job_dir.resolve()
        self.job_id = job_id
        self.database = database
        self.tick = tick
        self.manifest_path = self.job_dir / "checksums.json"
        self.records: dict[str, dict[str, Any]] = {}
        if self.manifest_path.exists():
            try:
                value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if value.get("schema_version") == SCHEMA_VERSION and isinstance(value.get("artifacts"), dict):
                    self.records = value["artifacts"]
            except (OSError, UnicodeError, json.JSONDecodeError):
                self.records = {}

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        if not _is_relative_to(resolved, self.data_root):
            raise PipelineError("Artifact path escaped the persistent data directory")
        return resolved.relative_to(self.data_root).as_posix()

    def path_for(self, key: str) -> Path | None:
        record = self.records.get(key)
        if not record or not isinstance(record.get("path"), str):
            return None
        path = (self.data_root / record["path"]).resolve()
        return path if _is_relative_to(path, self.data_root) else None
    def dependency(self, path: Path, **config: Any) -> dict[str, Any]:
        relative = self._relative(path)
        for key, record in self.records.items():
            if record.get("path") == relative:
                return {
                    "artifact_key": key,
                    "sha256": record.get("sha256"),
                    **config,
                }
        raise PipelineError("Pipeline input is not a registered artifact")


    def valid(
        self,
        key: str,
        path: Path | None = None,
        validator: Callable[[Path], bool] | None = None,
        inputs: Mapping[str, Any] | None = None,
    ) -> bool:
        record = self.records.get(key)
        candidate = path or self.path_for(key)
        if not record or candidate is None or not candidate.is_file():
            return False
        try:
            if record.get("path") != self._relative(candidate):
                return False
            if inputs is not None and record.get("inputs") != dict(inputs):
                return False
            if int(record.get("size_bytes", -1)) != candidate.stat().st_size:
                return False
            if sha256_file(candidate, self.tick) != record.get("sha256"):
                return False
            return validator(candidate) if validator else candidate.stat().st_size > 0
        except (OSError, TypeError, ValueError):
            return False

    def adopt(
        self,
        key: str,
        path: Path,
        mime_type: str | None = None,
        inputs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.is_file() or path.stat().st_size <= 0:
            raise PipelineError(f"Artifact {key} is empty or missing")
        record = {
            "path": self._relative(path),
            "sha256": sha256_file(path, self.tick),
            "size_bytes": path.stat().st_size,
            "mime_type": mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "inputs": dict(inputs or {}),
        }
        self.records[key] = record
        self._save()
        database_record = dict(record)
        database_record["path"] = str(path.resolve())
        self.database.artifact(self.job_id, key, database_record)
        return record

    def commit(
        self,
        key: str,
        temporary: Path,
        destination: Path,
        validator: Callable[[Path], bool] | None = None,
        mime_type: str | None = None,
        inputs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if validator and not validator(temporary):
            raise PipelineError(f"Generated artifact {key} failed validation")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
        return self.adopt(key, destination, mime_type, inputs)

    def _save(self) -> None:
        atomic_write_json(
            self.manifest_path,
            {"schema_version": SCHEMA_VERSION, "updated_at": utc_now(), "artifacts": self.records},
        )

    def register_manifest(self) -> None:
        if not self.manifest_path.is_file():
            self._save()
        record = {
            "path": self._relative(self.manifest_path),
            "sha256": sha256_file(self.manifest_path, self.tick),
            "size_bytes": self.manifest_path.stat().st_size,
            "mime_type": "application/json",
        }
        database_record = dict(record)
        database_record["path"] = str(self.manifest_path.resolve())
        self.database.artifact(self.job_id, "checksums", database_record)


class Pipeline:
    def __init__(
        self,
        database: Any,
        data_root: str | Path = "/data",
        models_dir: str | Path = "/models",
        deepfilter_cache: str | Path = "/cache/deepfilter",
        device: str = "cuda",
        compute_type: str | None = None,
        heartbeat: Callable[[str | None], None] | None = None,
        worker_id: str | None = None,
        poll_seconds: float = 0.5,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.db = DatabaseFacade(database, worker_id)
        self.data_root = Path(data_root).resolve()
        self.models_dir = Path(models_dir).resolve()
        self.deepfilter_cache = Path(deepfilter_cache).resolve()
        self.device = device.strip().lower()
        if self.device not in {"cuda", "cpu"}:
            raise PipelineError("ASR device must be cuda or cpu")
        self.compute_type = compute_type or ("int8_float16" if self.device == "cuda" else "int8")
        self.heartbeat_callback = heartbeat
        self.should_stop = should_stop
        self.poll_seconds = poll_seconds
        self.job_id = ""
        self.job: Mapping[str, Any] = {}
        self.job_dir = self.data_root
        self.store: ArtifactStore
        self.stage_timings: dict[str, float] = {}
        self.log_path = self.data_root / "pipeline.log"

    def run(self, job: Mapping[str, Any]) -> dict[str, Any] | None:
        self.job = job
        self.job_id = str(job.get("id") or job.get("job_id") or "").strip()
        if not self.job_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.job_id):
            raise PipelineError("Job has no safe identifier")
        configured_dir = job.get("artifact_dir") or job.get("job_dir")
        self.job_dir = (
            Path(str(configured_dir)).resolve()
            if configured_dir
            else (self.data_root / "jobs" / self.job_id).resolve()
        )
        if not _is_relative_to(self.job_dir, self.data_root):
            raise PipelineError("Job directory escaped the persistent data directory")
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.job_dir / "pipeline.log"
        raw_timings = job.get("stage_timings") or {}
        if isinstance(raw_timings, str):
            try:
                raw_timings = json.loads(raw_timings)
            except json.JSONDecodeError:
                raw_timings = {}
        self.stage_timings = dict(raw_timings) if isinstance(raw_timings, Mapping) else {}
        self.store = ArtifactStore(self.data_root, self.job_dir, self.job_id, self.db, self._tick)

        profile = resolve_profile(str(job.get("profile") or "standard"))
        requested_language = str(job.get("language") or job.get("requested_language") or "").strip() or None
        try:
            self._log("pipeline started", profile=profile.name, device=self.device)
            self._check_canceled()
            if self._is_url_job():
                with self._stage("acquiring"):
                    source, source_info = self._acquire_source()
            else:
                # Uploading is completed atomically by the web process before queueing.
                source, source_info = self._acquire_source()
            with self._stage("probing"):
                probe = self._probe(source)
            expected_duration = _probe_duration(probe)
            with self._stage("preparing"):
                prepared = self._prepare_wav(source, "prepared_48k", 48_000)
                baseline_input = self._prepare_wav(prepared, "baseline_whisper_16k", 16_000)

            if profile.enhance:
                with self._stage("enhancing"):
                    enhanced = self._enhance(prepared)
                    primary_input = self._prepare_wav(enhanced, "enhanced_whisper_16k", 16_000)
            else:
                primary_input = baseline_input

            with self._stage("transcribing"):
                primary_repository, primary_revision = model_identity(profile.model)
                primary = self._transcribe(
                    primary_input,
                    prefix="primary",
                    model=profile.model,
                    repository=primary_repository,
                    revision=primary_revision,
                    beam_size=profile.beam_size,
                    language=requested_language,
                    expected_duration=expected_duration,
                )
                baseline = None
                if profile.baseline:
                    baseline = self._transcribe(
                        baseline_input,
                        prefix="baseline",
                        model="large-v3",
                        repository=LARGE_V3_REPOSITORY,
                        revision=LARGE_V3_REVISION,
                        beam_size=5,
                        language=requested_language,
                        expected_duration=expected_duration,
                    )

            with self._stage("validating"):
                self._check_canceled()
                validate_transcript_outputs(
                    self.job_dir / "transcripts" / "transcript.txt",
                    self.job_dir / "transcripts" / "segments.jsonl",
                    self.job_dir / "transcripts" / "primary_asr_metadata.json",
                    expected_duration,
                )
            metadata = self._finalize(
                profile,
                requested_language,
                source,
                source_info,
                probe,
                primary,
                baseline,
            )
            self.db.update(
                self.job_id,
                status="completed",
                stage="completed",
                progress=100,
                error=None,
                stage_timings=self.stage_timings,
                completed_at=utc_now(),
                metadata=metadata,
            )
            return metadata
        except WorkerStopping:
            self._log("pipeline interrupted for worker shutdown")
            self._preserve_failure_log()
            return None

        except JobCanceled:
            self._log("pipeline canceled")
            self._preserve_failure_log()
            self.db.update(
                self.job_id,
                status="canceled",
                stage="canceled",
                error=None,
                stage_timings=self.stage_timings,
                completed_at=utc_now(),
            )
            return None
        except Exception as exc:
            error = _safe_error(exc)
            self._log("pipeline failed", error=error)
            self._preserve_failure_log()
            self.db.update(
                self.job_id,
                status="failed",
                stage="failed",
                error=error,
                stage_timings=self.stage_timings,
                completed_at=utc_now(),
            )
            return None

    def _is_url_job(self) -> bool:
        kind = str(self.job.get("source_type") or self.job.get("source_kind") or "").lower()
        return kind == "url" or bool(self.job.get("url") or self.job.get("source_url"))

    @contextmanager
    def _stage(self, stage: str) -> Iterable[None]:
        started = time.monotonic()
        artifacts_before = set(self.store.records)
        self.db.update(self.job_id, status="running", stage=stage, stage_timings=self.stage_timings)
        self._log("stage started", stage=stage)
        self._tick()
        try:
            yield
            elapsed = round(time.monotonic() - started, 3)
            self.stage_timings[stage] = round(float(self.stage_timings.get(stage, 0.0)) + elapsed, 3)
            completed_artifacts = sorted(set(self.store.records) - artifacts_before)
            self.db.checkpoint(self.job_id, stage, elapsed, completed_artifacts)
            self.db.update(self.job_id, stage=stage, stage_timings=self.stage_timings)
            self._log("stage completed", stage=stage, elapsed_seconds=elapsed)
        except Exception:
            elapsed = round(time.monotonic() - started, 3)
            self.stage_timings[stage] = round(float(self.stage_timings.get(stage, 0.0)) + elapsed, 3)
            raise

    def _tick(self) -> None:
        self.db.touch(self.job_id)
        if self.heartbeat_callback:
            self.heartbeat_callback(self.job_id or None)

    def _check_canceled(self) -> None:
        if self.db.canceled(self.job_id):
            raise JobCanceled("Job canceled")

    def _log(self, message: str, **fields: Any) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": utc_now(), "message": _safe_error(message)}
        record.update({key: _safe_error(value) for key, value in fields.items() if value is not None})
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _acquire_source(self) -> tuple[Path, dict[str, Any]]:
        existing = self.store.path_for("source")
        info_existing = self.store.path_for("source_info")
        if (
            existing
            and info_existing
            and self.store.valid("source", existing)
            and self.store.valid("source_info", info_existing, validate_json_file)
        ):
            info = json.loads(info_existing.read_text(encoding="utf-8"))
            return existing, info

        source_dir = self.job_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        if not self._is_url_job():
            raw_path = self.job.get("source_path") or self.job.get("upload_path") or self.job.get("input_path")
            if not raw_path:
                raise PipelineError("Uploaded job has no source path")
            source = Path(str(raw_path))
            if not source.is_absolute():
                source = self.data_root / source
            source = source.resolve()
            if not _is_relative_to(source, self.data_root) or not source.is_file() or source.stat().st_size <= 0:
                raise PipelineError("Uploaded source is missing or outside persistent storage")
            self.store.adopt("source", source)
            upload_info = {
                "source_type": "upload",
                "original_filename": self.job.get("original_filename") or self.job.get("source_name") or source.name,
                "size_bytes": source.stat().st_size,
            }
            info_path = source_dir / "source.info.json"
            atomic_write_json(info_path, upload_info)
            self.store.adopt("source_info", info_path, "application/json")
            return source, upload_info

        raw_url = str(self.job.get("url") or self.job.get("source_url") or "").strip()
        raw_url = validate_public_url(raw_url, resolve_dns=True)
        safe_url = sanitize_source_url(raw_url)
        for candidate in source_dir.glob("source.*"):
            if candidate.is_file() and _is_relative_to(candidate.resolve(), source_dir.resolve()):
                candidate.unlink()
        command = [
            "yt-dlp",
            "--no-playlist",
            "--no-progress",
            "--newline",
            "--write-info-json",
            "--force-overwrites",
            "-f",
            "bestaudio/best",
            "-o",
            str(source_dir / "source.%(ext)s"),
            raw_url,
        ]
        self._execute(command, timeout=_env_int("ASR_ACQUIRE_TIMEOUT_SECONDS", 7200))
        media = [
            path
            for path in source_dir.glob("source.*")
            if path.is_file()
            and path.suffix.lower() not in {".json", ".part", ".ytdl"}
            and not path.name.endswith(".info.json")
        ]
        if not media:
            raise PipelineError("yt-dlp completed without a source media file")
        source = max(media, key=lambda path: path.stat().st_mtime_ns)
        info_path = source_dir / "source.info.json"
        if not validate_json_file(info_path):
            raise PipelineError("yt-dlp completed without a valid info JSON file")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info.setdefault("requested_url", safe_url)
        self.store.adopt("source", source)
        self.store.adopt("source_info", info_path, "application/json")
        return source, info

    def _probe(self, source: Path) -> dict[str, Any]:
        destination = self.job_dir / "source" / "probe.json"
        inputs = self.store.dependency(source, operation="ffprobe")
        if self.store.valid("probe", destination, validate_json_file, inputs):
            return json.loads(destination.read_text(encoding="utf-8"))
        result = self._execute(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(source),
            ],
            timeout=_env_int("ASR_PROBE_TIMEOUT_SECONDS", 300),
        )
        try:
            probe = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PipelineError("ffprobe returned invalid media information") from exc
        if not probe.get("streams") or _probe_duration(probe) <= 0:
            raise PipelineError("Source media has no usable audio stream")
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        atomic_write_json(temporary, probe)
        self.store.commit("probe", temporary, destination, validate_json_file, "application/json", inputs)
        return probe

    def _prepare_wav(self, source: Path, key: str, sample_rate: int) -> Path:
        destination = self.job_dir / "audio" / f"{key}.wav"
        validator = lambda path: validate_wav(path, sample_rate)
        inputs = self.store.dependency(source, operation="pcm_s16le", sample_rate=sample_rate, channels=1)
        if self.store.valid(key, destination, validator, inputs):
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}.{os.getpid()}.part.wav")
        temporary.unlink(missing_ok=True)
        self._execute(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-sample_fmt",
                "s16",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            timeout=_env_int("ASR_FFMPEG_TIMEOUT_SECONDS", 7200),
        )
        return_path = destination
        self.store.commit(key, temporary, destination, validator, "audio/wav", inputs)
        return return_path

    def _enhance(self, prepared: Path) -> Path:
        destination = self.job_dir / "audio" / "enhanced_48k.wav"
        validator = lambda path: validate_wav(path, 48_000)
        inputs = self.store.dependency(prepared, operation="DeepFilterNet3", chunk_seconds=60, overlap_seconds=0.5, device=self.device)
        if self.store.valid("enhanced_48k", destination, validator, inputs):
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}.{os.getpid()}.part.wav")
        temporary.unlink(missing_ok=True)
        self._execute(
            [
                sys.executable,
                "-m",
                "reclip_asr.enhance_cli",
                "--input",
                str(prepared),
                "--output",
                str(temporary),
                "--device",
                self.device,
                "--chunk-seconds",
                "60",
                "--overlap-seconds",
                "0.5",
                "--cache-dir",
                str(self.deepfilter_cache),
            ],
            timeout=_env_int("ASR_ENHANCE_TIMEOUT_SECONDS", 86400),
        )
        self.store.commit("enhanced_48k", temporary, destination, validator, "audio/wav", inputs)
        return destination

    def _transcribe(
        self,
        source: Path,
        prefix: str,
        model: str,
        repository: str,
        revision: str,
        beam_size: int,
        language: str | None,
        expected_duration: float,
    ) -> dict[str, Any]:
        transcript_dir = self.job_dir / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        outputs = {
            "transcript": transcript_dir / ("transcript.txt" if prefix == "primary" else f"{prefix}_transcript.txt"),
            "segments": transcript_dir / ("segments.jsonl" if prefix == "primary" else f"{prefix}_segments.jsonl"),
            "asr_metadata": transcript_dir
            / ("primary_asr_metadata.json" if prefix == "primary" else f"{prefix}_asr_metadata.json"),
        }
        keys = {name: f"{prefix}_{name}" for name in outputs}
        inputs = self.store.dependency(
            source,
            operation="faster-whisper",
            model_repository=repository,
            model_revision=revision,
            beam_size=beam_size,
            language=language,
            device=self.device,
            compute_type=self.compute_type,
            vad_min_silence_ms=500,
        )
        if all(self.store.valid(keys[name], path, inputs=inputs) for name, path in outputs.items()):
            validated = validate_transcript_outputs(
                outputs["transcript"], outputs["segments"], outputs["asr_metadata"], expected_duration
            )
            return validated

        staging = Path(tempfile.mkdtemp(prefix=f".{prefix}.", dir=transcript_dir))
        try:
            command = [
                sys.executable,
                "-m",
                "reclip_asr.transcribe_cli",
                "--input",
                str(source),
                "--output-dir",
                str(staging),
                "--basename",
                prefix,
                "--model",
                model,
                "--model-repository",
                repository,
                "--model-revision",
                revision,
                "--models-dir",
                str(self.models_dir),
                "--device",
                self.device,
                "--compute-type",
                self.compute_type,
                "--beam-size",
                str(beam_size),
                "--min-silence-ms",
                "500",
            ]
            if language:
                command += ["--language", language]
            self._execute(command, timeout=_env_int("ASR_TRANSCRIBE_TIMEOUT_SECONDS", 172800))
            staged = {
                "transcript": staging / f"{prefix}_transcript.txt",
                "segments": staging / f"{prefix}_segments.jsonl",
                "asr_metadata": staging / f"{prefix}_asr_metadata.json",
            }
            validated = validate_transcript_outputs(
                staged["transcript"], staged["segments"], staged["asr_metadata"], expected_duration
            )
            mime_types = {
                "transcript": "text/plain; charset=utf-8",
                "segments": "application/x-ndjson",
                "asr_metadata": "application/json",
            }
            for name, path in staged.items():
                self.store.commit(keys[name], path, outputs[name], mime_type=mime_types[name], inputs=inputs)
            return validated
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _finalize(
        self,
        profile: TranscriptionProfile,
        requested_language: str | None,
        source: Path,
        source_info: Mapping[str, Any],
        probe: Mapping[str, Any],
        primary: Mapping[str, Any],
        baseline: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        primary_metadata = dict(primary["metadata"])
        source_record = self.store.records["source"]
        warnings: list[str] = []
        if profile.baseline:
            warnings.append(
                "The enhanced transcript is primary; the independent baseline is retained and is not automatically merged."
            )
        source_url = self.job.get("url") or self.job.get("source_url")
        source_identity = {
            "type": "url" if self._is_url_job() else "upload",
            "filename": source.name,
            "original_filename": self.job.get("original_filename") or self.job.get("source_name"),
            "url": sanitize_source_url(str(source_url)) if source_url else None,
            "extractor": source_info.get("extractor_key") or source_info.get("extractor"),
            "media_id": source_info.get("id"),
            "title": source_info.get("title"),
            "checksum_sha256": source_record["sha256"],
            "size_bytes": source_record["size_bytes"],
        }
        primary_repository, primary_revision = model_identity(profile.model)
        metadata: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "job_id": self.job_id,
            "created_at": self.job.get("created_at"),
            "completed_at": utc_now(),
            "source": source_identity,
            "profile": profile.name,
            "requested_language": requested_language,
            "detected_language": primary_metadata.get("detected_language"),
            "language_probability": primary_metadata.get("language_probability"),
            "duration_seconds": _probe_duration(probe),
            "duration_after_vad_seconds": primary_metadata.get("duration_after_vad_seconds"),
            "segment_count": primary_metadata.get("segment_count", primary_metadata.get("segments")),
            "decoding": {
                "beam_size": profile.beam_size,
                "vad_filter": True,
                "min_silence_duration_ms": 500,
                "condition_on_previous_text": True,
                "device": self.device,
                "compute_type": self.compute_type,
            },
            "models": {
                "primary": {
                    "name": profile.model,
                    "repository": primary_repository,
                    "revision": primary_revision,
                    "enhanced_input": profile.enhance,
                }
            },
            "stage_timings_seconds": self.stage_timings,
            "tool_versions": _tool_versions(),
            "artifacts": dict(self.store.records),
            "warnings": warnings,
        }
        if baseline:
            baseline_metadata = baseline["metadata"]
            metadata["models"]["baseline"] = {
                "name": "large-v3",
                "repository": LARGE_V3_REPOSITORY,
                "revision": LARGE_V3_REVISION,
                "enhanced_input": False,
                "detected_language": baseline_metadata.get("detected_language"),
                "segment_count": baseline_metadata.get("segment_count", baseline_metadata.get("segments")),
            }
        if profile.enhance:
            metadata["models"]["enhancement"] = {
                "name": "DeepFilterNet3",
                "package": "deepfilternet==0.5.6",
                "repository": "Rikorose/DeepFilterNet",
                "revision": "978576aa8400552a4ce9730838c635aa30db5e61",
                "archive_sha256": "49c52edc8947ae1f9bf50d81530beaf3a2c3245aeaf34b6f31ff535cd22284d2",
                "chunk_seconds": 60,
                "overlap_seconds": 0.5,
            }

        self._log("pipeline outputs validated", segments=metadata["segment_count"])
        self.store.adopt("log", self.log_path, "application/x-ndjson")
        metadata["artifacts"] = dict(self.store.records)
        destination = self.job_dir / "metadata.json"
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        atomic_write_json(temporary, metadata)
        self.store.commit("metadata", temporary, destination, validate_json_file, "application/json")
        self.store.register_manifest()
        return metadata

    def _preserve_failure_log(self) -> None:
        try:
            if self.log_path.is_file() and self.log_path.stat().st_size:
                self.store.adopt("log", self.log_path, "application/x-ndjson")
                self.store.register_manifest()
        except Exception:
            pass

    def _execute(self, command: Sequence[str], timeout: int) -> ProcessResult:
        self._check_canceled()
        started = time.monotonic()
        environment = os.environ.copy()
        environment.update(
            {
                "HF_HOME": str(self.models_dir / "huggingface"),
                "XDG_CACHE_HOME": str(self.deepfilter_cache.parent),
                "DF_CACHE_DIR": str(self.deepfilter_cache),
            }
        )
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                [str(item) for item in command],
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
                start_new_session=(os.name != "nt"),
            )
            try:
                while process.poll() is None:
                    if self.should_stop and self.should_stop():
                        self._terminate(process)
                        raise WorkerStopping("Worker is stopping")
                    if time.monotonic() - started > timeout:
                        self._terminate(process)
                        raise PipelineError("Pipeline subprocess timed out")
                    if self.db.canceled(self.job_id):
                        self._terminate(process)
                        raise JobCanceled("Job canceled")
                    self._tick()
                    time.sleep(self.poll_seconds)
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read().decode("utf-8", errors="replace")
                stderr = stderr_file.read().decode("utf-8", errors="replace")
                result = ProcessResult(process.returncode or 0, stdout, stderr)
                if result.returncode != 0:
                    detail = _safe_error(stderr or stdout)
                    raise PipelineError(f"Subprocess failed ({result.returncode}): {detail}")
                return result
            except BaseException:
                if process.poll() is None:
                    self._terminate(process)
                raise

    @staticmethod
    def _terminate(process: subprocess.Popen[Any]) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass


def _probe_duration(probe: Mapping[str, Any]) -> float:
    values = [probe.get("format", {}).get("duration")]
    values.extend(stream.get("duration") for stream in probe.get("streams", []) if isinstance(stream, Mapping))
    for value in values:
        try:
            duration = float(value)
            if duration > 0:
                return duration
        except (TypeError, ValueError):
            continue
    return 0.0


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _command_version(command: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=10, check=False
        )
        line = (result.stdout or result.stderr).splitlines()
        return _safe_error(line[0], 500) if line else None
    except (OSError, subprocess.SubprocessError):
        return None


def _tool_versions() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "ffmpeg": _command_version(["ffmpeg", "-version"]),
        "ffprobe": _command_version(["ffprobe", "-version"]),
        "yt_dlp": _package_version("yt-dlp") or _command_version(["yt-dlp", "--version"]),
        "yt_dlp_ejs": _package_version("yt-dlp-ejs"),
        "faster_whisper": _package_version("faster-whisper"),
        "ctranslate2": _package_version("ctranslate2"),
        "deepfilternet": _package_version("deepfilternet"),
        "deepfilterlib": _package_version("deepfilterlib"),
        "numpy": _package_version("numpy"),
        "torch": _package_version("torch"),
        "torchaudio": _package_version("torchaudio"),
        "deno": _command_version(["deno", "--version"]),
        "cuda": os.environ.get("CUDA_VERSION"),
        "cudnn": os.environ.get("CUDNN_VERSION"),
        "gpu_driver": os.environ.get("NVIDIA_DRIVER_VERSION"),
        "container_image": os.environ.get("RECLIP_WORKER_IMAGE_ID"),
    }
