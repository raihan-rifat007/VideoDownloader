"""Structured yt-dlp progress parsing for ReClip."""

import json
import math


DOWNLOAD_PREFIX = "RECLIP_PROGRESS "
POSTPROCESS_PREFIX = "RECLIP_POSTPROCESS "
FINAL_PREFIX = "RECLIP_FINAL "
MAX_LINE_LENGTH = 16 * 1024


def _finite_nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _nonnegative_integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def parse_progress_line(line):
    """Parse one allowlisted progress line, returning a small event or None."""
    if not isinstance(line, str) or len(line) > MAX_LINE_LENGTH:
        return None

    if line.startswith(DOWNLOAD_PREFIX):
        kind = "download"
        payload = line[len(DOWNLOAD_PREFIX):]
    elif line.startswith(POSTPROCESS_PREFIX):
        kind = "postprocess"
        payload = line[len(POSTPROCESS_PREFIX):]
    elif line.startswith(FINAL_PREFIX):
        payload = line[len(FINAL_PREFIX):]
        try:
            path = json.loads(payload)
        except (TypeError, ValueError):
            return None
        if not isinstance(path, str) or not path:
            return None
        return {"kind": "final", "path": path}
    else:
        return None

    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {"kind": kind, "data": data}


def normalize_download_progress(data, now):
    """Return a safe current-stream progress snapshot."""
    if not isinstance(data, dict):
        data = {}

    downloaded = _nonnegative_integer(data.get("downloaded_bytes"))
    total = _nonnegative_integer(data.get("total_bytes"))
    estimate = _nonnegative_integer(data.get("total_bytes_estimate"))

    total_is_estimate = False
    selected_total = total if total and total > 0 else None
    if selected_total is None and estimate and estimate > 0:
        selected_total = estimate
        total_is_estimate = True

    percent = None
    if downloaded is not None and selected_total is not None:
        percent = min(100.0, max(0.0, downloaded / selected_total * 100))

    speed = _finite_nonnegative(data.get("speed"))
    eta = _finite_nonnegative(data.get("eta"))
    timestamp = _finite_nonnegative(now)

    return {
        "scope": "current_stream",
        "percent": percent,
        "downloaded_bytes": downloaded,
        "total_bytes": selected_total,
        "total_is_estimate": total_is_estimate,
        "speed_bps": speed,
        "eta_seconds": eta,
        "updated_at": timestamp,
    }
