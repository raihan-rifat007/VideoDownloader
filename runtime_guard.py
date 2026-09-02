"""Runtime identity and single-owner lock for the Linux container service."""

from __future__ import annotations

import os
from pathlib import Path

if os.name == "posix":
    import fcntl
else:  # pragma: no cover - exercised only by a Windows deployment adapter
    import msvcrt


def _read_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()


def _read_pid1_starttime() -> str:
    stat = Path("/proc/1/stat").read_text(encoding="ascii")
    try:
        _, fields = stat.rsplit(")", 1)
    except ValueError:
        raise OSError("invalid /proc/1/stat") from None
    values = fields.strip().split()
    if len(values) <= 19:
        raise OSError("incomplete /proc/1/stat")
    return values[19]


def runtime_epoch() -> str:
    """Return an identity that changes after a complete Linux container restart."""
    if os.name != "posix":
        raise RuntimeError("runtime identity is only supported in the Linux container")
    try:
        boot_id = _read_boot_id()
        starttime = _read_pid1_starttime()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("Cannot read runtime identity") from exc
    if not boot_id or not starttime:
        raise RuntimeError("Cannot read runtime identity")
    return f"{boot_id}:{starttime}"


class RuntimeGuard:
    """Hold an OS file lock for the lifetime of the task-service owner."""

    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        self._file = None

    def acquire(self) -> str:
        if self._file is not None:
            raise RuntimeError("Runtime guard is already acquired")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        try:
            if os.name == "posix":
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - Docker deployment is Linux
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise RuntimeError("Another task service already owns the runtime lock") from exc
        self._file = handle
        try:
            return runtime_epoch()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            if os.name == "posix":
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Docker deployment is Linux
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
