"""Small security and filesystem helpers for transcription jobs."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlsplit


ARTIFACT_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")


def normalize_uuid(value: str | uuid.UUID | None = None) -> str:
    """Return a canonical UUID string, generating one when omitted."""

    return str(uuid.uuid4() if value is None else uuid.UUID(str(value)))


def normalize_language(value: object) -> str | None:
    """Validate a faster-whisper language code or return ``None`` for auto."""

    if value is None:
        return None
    language = str(value).strip().lower()
    if not language or language == "auto":
        return None
    if not LANGUAGE_RE.fullmatch(language):
        raise ValueError("Language must be a two- or three-letter Whisper language code")
    return language


def validate_public_url(value: object, *, resolve_dns: bool = False) -> str:
    """Validate the non-authenticated public HTTP(S) URL accepted by v1.

    Literal private targets are always rejected. Workers additionally resolve
    DNS immediately before acquisition so aliases to private/link-local targets
    cannot reach yt-dlp.
    """

    url = str(value or "").strip()
    if not url:
        raise ValueError("URL is required")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public http:// and https:// URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Authenticated URLs are not supported")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local and private URLs are not supported")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Local and private URLs are not supported")
    if resolve_dns and address is None:
        try:
            answers = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ValueError("Public media hostname could not be resolved") from exc
        resolved = {item[4][0].split("%", 1)[0] for item in answers if item[4]}
        if not resolved or any(not ipaddress.ip_address(item).is_global for item in resolved):
            raise ValueError("Local and private URLs are not supported")

    return url


def sanitize_filename(value: str, fallback: str = "upload.bin") -> str:
    """Produce a portable basename while preserving a useful file extension."""

    name = Path(str(value or "")).name
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch >= " " and ch not in '<>:"/\\|?*')
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    stem, suffix = os.path.splitext(name)
    suffix = re.sub(r"[^A-Za-z0-9.]", "", suffix)[:16]
    stem = stem.strip(" .")[:180] or "upload"
    return f"{stem}{suffix}"[:200]


def validate_artifact_key(key: str) -> str:
    normalized = str(key or "").strip().lower()
    if not ARTIFACT_KEY_RE.fullmatch(normalized):
        raise ValueError("Invalid artifact key")
    return normalized


def job_directory(data_dir: str | os.PathLike[str], job_id: str) -> Path:
    canonical = normalize_uuid(job_id)
    return Path(data_dir).resolve() / "jobs" / canonical


def is_path_within(path: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def ensure_path_within(path: str | os.PathLike[str], parent: str | os.PathLike[str]) -> Path:
    resolved = Path(path).resolve()
    if not is_path_within(resolved, parent):
        raise ValueError("Path escapes the job directory")
    return resolved


def sha256_file(path: str | os.PathLike[str], chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
