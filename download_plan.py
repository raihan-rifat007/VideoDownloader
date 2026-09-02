"""Resolve a media choice once and build safe, resumable yt-dlp commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable


FORMAT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _format_id(value: Any) -> str:
    value = str(value or "")
    if not FORMAT_ID_RE.fullmatch(value):
        raise ValueError("Unsupported format identifier")
    return value


def _codec_present(value: Any) -> bool:
    return bool(value and value != "none")


def _score(media_format: dict[str, Any]) -> tuple[float, float, float]:
    def number(name: str) -> float:
        value = media_format.get(name)
        return float(value) if isinstance(value, (int, float)) and value >= 0 else 0.0

    return number("height"), number("tbr"), number("filesize")


def _select_format(
    formats: list[dict[str, Any]], requested_id: str | None, predicate: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    candidates = [item for item in formats if predicate(item)]
    if requested_id:
        requested_id = _format_id(requested_id)
        candidates = [item for item in candidates if str(item.get("format_id")) == requested_id]
        if not candidates:
            raise ValueError("Requested format is unavailable")
    if not candidates:
        raise ValueError("No compatible format is available")
    return max(candidates, key=_score)


def _format_snapshot(media_format: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _format_id(media_format.get("format_id")),
        "ext": media_format.get("ext"),
        "protocol": media_format.get("protocol"),
        "vcodec": media_format.get("vcodec", "none"),
        "acodec": media_format.get("acodec", "none"),
        "filesize": media_format.get("filesize"),
        "filesize_approx": media_format.get("filesize_approx"),
        "etag": media_format.get("etag"),
    }


def resolve_plan(
    source_url: str,
    format_choice: str,
    requested_format_id: str | None,
    *,
    info: dict[str, Any],
) -> dict[str, Any]:
    """Resolve metadata into a plan whose format IDs will not change on retry."""
    if format_choice not in {"audio", "video"}:
        raise ValueError("Invalid format choice")
    if not isinstance(source_url, str) or not source_url.startswith(("http://", "https://")):
        raise ValueError("Invalid source URL")
    formats = info.get("formats")
    if not isinstance(formats, list):
        raise ValueError("No formats returned")
    formats = [item for item in formats if isinstance(item, dict)]
    extractor = info.get("extractor_key") or info.get("extractor")
    video_id = info.get("id")
    if not isinstance(extractor, str) or not extractor or not isinstance(video_id, str) or not video_id:
        raise ValueError("Incomplete resource identity")

    if format_choice == "audio":
        audio_only = [
            item for item in formats
            if _codec_present(item.get("acodec")) and not _codec_present(item.get("vcodec"))
        ]
        selected = _select_format(
            audio_only or formats,
            requested_format_id,
            lambda item: _codec_present(item.get("acodec")),
        )
        selected_formats = [_format_snapshot(selected)]
        selector = selected_formats[0]["id"]
    else:
        video = _select_format(
            formats,
            requested_format_id,
            lambda item: _codec_present(item.get("vcodec")),
        )
        selected_formats = [_format_snapshot(video)]
        selector = selected_formats[0]["id"]
        if not _codec_present(video.get("acodec")):
            audio = _select_format(
                formats,
                None,
                lambda item: _codec_present(item.get("acodec")) and not _codec_present(item.get("vcodec")),
            )
            audio_snapshot = _format_snapshot(audio)
            selected_formats.append(audio_snapshot)
            selector = f"{selector}+{audio_snapshot['id']}"

    return {
        "source_url": source_url,
        "extractor": extractor,
        "video_id": video_id,
        "format_choice": format_choice,
        "requested_format_id": requested_format_id,
        "formats": selected_formats,
        "format_selector": selector,
    }


def validate_resume_plan(stored_plan: dict[str, Any], current_plan: dict[str, Any]) -> None:
    """Reject a retry when the source or the selected media streams changed."""
    identity_fields = ("extractor", "video_id", "format_choice", "format_selector")
    if any(stored_plan.get(field) != current_plan.get(field) for field in identity_fields):
        raise ValueError("Source or format changed; restart required")
    stored_formats = stored_plan.get("formats", [])
    current_formats = current_plan.get("formats", [])
    fields = ("id", "ext", "protocol", "vcodec", "acodec")
    if len(stored_formats) != len(current_formats) or any(
        any(old.get(field) != new.get(field) for field in fields)
        for old, new in zip(stored_formats, current_formats)
    ):
        raise ValueError("Source or format changed; restart required")


def build_download_command(plan: dict[str, Any], task_dir: str | Path) -> list[str]:
    """Build a shell-free command using only server-owned plan values."""
    task_dir = Path(task_dir).resolve()
    task_dir.mkdir(parents=True, exist_ok=True)
    selector = plan.get("format_selector")
    if not isinstance(selector, str):
        raise ValueError("Missing fixed format selector")
    selector_ids = selector.split("+")
    if not selector_ids or any(not FORMAT_ID_RE.fullmatch(item) for item in selector_ids):
        raise ValueError("Invalid fixed format selector")
    source_url = plan.get("source_url")
    if not isinstance(source_url, str) or not source_url.startswith(("http://", "https://")):
        raise ValueError("Invalid source URL")

    command = [
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--continue",
        "--part",
        "--newline",
        "--progress",
        "--progress-delta",
        "0.5",
        "--progress-template",
        "download:RECLIP_PROGRESS %(progress.{status,downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta})j",
        "--progress-template",
        "postprocess:RECLIP_POSTPROCESS %(progress.{status,postprocessor})j",
        "--print",
        "after_move:RECLIP_FINAL %(filepath)j",
        "--no-simulate",
        "--abort-on-unavailable-fragments",
        "-f",
        selector,
        "-o",
        str(task_dir / "media.%(ext)s"),
    ]
    if plan.get("format_choice") == "audio":
        command.extend(["-x", "--audio-format", "mp3"])
    elif plan.get("format_choice") == "video":
        command.extend(["--merge-output-format", "mp4"])
    else:
        raise ValueError("Invalid format choice")
    command.extend(["--", source_url])
    return command


def validate_final_file(
    task_dir: str | Path,
    reported_path: str | Path,
    format_choice: str,
    *,
    probe_runner: Callable[[Path, str], Any] | None = None,
) -> dict[str, Any]:
    """Validate a post-processed file and return a task-relative path."""
    if format_choice not in {"audio", "video"}:
        raise ValueError("Invalid format choice")
    root = Path(task_dir).resolve()
    raw_path = Path(reported_path)
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    candidate = candidate.resolve(strict=False)
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Final file is outside the task directory")
    if Path(reported_path).is_symlink() or candidate.is_symlink():
        raise ValueError("Final file must not be a symlink")
    if candidate.suffix.lower() in {".part", ".ytdl", ".tmp", ".temp"}:
        raise ValueError("Final file is still temporary")
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise ValueError("Final media file is missing or empty")
    if probe_runner is not None and probe_runner(candidate, format_choice) is not True:
        raise ValueError("Final media file failed validation")
    return {
        "relative_path": str(candidate.relative_to(root)),
        "filename": candidate.name,
        "size_bytes": candidate.stat().st_size,
    }
