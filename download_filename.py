"""Build safe, user-facing names for completed media downloads."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any


MEDIA_EXTENSIONS = {
    ".mp4",
    ".mp3",
    ".m4a",
    ".m4v",
    ".mkv",
    ".webm",
    ".mov",
    ".aac",
    ".ogg",
    ".opus",
    ".wav",
    ".flac",
}
FORBIDDEN = set('\\/:*?"<>|')
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    prefix + suffix
    for prefix in ("COM", "LPT")
    for suffix in "123456789¹²³"
}


def build_download_filename(title: object, job_id: str, extension: str) -> str:
    """Return a safe, deterministic browser-facing media filename."""
    if not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        raise ValueError("Invalid job ID")
    if not isinstance(extension, str) or extension.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("Unsupported media extension")

    ext = extension.lower()
    stem = unicodedata.normalize("NFC", title if isinstance(title, str) else "")
    stem = " ".join(stem.split())
    stem = "".join("_" if char in FORBIDDEN else char for char in stem)
    stem = "".join(
        char for char in stem if not unicodedata.category(char).startswith("C")
    )
    stem = stem.strip(" .")
    if stem.lower().endswith(ext):
        stem = stem[:-len(ext)].rstrip(" .")

    fallback = f"视频-{job_id[:8]}"
    if not any(unicodedata.category(char)[0] in "LNS" for char in stem):
        stem = fallback
    if stem.split(".", 1)[0].rstrip().upper() in RESERVED:
        stem = "视频-" + stem

    stem = stem[:100].rstrip(" .")
    while len((stem + ext).encode("utf-8")) > 180:
        stem = stem[:-1].rstrip(" .")
    return (stem or fallback) + ext


def job_download_filename(job: dict[str, Any]) -> str | None:
    """Derive a public filename without mutating the persisted job record."""
    if job.get("state") != "completed":
        return None
    relative_path = job.get("final_relpath")
    if not isinstance(relative_path, str) or not relative_path:
        return None

    # Normalize separators for suffix extraction only, never for file access.
    extension = PurePosixPath(relative_path.replace("\\", "/")).suffix
    try:
        return build_download_filename(job.get("title"), job.get("job_id"), extension)
    except ValueError:
        return None
