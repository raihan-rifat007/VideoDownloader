"""SQLite-backed durable queue shared by the ReClip web and ASR worker.

The queue intentionally exposes a small, synchronous API.  Every mutating queue
operation that depends on current state uses ``BEGIN IMMEDIATE`` so independent
web and worker processes cannot claim, cancel, retry, or delete the same job at
the same time.  A claimed job keeps its current pipeline stage when its lease is
stale; the replacement worker can use stage checkpoints plus verified artifacts
to resume safely.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .profiles import get_profile
from .utils import (
    ensure_path_within,
    job_directory,
    normalize_language,
    normalize_uuid,
    sha256_file,
    validate_artifact_key,
)


PIPELINE_STAGES = (
    "queued",
    "acquiring",
    "uploading",
    "probing",
    "preparing",
    "enhancing",
    "transcribing",
    "validating",
)
ACTIVE_STAGES = PIPELINE_STAGES[1:]
# ``uploading`` is owned by the HTTP process. A worker must never reclaim a
# partially copied request body as though it were a pipeline checkpoint.
WORKER_ACTIVE_STAGES = tuple(stage for stage in ACTIVE_STAGES if stage != "uploading")
TERMINAL_STATUSES = ("completed", "failed", "canceled")
ALL_STATUSES = frozenset((*PIPELINE_STAGES, *TERMINAL_STATUSES))


class DatabaseError(RuntimeError):
    """Base error for durable queue operations."""


class JobNotFound(DatabaseError):
    pass


class JobConflict(DatabaseError):
    pass


class InvalidJobState(DatabaseError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _cutoff(seconds: float) -> str:
    value = datetime.now(timezone.utc) - timedelta(seconds=max(0.0, float(seconds)))
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class Database:
    """Durable transcription queue and artifact registry.

    ``data_dir`` is the only filesystem root accepted by artifact registration.
    Paths returned from the database are absolute for worker convenience, while
    SQLite stores paths relative to that root so the named Docker volume remains
    portable between containers.
    """

    def __init__(self, db_path: str | os.PathLike[str], data_dir: str | os.PathLike[str]):
        self.db_path = Path(db_path).resolve()
        self.data_dir = Path(data_dir).resolve()
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "Database":
        data_dir = os.environ.get("DATA_DIR", "/data")
        db_path = os.environ.get("ASR_DB_PATH", str(Path(data_dir) / "reclip.sqlite3"))
        return cls(db_path, data_dir)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            (self.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS transcription_jobs (
                        id TEXT PRIMARY KEY,
                        source_type TEXT NOT NULL,
                        source_url TEXT,
                        source_name TEXT NOT NULL,
                        source_path TEXT,
                        profile TEXT NOT NULL,
                        requested_language TEXT,
                        status TEXT NOT NULL,
                        progress REAL NOT NULL DEFAULT 0,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        job_dir TEXT NOT NULL,
                        claimed_by TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        heartbeat_at TEXT,
                        retry_of TEXT REFERENCES transcription_jobs(id) ON DELETE SET NULL,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE INDEX IF NOT EXISTS idx_transcription_jobs_queue
                        ON transcription_jobs(status, created_at, id);
                    CREATE INDEX IF NOT EXISTS idx_transcription_jobs_heartbeat
                        ON transcription_jobs(status, heartbeat_at);

                    CREATE TABLE IF NOT EXISTS transcription_artifacts (
                        job_id TEXT NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
                        artifact_key TEXT NOT NULL,
                        relative_path TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        media_type TEXT,
                        size_bytes INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (job_id, artifact_key)
                    );

                    CREATE TABLE IF NOT EXISTS transcription_stage_checkpoints (
                        job_id TEXT NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
                        stage TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        duration_seconds REAL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        PRIMARY KEY (job_id, stage)
                    );

                    CREATE TABLE IF NOT EXISTS asr_workers (
                        worker_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        gpu_available INTEGER,
                        gpu_name TEXT,
                        current_job_id TEXT REFERENCES transcription_jobs(id) ON DELETE SET NULL,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        started_at TEXT NOT NULL,
                        heartbeat_at TEXT NOT NULL
                    );
                    """
                )
            self._initialized = True

    def _ready(self) -> None:
        self.initialize()

    def _job_from_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["progress"] = float(result["progress"] or 0)
        result["metadata"] = _decode(result.pop("metadata_json", None), {})
        return result

    def _artifact_from_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["key"] = result.pop("artifact_key")
        result["size"] = result.pop("size_bytes")
        result["path"] = str((self.data_dir / result["relative_path"]).resolve())
        return result

    def _worker_from_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        if result["gpu_available"] is not None:
            result["gpu_available"] = bool(result["gpu_available"])
        result["details"] = _decode(result.pop("details_json", None), {})
        return result

    def create_job(
        self,
        source_type: str,
        profile: str,
        requested_language: str | None = None,
        source_url: str | None = None,
        source_name: str | None = None,
        source_path: str | None = None,
        status: str = "queued",
        retry_of: str | None = None,
        job_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a UUID job and its private directory.

        Upload handlers may create a job in ``uploading`` and transition it to
        ``queued`` only after the final source file has been atomically renamed.
        """

        self._ready()
        if source_type not in {"url", "upload"}:
            raise ValueError("source_type must be 'url' or 'upload'")
        profile_name = get_profile(profile).name
        language = normalize_language(requested_language)
        if status not in ALL_STATUSES:
            raise ValueError(f"Unknown job status: {status}")
        canonical_id = normalize_uuid(job_id)
        directory = job_directory(self.data_dir, canonical_id)
        directory.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        display_name = str(source_name or source_url or "uploaded media")[:1000]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transcription_jobs (
                    id, source_type, source_url, source_name, source_path, profile,
                    requested_language, status, job_dir, created_at, updated_at,
                    retry_of, metadata_json, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical_id,
                    source_type,
                    source_url,
                    display_name,
                    source_path,
                    profile_name,
                    language,
                    status,
                    str(directory),
                    now,
                    now,
                    retry_of,
                    _json(metadata or {}),
                    now if status in ACTIVE_STAGES else None,
                ),
            )
        return self.get_job(canonical_id)

    def get_job(self, job_id: str, include_queue_position: bool = True) -> dict[str, Any]:
        self._ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM transcription_jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
            job = self._job_from_row(row)
            if job is None:
                raise JobNotFound(f"Transcription job {job_id} was not found")
            if include_queue_position:
                job["queue_position"] = self._queue_position(connection, job)
            return job

    def _queue_position(self, connection: sqlite3.Connection, job: dict[str, Any]) -> int | None:
        if job["status"] != "queued":
            return None
        row = connection.execute(
            """
            SELECT COUNT(*) AS position
              FROM transcription_jobs
             WHERE status = 'queued'
               AND (created_at < ? OR (created_at = ? AND id <= ?))
            """,
            (job["created_at"], job["created_at"], job["id"]),
        ).fetchone()
        return int(row["position"])

    def list_jobs(self, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        self._ready()
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM transcription_jobs
                   ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            total = int(
                connection.execute("SELECT COUNT(*) FROM transcription_jobs").fetchone()[0]
            )
            jobs = []
            for row in rows:
                job = self._job_from_row(row)
                assert job is not None
                job["queue_position"] = self._queue_position(connection, job)
                jobs.append(job)
            return jobs, total

    def claim_next_job(self, worker_id: str, stale_after_seconds: float = 30) -> dict[str, Any] | None:
        """Atomically claim one job while enforcing global serial execution.

        A fresh active lease prevents every worker from claiming another job.  A
        stale active job is reclaimed before queued work and keeps its stage so
        checkpoint/artifact validation can resume it.  Fresh queued work starts
        in ``acquiring`` for URLs and ``probing`` for completed uploads.
        """

        self._ready()
        worker_id = str(worker_id).strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        cutoff = _cutoff(stale_after_seconds)
        now = utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            fresh = connection.execute(
                f"""SELECT id FROM transcription_jobs
                    WHERE status IN ({','.join('?' for _ in WORKER_ACTIVE_STAGES)})
                      AND heartbeat_at IS NOT NULL AND heartbeat_at >= ?
                    LIMIT 1""",
                (*WORKER_ACTIVE_STAGES, cutoff),
            ).fetchone()
            if fresh is not None:
                connection.commit()
                return None

            # A canceled job whose owner disappeared is safe to terminate before
            # selecting another candidate.
            connection.execute(
                f"""UPDATE transcription_jobs
                       SET status='canceled', progress=0, completed_at=?, updated_at=?,
                           claimed_by=NULL, heartbeat_at=?
                     WHERE status IN ({','.join('?' for _ in WORKER_ACTIVE_STAGES)})
                       AND cancel_requested=1
                       AND (heartbeat_at IS NULL OR heartbeat_at < ?)""",
                (now, now, now, *WORKER_ACTIVE_STAGES, cutoff),
            )

            candidate = connection.execute(
                f"""SELECT * FROM transcription_jobs
                    WHERE status IN ({','.join('?' for _ in WORKER_ACTIVE_STAGES)})
                      AND cancel_requested=0
                      AND (heartbeat_at IS NULL OR heartbeat_at < ?)
                    ORDER BY updated_at ASC, id ASC LIMIT 1""",
                (*WORKER_ACTIVE_STAGES, cutoff),
            ).fetchone()
            reclaimed = candidate is not None
            if candidate is None:
                candidate = connection.execute(
                    """SELECT * FROM transcription_jobs
                        WHERE status='queued' AND cancel_requested=0
                        ORDER BY created_at ASC, id ASC LIMIT 1"""
                ).fetchone()
            if candidate is None:
                connection.commit()
                return None

            new_status = candidate["status"]
            if not reclaimed:
                new_status = "acquiring" if candidate["source_type"] == "url" else "probing"
            connection.execute(
                """UPDATE transcription_jobs
                      SET status=?, claimed_by=?, heartbeat_at=?, updated_at=?,
                          started_at=COALESCE(started_at, ?)
                    WHERE id=?""",
                (new_status, worker_id, now, now, now, candidate["id"]),
            )
            connection.commit()
            return self.get_job(candidate["id"])
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def heartbeat_job(self, job_id: str, worker_id: str) -> bool:
        self._ready()
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                f"""UPDATE transcription_jobs SET heartbeat_at=?, updated_at=?
                    WHERE id=? AND claimed_by=?
                      AND status IN ({','.join('?' for _ in ACTIVE_STAGES)})""",
                (now, now, str(job_id), str(worker_id), *ACTIVE_STAGES),
            )
            return cursor.rowcount == 1

    def transition_job(
        self,
        job_id: str,
        status: str,
        *,
        worker_id: str | None = None,
        progress: float | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a job to a pipeline/terminal status and optionally merge metadata."""

        self._ready()
        if status not in ALL_STATUSES:
            raise ValueError(f"Unknown job status: {status}")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transcription_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise JobNotFound(f"Transcription job {job_id} was not found")
            if worker_id is not None and row["claimed_by"] != str(worker_id):
                raise JobConflict("The job is leased by another worker")
            # Finalizing an upload and requesting cancellation can race in two
            # HTTP threads. Resolve that race while holding the same write lock.
            if row["status"] == "uploading" and status == "queued" and row["cancel_requested"]:
                status = "canceled"

            if row["status"] in TERMINAL_STATUSES and status != row["status"]:
                raise InvalidJobState(f"Cannot transition terminal job from {row['status']}")

            merged_metadata = _decode(row["metadata_json"], {})
            if metadata:
                merged_metadata.update(metadata)
            now = utc_now()
            terminal = status in TERMINAL_STATUSES
            next_progress = row["progress"] if progress is None else max(0.0, min(100.0, float(progress)))
            if status == "completed":
                next_progress = 100.0
            next_error = error
            if error is None and status != "failed":
                next_error = None
            connection.execute(
                """UPDATE transcription_jobs
                      SET status=?, progress=?, error=?, metadata_json=?, updated_at=?,
                          heartbeat_at=?, completed_at=?,
                          claimed_by=CASE WHEN ? THEN NULL ELSE claimed_by END
                    WHERE id=?""",
                (
                    status,
                    next_progress,
                    next_error,
                    _json(merged_metadata),
                    now,
                    now,
                    now if terminal else None,
                    int(terminal),
                    str(job_id),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_job(job_id)

    def update_progress(
        self, job_id: str, progress: float, worker_id: str | None = None
    ) -> dict[str, Any]:
        job = self.get_job(job_id, include_queue_position=False)
        return self.transition_job(
            job_id, job["status"], worker_id=worker_id, progress=progress
        )

    def merge_job_metadata(self, job_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        job = self.get_job(job_id, include_queue_position=False)
        return self.transition_job(job_id, job["status"], metadata=metadata)

    def set_source_path(self, job_id: str, source_path: str) -> dict[str, Any]:
        self._ready()
        job = self.get_job(job_id, include_queue_position=False)
        resolved = ensure_path_within(source_path, job["job_dir"])
        with self._connect() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET source_path=?, updated_at=? WHERE id=?",
                (str(resolved), utc_now(), str(job_id)),
            )
        return self.get_job(job_id)

    def recover_interrupted_uploads(self) -> int:
        """Terminate uploads left behind by a previous web process.

        The supported deployment has one Gunicorn process. This method is
        called exactly once when that process opens the queue, before it can
        create a new upload job.
        """

        self._ready()
        now = utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            canceled = connection.execute(
                """UPDATE transcription_jobs
                      SET status='canceled', updated_at=?, completed_at=?, heartbeat_at=?
                    WHERE status='uploading' AND cancel_requested=1""",
                (now, now, now),
            ).rowcount
            failed = connection.execute(
                """UPDATE transcription_jobs
                      SET status='failed', error='Upload was interrupted; retry if the source artifact completed, otherwise delete and upload again.',
                          updated_at=?, completed_at=?, heartbeat_at=?
                    WHERE status='uploading'""",
                (now, now, now),
            ).rowcount
            connection.commit()
            return int(canceled + failed)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        self._ready()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM transcription_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise JobNotFound(f"Transcription job {job_id} was not found")
            now = utc_now()
            if row["status"] == "queued":
                connection.execute(
                    """UPDATE transcription_jobs
                          SET status='canceled', cancel_requested=1, updated_at=?,
                              completed_at=?, heartbeat_at=? WHERE id=?""",
                    (now, now, now, str(job_id)),
                )
            elif row["status"] in ACTIVE_STAGES:
                connection.execute(
                    "UPDATE transcription_jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                    (now, str(job_id)),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_job(job_id)

    def is_cancel_requested(self, job_id: str) -> bool:
        return bool(self.get_job(job_id, include_queue_position=False)["cancel_requested"])

    def mark_canceled(self, job_id: str, worker_id: str | None = None) -> dict[str, Any]:
        return self.transition_job(job_id, "canceled", worker_id=worker_id)

    def mark_failed(
        self, job_id: str, error: str, worker_id: str | None = None
    ) -> dict[str, Any]:
        return self.transition_job(job_id, "failed", worker_id=worker_id, error=str(error)[:4000])

    def mark_completed(
        self, job_id: str, metadata: dict[str, Any] | None = None, worker_id: str | None = None
    ) -> dict[str, Any]:
        return self.transition_job(
            job_id, "completed", worker_id=worker_id, progress=100.0, metadata=metadata
        )

    def retry_job(self, job_id: str) -> dict[str, Any]:
        """Requeue a failed/canceled job in place, retaining verified checkpoints."""

        self._ready()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM transcription_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise JobNotFound(f"Transcription job {job_id} was not found")
            if row["status"] not in {"failed", "canceled"}:
                raise InvalidJobState("Only failed or canceled jobs can be retried")
            now = utc_now()
            connection.execute(
                """UPDATE transcription_jobs
                      SET status='queued', progress=0, cancel_requested=0, error=NULL,
                          claimed_by=NULL, heartbeat_at=NULL, completed_at=NULL,
                          updated_at=?, attempt=attempt+1 WHERE id=?""",
                (now, str(job_id)),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_job(job_id)

    def prepare_job_deletion(self, job_id: str) -> dict[str, Any]:
        """Stop a queued job atomically before its directory is removed."""

        self._ready()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transcription_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            job = self._job_from_row(row)
            if job is None:
                raise JobNotFound(f"Transcription job {job_id} was not found")
            if job["status"] in ACTIVE_STAGES:
                raise InvalidJobState("Cancel the running job and wait for it to stop before deleting")
            if job["status"] == "queued":
                now = utc_now()
                connection.execute(
                    """UPDATE transcription_jobs
                          SET status='canceled', cancel_requested=1, updated_at=?,
                              completed_at=?, heartbeat_at=? WHERE id=?""",
                    (now, now, now, str(job_id)),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        """Delete a stopped job record; the caller removes its exact job directory."""

        self._ready()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transcription_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            job = self._job_from_row(row)
            if job is None:
                raise JobNotFound(f"Transcription job {job_id} was not found")
            if job["status"] in ACTIVE_STAGES:
                raise InvalidJobState("Cancel the running job and wait for it to stop before deleting")
            connection.execute("DELETE FROM transcription_jobs WHERE id=?", (str(job_id),))
            connection.commit()
            return job
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register_artifact(
        self,
        job_id: str,
        key: str,
        path: str | os.PathLike[str],
        *,
        filename: str | None = None,
        media_type: str | None = None,
        sha256: str | None = None,
        size: int | None = None,
    ) -> dict[str, Any]:
        """Allowlist a completed file after constraining it to the job directory."""

        self._ready()
        artifact_key = validate_artifact_key(key)
        job = self.get_job(job_id, include_queue_position=False)
        resolved = ensure_path_within(path, job["job_dir"])
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        relative = resolved.relative_to(self.data_dir).as_posix()
        file_size = int(resolved.stat().st_size if size is None else size)
        digest = sha256 or sha256_file(resolved)
        guessed_type = media_type or mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO transcription_artifacts (
                       job_id, artifact_key, relative_path, filename, media_type,
                       size_bytes, sha256, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id, artifact_key) DO UPDATE SET
                       relative_path=excluded.relative_path,
                       filename=excluded.filename,
                       media_type=excluded.media_type,
                       size_bytes=excluded.size_bytes,
                       sha256=excluded.sha256,
                       created_at=excluded.created_at""",
                (
                    str(job_id),
                    artifact_key,
                    relative,
                    filename or resolved.name,
                    guessed_type,
                    file_size,
                    digest,
                    now,
                ),
            )
        return self.get_artifact(job_id, artifact_key)

    def get_artifact(self, job_id: str, key: str) -> dict[str, Any]:
        self._ready()
        artifact_key = validate_artifact_key(key)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM transcription_artifacts
                   WHERE job_id=? AND artifact_key=?""",
                (str(job_id), artifact_key),
            ).fetchone()
        artifact = self._artifact_from_row(row)
        if artifact is None:
            raise JobNotFound(f"Artifact {artifact_key} was not found")
        return artifact

    def list_artifacts(self, job_id: str) -> dict[str, dict[str, Any]]:
        self._ready()
        self.get_job(job_id, include_queue_position=False)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM transcription_artifacts
                   WHERE job_id=? ORDER BY artifact_key""",
                (str(job_id),),
            ).fetchall()
        artifacts: dict[str, dict[str, Any]] = {}
        for row in rows:
            artifact = self._artifact_from_row(row)
            assert artifact is not None
            artifacts[artifact["key"]] = artifact
        return artifacts

    def verify_artifact(self, job_id: str, key: str) -> bool:
        try:
            artifact = self.get_artifact(job_id, key)
            path = Path(artifact["path"])
            return (
                path.is_file()
                and path.stat().st_size == artifact["size"]
                and sha256_file(path) == artifact["sha256"]
            )
        except (JobNotFound, FileNotFoundError, OSError, ValueError):
            return False

    def record_stage_checkpoint(
        self,
        job_id: str,
        stage: str,
        *,
        duration_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a completed stage and its artifact keys for restart recovery."""

        self._ready()
        if stage not in ACTIVE_STAGES:
            raise ValueError(f"Unknown checkpoint stage: {stage}")
        self.get_job(job_id, include_queue_position=False)
        checkpoint_details = dict(details or {})
        checkpoint_details.setdefault("artifacts", sorted(self.list_artifacts(job_id)))
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO transcription_stage_checkpoints (
                       job_id, stage, completed_at, duration_seconds, details_json
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(job_id, stage) DO UPDATE SET
                       completed_at=excluded.completed_at,
                       duration_seconds=excluded.duration_seconds,
                       details_json=excluded.details_json""",
                (str(job_id), stage, now, duration_seconds, _json(checkpoint_details)),
            )
        return next(item for item in self.get_stage_checkpoints(job_id) if item["stage"] == stage)

    def get_stage_checkpoints(self, job_id: str) -> list[dict[str, Any]]:
        self._ready()
        self.get_job(job_id, include_queue_position=False)
        order = {stage: index for index, stage in enumerate(ACTIVE_STAGES)}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transcription_stage_checkpoints WHERE job_id=?",
                (str(job_id),),
            ).fetchall()
        checkpoints = []
        for row in rows:
            item = dict(row)
            item["details"] = _decode(item.pop("details_json", None), {})
            checkpoints.append(item)
        checkpoints.sort(key=lambda item: order.get(item["stage"], 999))
        return checkpoints

    def last_recoverable_stage(self, job_id: str) -> str | None:
        """Return the last contiguous checkpoint whose declared artifacts verify."""

        checkpoints = {item["stage"]: item for item in self.get_stage_checkpoints(job_id)}
        job = self.get_job(job_id, include_queue_position=False)
        profile = get_profile(job["profile"])
        stages: list[str] = []
        if job["source_type"] == "url":
            stages.append("acquiring")
        stages.extend(("probing", "preparing"))
        if profile.enhance:
            stages.append("enhancing")
        stages.extend(("transcribing", "validating"))
        last: str | None = None
        for stage in stages:
            checkpoint = checkpoints.get(stage)
            if checkpoint is None:
                break
            artifact_keys: Iterable[str] = checkpoint["details"].get("artifacts", [])
            if not all(self.verify_artifact(job_id, key) for key in artifact_keys):
                break
            last = stage
        return last

    def heartbeat_worker(
        self,
        worker_id: str,
        *,
        status: str = "idle",
        gpu_available: bool | None = None,
        gpu_name: str | None = None,
        current_job_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ready()
        worker_id = str(worker_id).strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO asr_workers (
                       worker_id, status, gpu_available, gpu_name, current_job_id,
                       details_json, started_at, heartbeat_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(worker_id) DO UPDATE SET
                       status=excluded.status,
                       gpu_available=excluded.gpu_available,
                       gpu_name=excluded.gpu_name,
                       current_job_id=excluded.current_job_id,
                       details_json=excluded.details_json,
                       heartbeat_at=excluded.heartbeat_at""",
                (
                    worker_id,
                    status,
                    None if gpu_available is None else int(gpu_available),
                    gpu_name,
                    current_job_id,
                    _json(details or {}),
                    now,
                    now,
                ),
            )
        worker = self.get_worker(worker_id)
        assert worker is not None
        return worker

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        self._ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM asr_workers WHERE worker_id=?", (str(worker_id),)
            ).fetchone()
        return self._worker_from_row(row)

    def latest_worker(self) -> dict[str, Any] | None:
        self._ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM asr_workers ORDER BY heartbeat_at DESC LIMIT 1"
            ).fetchone()
        return self._worker_from_row(row)

    def queue_counts(self) -> dict[str, int]:
        self._ready()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM transcription_jobs GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in ALL_STATUSES}
        counts.update({row["status"]: int(row["count"]) for row in rows})
        counts["active"] = sum(counts[stage] for stage in ACTIVE_STAGES)
        return counts

    def get_system_status(self, stale_after_seconds: float = 30) -> dict[str, Any]:
        worker = self.latest_worker()
        if worker is not None:
            worker["alive"] = worker["heartbeat_at"] >= _cutoff(stale_after_seconds)
        return {"worker": worker, "queue": self.queue_counts()}
