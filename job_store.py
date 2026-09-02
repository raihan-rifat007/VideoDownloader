"""Durable task state for resumable ReClip downloads."""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2


JOB_STATES = {
    "preparing",
    "downloading",
    "processing",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
    "interrupted",
    "deleting",
    "deleted",
}

JOB_FIELDS = {
    "state",
    "attempt_no",
    "resource_json",
    "progress_json",
    "last_progress_json",
    "error_code",
    "error_message",
    "final_relpath",
    "filename",
    "updated_at",
}

ACTIVE_JOB_STATES = frozenset({"preparing", "downloading", "processing", "cancelling"})
DOWNLOAD_ACTIVE_STATES = frozenset({"preparing", "downloading", "processing"})


def _encode_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode_json(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _encode_cursor(created_at: float, job_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at, "job_id": job_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[float, str] | None:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor or len(cursor) > 256:
        raise ValueError("Invalid cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        created_at = float(data["created_at"])
        job_id = data["job_id"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("Invalid cursor") from None
    if not isinstance(job_id, str) or len(job_id) != 32:
        raise ValueError("Invalid cursor")
    return created_at, job_id


class JobStore:
    """SQLite-backed state store with one short-lived connection per operation."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported jobs database schema: {version}")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_url TEXT,
                    title TEXT,
                    format_choice TEXT NOT NULL,
                    requested_format_id TEXT,
                    state TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    resource_json TEXT,
                    progress_json TEXT,
                    last_progress_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    final_relpath TEXT,
                    filename TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    attempt_no INTEGER NOT NULL,
                    runtime_epoch TEXT,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    exit_code INTEGER,
                    outcome TEXT NOT NULL,
                    PRIMARY KEY (job_id, attempt_no)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS service_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_listing
                    ON jobs (state, created_at DESC, job_id DESC)
                """
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        """Keep the public lifecycle API; connections are per-operation."""

    def insert_job(self, record: dict[str, Any]) -> None:
        required = {
            "job_id",
            "source_url",
            "title",
            "format_choice",
            "requested_format_id",
            "state",
            "attempt_no",
            "resource_json",
            "progress_json",
            "error_code",
            "error_message",
            "final_relpath",
            "filename",
            "created_at",
            "updated_at",
        }
        missing = required.difference(record)
        if missing:
            raise ValueError(f"Missing job fields: {sorted(missing)}")
        if record["state"] not in JOB_STATES:
            raise ValueError("Invalid job state")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, source_url, title, format_choice, requested_format_id,
                    state, attempt_no, resource_json, progress_json,
                    last_progress_json, error_code, error_message, final_relpath,
                    filename, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["job_id"],
                    record["source_url"],
                    record["title"],
                    record["format_choice"],
                    record["requested_format_id"],
                    record["state"],
                    record["attempt_no"],
                    _encode_json(record["resource_json"]),
                    _encode_json(record["progress_json"]),
                    _encode_json(record.get("last_progress_json")),
                    record["error_code"],
                    record["error_message"],
                    record["final_relpath"],
                    record["filename"],
                    record["created_at"],
                    record["updated_at"],
                ),
            )
            connection.execute(
                """
                INSERT INTO attempts
                    (job_id, attempt_no, started_at, outcome)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record["job_id"],
                    record["attempt_no"],
                    record["created_at"],
                    "running" if record["state"] in ACTIVE_JOB_STATES else record["state"],
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in ("resource_json", "progress_json", "last_progress_json"):
            result[field] = _decode_json(result[field])
        return result

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._row_to_dict(row)
        finally:
            connection.close()

    def list_jobs(self, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Invalid limit")
        decoded = _decode_cursor(cursor)
        connection = self._connect()
        try:
            if decoded is None:
                rows = connection.execute(
                    """
                    SELECT * FROM jobs WHERE state != 'deleted'
                    ORDER BY created_at DESC, job_id DESC LIMIT ?
                    """,
                    (limit + 1,),
                ).fetchall()
            else:
                created_at, job_id = decoded
                rows = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE state != 'deleted'
                      AND (created_at < ? OR (created_at = ? AND job_id < ?))
                    ORDER BY created_at DESC, job_id DESC LIMIT ?
                    """,
                    (created_at, created_at, job_id, limit + 1),
                ).fetchall()
            has_next = len(rows) > limit
            rows = rows[:limit]
            items = [self._row_to_dict(row) for row in rows]
            next_cursor = None
            if has_next and items:
                last = items[-1]
                next_cursor = _encode_cursor(last["created_at"], last["job_id"])
            return {"items": items, "next_cursor": next_cursor}
        finally:
            connection.close()

    def claim_retry(
        self,
        job_id: str,
        expected_attempt: int,
        runtime_epoch: str,
    ) -> int | None:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, attempt_no FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if (
                row is None
                or row["attempt_no"] != expected_attempt
                or row["state"] not in {"failed", "interrupted", "cancelled"}
            ):
                connection.rollback()
                return None
            new_attempt = expected_attempt + 1
            connection.execute(
                """
                UPDATE jobs
                SET state='preparing', attempt_no=?, progress_json=NULL,
                    error_code=NULL, error_message=NULL, updated_at=?
                WHERE job_id=? AND attempt_no=?
                """,
                (new_attempt, now, job_id, expected_attempt),
            )
            connection.execute(
                """
                INSERT INTO attempts
                    (job_id, attempt_no, runtime_epoch, started_at, outcome)
                VALUES (?, ?, ?, ?, 'running')
                """,
                (job_id, new_attempt, runtime_epoch, now),
            )
            connection.commit()
            return new_attempt
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_attempt(
        self,
        job_id: str,
        attempt_no: int,
        fields: dict[str, Any],
        *,
        expected_states: set[str] | frozenset[str] | None = None,
    ) -> bool:
        unknown = set(fields).difference(JOB_FIELDS)
        if unknown:
            raise ValueError(f"Unknown job fields: {sorted(unknown)}")
        if not fields:
            return False
        if "state" in fields and fields["state"] not in JOB_STATES:
            raise ValueError("Invalid job state")
        if expected_states is not None:
            expected_states = set(expected_states)
            if not expected_states:
                return False
            if not expected_states.issubset(JOB_STATES):
                raise ValueError("Invalid expected job states")
        values = dict(fields)
        for field in ("resource_json", "progress_json", "last_progress_json"):
            if field in values:
                values[field] = _encode_json(values[field])
        values.setdefault("updated_at", time.time())
        assignments = ", ".join(f"{field} = ?" for field in values)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            params = [values[field] for field in values]
            params.extend((job_id, attempt_no))
            where = "job_id=? AND attempt_no=?"
            if expected_states is not None:
                placeholders = ", ".join("?" for _ in expected_states)
                where += f" AND state IN ({placeholders})"
                params.extend(sorted(expected_states))
            cursor = connection.execute(
                f"UPDATE jobs SET {assignments} WHERE {where}",
                params,
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def request_cancel(self, job_id: str, attempt_no: int) -> bool:
        if not isinstance(attempt_no, int) or isinstance(attempt_no, bool) or attempt_no <= 0:
            return False
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE jobs
                SET state='cancelling',
                    last_progress_json=COALESCE(progress_json, last_progress_json),
                    progress_json=NULL, error_code=NULL, error_message=NULL,
                    updated_at=?
                WHERE job_id=? AND attempt_no=?
                  AND state IN ('preparing', 'downloading', 'processing')
                """,
                (time.time(), job_id, attempt_no),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finalize_attempt(
        self,
        job_id: str,
        attempt_no: int,
        outcome: str,
        fields: dict[str, Any],
        exit_code: int | None = None,
    ) -> bool:
        if outcome not in {"completed", "failed", "cancelled"}:
            raise ValueError("Invalid attempt outcome")
        unknown = set(fields).difference(JOB_FIELDS)
        if unknown:
            raise ValueError(f"Unknown job fields: {sorted(unknown)}")
        if "state" in fields and fields["state"] != outcome:
            raise ValueError("Final job state must match attempt outcome")

        now = time.time()
        values = dict(fields)
        values["state"] = outcome
        values["updated_at"] = now
        for field in ("resource_json", "progress_json", "last_progress_json"):
            if field in values:
                values[field] = _encode_json(values[field])
        assignments = ", ".join(f"{field} = ?" for field in values)
        expected_states = (
            DOWNLOAD_ACTIVE_STATES if outcome in {"completed", "failed"} else {"cancelling"}
        )
        placeholders = ", ".join("?" for _ in expected_states)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            attempt_cursor = connection.execute(
                """
                UPDATE attempts
                SET finished_at=?, exit_code=?, outcome=?
                WHERE job_id=? AND attempt_no=? AND outcome='running'
                """,
                (now, exit_code, outcome, job_id, attempt_no),
            )
            if attempt_cursor.rowcount != 1:
                connection.rollback()
                return False

            params = [values[field] for field in values]
            params.extend((job_id, attempt_no))
            params.extend(sorted(expected_states))
            job_cursor = connection.execute(
                f"""
                UPDATE jobs SET {assignments}
                WHERE job_id=? AND attempt_no=? AND state IN ({placeholders})
                """,
                params,
            )
            if job_cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def recover_active_jobs(self, runtime_epoch: str) -> dict[str, Any]:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id, attempt_no, state FROM jobs WHERE state IN (?, ?, ?, ?)",
                tuple(sorted(ACTIVE_JOB_STATES)),
            ).fetchall()
            previous_epoch_row = connection.execute(
                "SELECT value FROM service_meta WHERE key='runtime_epoch'"
            ).fetchone()
            previous_epoch = previous_epoch_row["value"] if previous_epoch_row else None
            if previous_epoch == runtime_epoch and rows:
                connection.rollback()
                return {"recovered": 0, "restart_required": True}

            for row in rows:
                if row["state"] == "cancelling":
                    job_cursor = connection.execute(
                        """
                        UPDATE jobs
                        SET state='cancelled', progress_json=NULL,
                            error_code=NULL, error_message=NULL, updated_at=?
                        WHERE job_id=? AND attempt_no=? AND state='cancelling'
                        """,
                        (now, row["job_id"], row["attempt_no"]),
                    )
                    attempt_cursor = connection.execute(
                        """
                        UPDATE attempts SET finished_at=?, outcome='cancelled'
                        WHERE job_id=? AND attempt_no=? AND outcome='running'
                        """,
                        (now, row["job_id"], row["attempt_no"]),
                    )
                else:
                    job_cursor = connection.execute(
                        """
                        UPDATE jobs
                        SET state='interrupted', last_progress_json=progress_json,
                            progress_json=NULL, error_code='service_restarted',
                            error_message='Download interrupted by service restart', updated_at=?
                        WHERE job_id=? AND attempt_no=?
                        """,
                        (now, row["job_id"], row["attempt_no"]),
                    )
                    attempt_cursor = connection.execute(
                        """
                        UPDATE attempts SET finished_at=?, outcome='interrupted'
                        WHERE job_id=? AND attempt_no=? AND outcome='running'
                        """,
                        (now, row["job_id"], row["attempt_no"]),
                    )
                if job_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                    raise RuntimeError("Unable to safely recover active job")
            connection.execute(
                """
                INSERT INTO service_meta(key, value) VALUES('runtime_epoch', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (runtime_epoch,),
            )
            connection.commit()
            return {"recovered": len(rows), "restart_required": False}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_attempt(
        self,
        job_id: str,
        attempt_no: int,
        outcome: str,
        exit_code: int | None = None,
    ) -> bool:
        if outcome not in {"completed", "failed", "interrupted"}:
            raise ValueError("Invalid attempt outcome")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE attempts
                SET finished_at=?, exit_code=?, outcome=?
                WHERE job_id=? AND attempt_no=? AND outcome='running'
                """,
                (time.time(), exit_code, outcome, job_id, attempt_no),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def begin_delete(self, job_id: str) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE jobs SET state='deleting', updated_at=?
                WHERE job_id=? AND state IN ('failed','interrupted','completed','cancelled')
                """,
                (time.time(), job_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_delete(self, job_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM attempts WHERE job_id=?", (job_id,))
            connection.execute(
                """
                UPDATE jobs
                SET source_url=NULL, title=NULL, format_choice='deleted',
                    requested_format_id=NULL, resource_json=NULL,
                    progress_json=NULL, last_progress_json=NULL,
                    error_code=NULL, error_message=NULL, final_relpath=NULL,
                    filename=NULL, state='deleted', updated_at=?
                WHERE job_id=? AND state='deleting'
                """,
                (time.time(), job_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
