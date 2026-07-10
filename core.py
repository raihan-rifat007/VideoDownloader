import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

PROBE_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300


class ReclipError(Exception):
    """An expected processing failure that is safe to show users."""


def _run(args, timeout):
    cmd = [sys.executable, "-m", "yt_dlp", *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ReclipError(f"yt-dlp timed out after {timeout}s") from e
    except OSError as e:
        raise ReclipError(f"Cannot start yt-dlp: {e}") from e


def _ytdlp_error(result):
    stderr = result.stderr.strip() if result.stderr else ""
    return stderr.split("\n")[-1] if stderr else "yt-dlp failed"


def _load_json(result):
    """Parse the first JSON object from yt-dlp output.

    With ``-j`` yt-dlp prints one JSON object per line. Some extractors
    emit multiple videos even with ``--no-playlist``, so stdout contains
    several objects and a plain ``json.loads`` raises "Extra data".
    """
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except (json.JSONDecodeError, ValueError):
            raise ReclipError("yt-dlp returned unreadable output")
    raise ReclipError("yt-dlp returned no data")


def probe(url):
    """Return media metadata and the best video format for each height."""
    result = _run(["--no-playlist", "-j", "--", url], PROBE_TIMEOUT)
    if result.returncode != 0:
        raise ReclipError(_ytdlp_error(result))
    info = _load_json(result)

    best_by_height = {}
    for f in (info.get("formats") or []):
        height = f.get("height")
        if height and f.get("vcodec", "none") != "none" and f.get("format_id"):
            tbr = f.get("tbr") or 0
            if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                best_by_height[height] = f

    formats = [
        {"id": f["format_id"], "label": f"{height}p", "height": height}
        for height, f in best_by_height.items()
    ]
    formats.sort(key=lambda x: x["height"], reverse=True)

    return {
        "title": info.get("title", ""),
        "uploader": info.get("uploader", ""),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail", ""),
        "formats": formats,
    }


def expand_playlist(url):
    """Return every non-empty entry URL from a playlist."""
    result = _run(["--flat-playlist", "-J", "--", url], PROBE_TIMEOUT)
    if result.returncode != 0:
        raise ReclipError(_ytdlp_error(result))
    info = _load_json(result)
    return [e.get("url") for e in (info.get("entries") or []) if e.get("url")]


def sanitize_filename(title):
    """Return a trimmed filename stem without reserved punctuation.

    The result is capped at 100 characters.
    """
    return "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:100].strip()


def _reserve_dest(output_dir, stem, ext):
    dest = os.path.join(output_dir, f"{stem}{ext}")
    n = 0
    while True:
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            dest = os.path.join(output_dir, f"{stem} ({n}){ext}")
        except OSError as e:
            raise ReclipError(f"Cannot write to output directory: {e}")
        else:
            os.close(fd)
            return dest


def download(url, kind="video", quality=None, format_id=None, output_dir=".", name=None,
             timeout=DOWNLOAD_TIMEOUT):
    """Download one URL as MP4 video or MP3 audio and return saved-file metadata.

    Existing destination files are preserved by adding a numbered suffix.
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        work = tempfile.mkdtemp(dir=output_dir, prefix=".reclip-")
    except OSError as e:
        raise ReclipError(f"Cannot write to output directory: {e}")
    try:
        out_template = os.path.join(work, "%(title)s.%(ext)s")
        cmd = ["--no-playlist", "-o", out_template]

        if kind == "audio":
            cmd += ["-x", "--audio-format", "mp3"]
        else:
            if format_id:
                selector = f"{format_id}+bestaudio/best"
            elif quality:
                selector = f"bestvideo[height<=?{quality}]+bestaudio/best[height<=?{quality}]"
            else:
                selector = "bestvideo+bestaudio/best"
            cmd += ["-f", selector, "--merge-output-format", "mp4",
                    "--remux-video", "mp4"]

        cmd += ["--", url]

        result = _run(cmd, timeout)
        if result.returncode != 0:
            raise ReclipError(_ytdlp_error(result))

        try:
            files = [os.path.join(work, fn) for fn in sorted(os.listdir(work))]
        except OSError as e:
            raise ReclipError(f"Cannot inspect downloaded files: {e}") from e
        if not files:
            raise ReclipError("Download completed but no file was found")

        wanted_ext = ".mp3" if kind == "audio" else ".mp4"
        matching = [f for f in files if os.path.splitext(f)[1].lower() == wanted_ext]
        if not matching:
            raise ReclipError(f"Download completed but no {wanted_ext[1:].upper()} file was found")
        chosen = matching[0]

        title = os.path.splitext(os.path.basename(chosen))[0]
        ext = os.path.splitext(chosen)[1]
        final = sanitize_filename(name) if name else sanitize_filename(title)
        final = final or sanitize_filename(title) or "download"
        dest = _reserve_dest(output_dir, final, ext)
        try:
            shutil.move(chosen, dest)
        except Exception as e:
            try:
                os.remove(dest)
            except OSError:
                pass
            if isinstance(e, OSError):
                raise ReclipError(f"Cannot save file to {dest}: {e}")
            raise
        return {"file": dest, "title": title, "ext": ext}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _vtt_to_text(vtt):
    out = []
    blocks = []
    block = []
    for raw in vtt.splitlines():
        if raw.strip():
            block.append(raw)
        elif block:
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)

    for block in blocks:
        first = block[0].lstrip("\ufeff").strip()
        if first.startswith("WEBVTT"):
            continue
        if first == "NOTE" or first.startswith(("NOTE ", "NOTE\t")):
            continue
        timing = next((i for i, raw in enumerate(block) if "-->" in raw), None)
        if timing is None:
            continue
        for raw in block[timing + 1:]:
            line = re.sub(r"<[^>]+>", "", raw).strip()
            if line and (not out or out[-1] != line):
                out.append(line)
    return "\n".join(out)


def _raw_info(url):
    result = _run(["--no-playlist", "--skip-download", "-j", "--", url], PROBE_TIMEOUT)
    if result.returncode != 0:
        raise ReclipError(_ytdlp_error(result))
    return _load_json(result)


def _fetch_subs(url, lang, auto, work):
    flag = "--write-auto-sub" if auto else "--write-sub"
    cmd = ["--no-playlist", flag, "--sub-lang", lang,
           "--sub-format", "vtt", "--convert-subs", "vtt",
           "--skip-download", "-o", os.path.join(work, "sub.%(ext)s"), "--", url]
    result = _run(cmd, PROBE_TIMEOUT)
    if result.returncode != 0:
        raise ReclipError(_ytdlp_error(result))
    vtts = [os.path.join(work, fn) for fn in os.listdir(work) if fn.endswith(".vtt")]
    return vtts[0] if vtts else None


def transcript(url, lang=None):
    """Return requested manual subtitles, falling back to automatic captions.

    Successful results include every available subtitle and caption language.
    """
    lang = lang or "en"
    work = None
    try:
        work = tempfile.mkdtemp(prefix="reclip-sub-")
        auto = False
        chosen = _fetch_subs(url, lang, auto=False, work=work)
        if chosen is None:
            for fn in os.listdir(work):
                os.remove(os.path.join(work, fn))
            chosen = _fetch_subs(url, lang, auto=True, work=work)
            auto = chosen is not None
        info = _raw_info(url)
        langs = sorted(set(info.get("subtitles", {})) | set(info.get("automatic_captions", {})))
        if chosen is None:
            return {"text": None, "lang": None, "auto": None, "available_langs": langs}
        base = os.path.basename(chosen)
        parts = base.split(".")
        found_lang = parts[1] if len(parts) >= 3 else lang
        with open(chosen, encoding="utf-8") as fh:
            text = _vtt_to_text(fh.read())
        return {"text": text, "lang": found_lang, "auto": auto,
                "available_langs": sorted(set(langs) | {found_lang})}
    except OSError as e:
        raise ReclipError(f"Cannot process subtitle files: {e}") from e
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
