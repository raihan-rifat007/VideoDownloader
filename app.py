import os
import sys
import re
import time
import uuid
import glob
import json
import shutil
import importlib.util
import subprocess
import threading
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)


def _expand_and_abs(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def _resolve_download_dir() -> str:
    """Where downloaded media is written.

    Override with RECLIP_DOWNLOAD_DIR (e.g. ~/Downloads/ReClip). Defaults to the
    project's own `downloads/` folder so the app stays self-contained.
    """
    configured = os.environ.get("RECLIP_DOWNLOAD_DIR")
    if configured:
        return _expand_and_abs(configured)
    return _expand_and_abs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads"))


DOWNLOAD_DIR = _resolve_download_dir()


def _ensure_download_dir() -> tuple[bool, str]:
    """Make sure the download dir exists and is writable. Returns (ok, detail)."""
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        if not os.access(DOWNLOAD_DIR, os.W_OK):
            return False, f"Download folder is not writable: {DOWNLOAD_DIR}"
        return True, DOWNLOAD_DIR
    except OSError as e:
        return False, f"Could not create download folder {DOWNLOAD_DIR}: {e}"


_ensure_ok, _ensure_detail = _ensure_download_dir()
if not _ensure_ok:
    print(f"[reclip] WARNING: {_ensure_detail}", file=sys.stderr)


jobs = {}

# Prefer WhatsApp-compatible H.264 + AAC when merging MP4.
H264_VIDEO_SELECTOR = (
    "bestvideo[vcodec^=avc1][ext=mp4]/bestvideo[vcodec^=avc1]/bestvideo[ext=mp4]/bestvideo"
)
AAC_AUDIO_SELECTOR = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio"

INSTAGRAM_PHOTO_CAROUSEL_ERROR = (
    "ReClip and yt-dlp do not yet support image-only Instagram carousel media."
)


def _transcode_enabled() -> bool:
    """Whether to re-encode downloads for WhatsApp compatibility.

    Disable with RECLIP_WHATSAPP_COMPAT=0 to keep the original file untouched
    (faster, and downloads are never blocked by a failed transcode).
    """
    val = os.environ.get("RECLIP_WHATSAPP_COMPAT", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def _temp_suffixes() -> tuple[str, ...]:
    """Suffixes of yt-dlp temp/partial files we should never treat as output."""
    return (".part", ".ytdl", ".tmp")


def _resolve_ytdlp():
    """Return the yt-dlp invocation as a list of argv tokens.

    Prefer the yt-dlp *module* inside the running interpreter. This is robust
    against project venvs that were copied/moved from another machine, where
    the `venv/bin/yt-dlp` script carries a stale shebang to an interpreter that
    no longer exists (which surfaces as `FileNotFoundError` / ENOENT).
    """
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    bin_ytdlp = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "yt-dlp")
    if os.path.isfile(bin_ytdlp):
        return [bin_ytdlp]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    return ["yt-dlp"]


YTDLP = _resolve_ytdlp()


# --------------------------------------------------------------------------- #
# TikTok support. yt-dlp can no longer strip TikTok's page with a bare request,
# but with `--impersonate chrome` (curl_cffi installed) it impersonates a real
# Chrome TLS fingerprint and solves the anti-bot JS challenge itself — no browser
# window needed. TikTok's challenge is intermittent, so we retry a few times.
# --------------------------------------------------------------------------- #

def _is_tiktok(url: str) -> bool:
    return "tiktok.com" in url.lower()


def _impersonate_ytdlp() -> list:
    """Base yt-dlp command for TikTok: impersonate Chrome (uses curl_cffi)."""
    return [*YTDLP, "--no-playlist", "--impersonate", "chrome"]


def _run_ytdlp_progress(cmd: list, job: dict | None = None, timeout: int = 300) -> tuple[int, str]:
    """Run yt-dlp, streaming download progress (%) into `job['progress']`.

    Returns (returncode, stderr). Adds `--newline --progress` so progress lines are
    emitted even when stdout is not a TTY."""
    # Insert progress flags before the trailing URL (the last argv token).
    if cmd and cmd[-1].startswith("http"):
        cmd = cmd[:-1] + ["--newline", "--progress", cmd[-1]]
    else:
        cmd = [*cmd, "--newline", "--progress"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    except OSError as e:
        return 2, str(e)
    out_lines: list[str] = []
    err_lines: list[str] = []

    def _read(stream, sink):
        try:
            for line in iter(stream.readline, ""):
                sink.append(line)
        finally:
            stream.close()

    t_out = threading.Thread(target=_read, args=(proc.stdout, out_lines), daemon=True)
    t_err = threading.Thread(target=_read, args=(proc.stderr, err_lines), daemon=True)
    t_out.start()
    t_err.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return 1, "yt-dlp timed out"
    t_out.join(timeout=2)
    t_err.join(timeout=2)
    if job is not None and job.get("status") != "error":
        for line in out_lines:
            m = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
            if m:
                try:
                    job["progress"] = int(float(m.group(1)))
                except ValueError:
                    pass
    return proc.returncode, "".join(err_lines)


def _tiktok_fetch_info(url: str) -> dict:
    """Return TikTok metadata via yt-dlp + Chrome impersonation."""
    last_error = ""
    for attempt in range(4):
        cmd = [*_impersonate_ytdlp(), "-j", url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except (subprocess.TimeoutExpired, OSError) as e:
            last_error = str(e)
            continue
        if result.returncode == 0:
            try:
                info = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                return {"error": f"Could not parse video info: {e}"}
            formats = _pick_video_formats(info.get("formats", []))
            if not formats:
                formats = [{"id": "all", "label": "Video", "height": None}]
            return {
                "title": info.get("title") or info.get("fulltitle") or "TikTok video",
                "thumbnail": info.get("thumbnail") or "",
                "duration": info.get("duration"),
                "uploader": info.get("uploader") or info.get("channel") or "TikTok",
                "formats": formats,
            }
        last_error = _ytdlp_error(result.stderr)
        time.sleep(1.5)
    if not last_error:
        last_error = "Could not fetch TikTok video info"
    if "impersonate" in last_error.lower() or "curl_cffi" in last_error.lower():
        last_error += " (install curl-cffi for TikTok: pip install curl-cffi)"
    return {"error": last_error}


def _tiktok_download(job, url: str, job_id: str, format_choice: str, format_id) -> bool:
    """Download a TikTok video via yt-dlp + Chrome impersonation, retrying the
    intermittent anti-bot challenge. Reports progress into `job['progress']`."""
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")
    last_error = ""
    for attempt in range(5):
        job["progress"] = 0
        cmd = [*_impersonate_ytdlp(), "-o", out_template]
        if format_choice == "audio":
            cmd += ["-x", "--audio-format", "mp3"]
        else:
            cmd += ["-f", _mp4_format_string(format_id), "--merge-output-format", "mp4"]
        cmd.append(url)
        returncode, stderr = _run_ytdlp_progress(cmd, job)
        if returncode == 0:
            candidates = [
                f for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"*{job_id}*"))
                if os.path.isfile(f) and not f.endswith(_temp_suffixes())
            ]
            if not candidates:
                last_error = "Download completed but no usable file was found"
                continue
            target = [f for f in candidates if f.endswith(".mp3")] if format_choice == "audio" \
                else [f for f in candidates if f.endswith(".mp4")]
            chosen = target[0] if target else max(candidates, key=os.path.getsize)
            job["file"] = chosen
            for f in candidates:
                if f != chosen and os.path.isfile(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
            return True
        last_error = _ytdlp_error(stderr)
        time.sleep(1.5)
    job["_browser_err"] = last_error or "Download failed"
    if "impersonate" in last_error.lower() or "curl_cffi" in last_error.lower():
        job["_browser_err"] += " (install curl-cffi for TikTok: pip install curl-cffi)"
    return False


def _ytdlp_error(stderr: str) -> str:
    """Return the meaningful yt-dlp ERROR line when present."""
    lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("ERROR:"):
            return ln
    return lines[-1] if lines else "yt-dlp failed"


def _instagram_photo_carousel_error(stderr: str, url: str) -> str | None:
    """Detect Instagram image-only carousel posts from yt-dlp output."""
    if "instagram.com" not in url.lower():
        return None
    stderr_lower = stderr.lower()
    if "no video formats found" not in stderr_lower:
        return None
    if stderr_lower.count("no video formats found") > 1:
        return INSTAGRAM_PHOTO_CAROUSEL_ERROR
    j_result = subprocess.run(
        [*YTDLP, "--no-playlist", "-J", url],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if not j_result.stdout.strip():
        return INSTAGRAM_PHOTO_CAROUSEL_ERROR
    try:
        info = json.loads(j_result.stdout)
    except json.JSONDecodeError:
        return INSTAGRAM_PHOTO_CAROUSEL_ERROR
    if info.get("_type") != "playlist":
        return None
    entries = info.get("entries") or []
    if info.get("playlist_count", 0) > 0 and entries and all(e is None for e in entries):
        return INSTAGRAM_PHOTO_CAROUSEL_ERROR
    return None


def _info_error(stderr: str, url: str) -> str:
    ig_err = _instagram_photo_carousel_error(stderr, url)
    if ig_err:
        return ig_err
    return _ytdlp_error(stderr)


def _is_h264(fmt: dict) -> bool:
    vcodec = (fmt.get("vcodec") or "").lower()
    return vcodec.startswith("avc1") or "h264" in vcodec


def _is_unknown_video_metadata(fmt: dict) -> bool:
    """Video stream with no codec tag (e.g. some X/http progressive URLs)."""
    return (
        fmt.get("vcodec", "none") == "none"
        and fmt.get("video_ext", "none") != "none"
    )


def _is_video_only_format(fmt: dict) -> bool:
    vcodec = (fmt.get("vcodec") or "").lower()
    return bool(vcodec and vcodec != "none")


def _format_tbr(fmt: dict) -> float:
    return fmt.get("tbr") or fmt.get("vbr") or 0


def _pick_video_formats(formats: list) -> list:
    """WhatsApp MP4 picker: H.264 first; unknown-metadata fallback; VP9/HEVC only if no H.264."""
    by_height: dict[int, list] = {}
    unknown_no_height = []

    for fmt in formats:
        if _is_h264(fmt) or _is_unknown_video_metadata(fmt):
            height = fmt.get("height")
            if height:
                by_height.setdefault(height, []).append(fmt)
            elif _is_unknown_video_metadata(fmt):
                unknown_no_height.append(fmt)

    picked = []
    for height, fmts in by_height.items():
        h264_fmts = [f for f in fmts if _is_h264(f)]
        if h264_fmts:
            best = max(h264_fmts, key=_format_tbr)
        else:
            unknown_fmts = [f for f in fmts if _is_unknown_video_metadata(f)]
            if not unknown_fmts:
                continue
            best = max(unknown_fmts, key=_format_tbr)
        picked.append({
            "id": best["format_id"],
            "label": f"{height}p",
            "height": height,
        })

    for fmt in unknown_no_height:
        picked.append({
            "id": fmt["format_id"],
            "label": "Original",
            "height": 0,
        })

    if not any(_is_h264(f) for f in formats):
        picked_heights = {p["height"] for p in picked if p["height"]}
        fallback_by_height: dict[int, list] = {}
        for fmt in formats:
            height = fmt.get("height")
            if height and height not in picked_heights and _is_video_only_format(fmt):
                fallback_by_height.setdefault(height, []).append(fmt)
        for height, fmts in fallback_by_height.items():
            best = max(fmts, key=_format_tbr)
            picked.append({
                "id": best["format_id"],
                "label": f"{height}p",
                "height": height,
            })

    picked.sort(key=lambda x: x["height"], reverse=True)
    return picked


def _mp4_format_string(format_id=None) -> str:
    """Build yt-dlp format string with grouped video+audio pairs before combined fallback."""
    audio_group = f"({AAC_AUDIO_SELECTOR})"
    if format_id:
        return f"{format_id}+{audio_group}/bestvideo+bestaudio/best"
    return f"({H264_VIDEO_SELECTOR})+{audio_group}/bestvideo+bestaudio/best"


def _ffprobe_streams(path: str) -> list:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return []
    result = subprocess.run(
        [
            ffprobe,
            "-hide_banner",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,codec_tag_string,pix_fmt,profile",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        return []


def _is_whatsapp_compatible_video(path: str) -> bool:
    """True when file is H.264 + yuv420p + AAC-LC (WhatsApp-friendly)."""
    streams = _ffprobe_streams(path)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video or not audio:
        return False

    vcodec = (video.get("codec_name") or "").lower()
    vtag = (video.get("codec_tag_string") or "").lower()
    pix = (video.get("pix_fmt") or "").lower()
    acodec = (audio.get("codec_name") or "").lower()
    aprofile = (audio.get("profile") or "").upper()

    if vcodec != "h264" and "avc" not in vtag:
        return False
    if pix != "yuv420p":
        return False
    if acodec != "aac":
        return False
    if "HE-AAC" in aprofile:
        return False
    return True


def _transcode_for_whatsapp(path: str, job: dict | None = None) -> tuple[bool, str | None]:
    """Re-encode to H.264 Main + AAC-LC; atomic replace. Original kept on failure.

    When `job` is given its `phase` is set to "Converting" so the UI can show
    activity (ffmpeg's own out_time is misleading, so no fake percentage)."""
    if job is not None:
        job["phase"] = "Converting"
        job["progress"] = None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg not found — cannot convert video for WhatsApp compatibility"

    tmp_path = path + ".whatsapp.mp4"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        path,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "main",
        "-c:a",
        "aac",
        "-profile:a",
        "aac_low",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        tmp_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
            detail = (result.stderr or "").strip().splitlines()[-1] if result.stderr else ""
            msg = "Video conversion for WhatsApp failed"
            if detail:
                msg = f"{msg}: {detail}"
            return False, msg
        if not _is_whatsapp_compatible_video(tmp_path):
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
            if not shutil.which("ffprobe"):
                return False, "ffprobe not found — cannot verify converted video"
            return False, "Video conversion for WhatsApp failed — output not compatible"
        os.replace(tmp_path, path)
        return True, None
    except (subprocess.TimeoutExpired, OSError):
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False, "Video conversion for WhatsApp timed out"


def parse_ytdlp_json(stdout):
    """Parse yt-dlp JSON output.

    With ``-j`` yt-dlp prints one JSON object per line. Some extractors
    emit multiple videos even with ``--no-playlist``, so stdout contains
    several objects and a plain ``json.loads`` raises "Extra data".
    Return the first valid object.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        return json.loads(line)
    raise ValueError("yt-dlp returned no data")


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]

    dir_ok, dir_detail = _ensure_download_dir()
    if not dir_ok:
        job["status"] = "error"
        job["error"] = dir_detail
        return

    # TikTok: yt-dlp + Chrome impersonation solves the anti-bot challenge; runs
    # as a background job (never blocks the UI) and reports live progress.
    if _is_tiktok(url):
        job["status"] = "downloading"
        job["phase"] = "Downloading"
        job["progress"] = 0
        ok = _tiktok_download(job, url, job_id, format_choice, format_id)
        if not ok:
            job["status"] = "error"
            job["error"] = job.get("_browser_err") or "Download failed"
            return
        chosen = job["file"]
        ext = os.path.splitext(chosen)[1]
        title = job.get("title", "").strip()
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:100].strip()
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)
        if format_choice != "audio" and chosen.endswith(".mp4") and _transcode_enabled():
            if not _is_whatsapp_compatible_video(chosen):
                ok, transcode_err = _transcode_for_whatsapp(chosen, job)
                if not ok:
                    job["warning"] = transcode_err or "Could not convert video for WhatsApp compatibility"
        job["phase"] = None
        job["progress"] = None
        job["status"] = "done"
        return

    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = [*YTDLP, "--no-playlist", "-o", out_template]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    else:
        cmd += ["-f", _mp4_format_string(format_id), "--merge-output-format", "mp4"]

    cmd.append(url)

    job["phase"] = "Downloading"
    job["progress"] = 0
    returncode, stderr = _run_ytdlp_progress(cmd, job)
    if returncode != 0:
        job["status"] = "error"
        job["error"] = _info_error(stderr, url)
        return

    # Discover the produced file(s), ignoring yt-dlp temp/partial leftovers.
    candidates = [
        f for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"*{job_id}*"))
        if os.path.isfile(f) and not f.endswith(_temp_suffixes())
    ]
    if not candidates:
        job["status"] = "error"
        job["error"] = "Download completed but no usable file was found"
        return

    if format_choice == "audio":
        target = [f for f in candidates if f.endswith(".mp3")]
        chosen = target[0] if target else max(candidates, key=os.path.getsize)
    else:
        target = [f for f in candidates if f.endswith(".mp4")]
        chosen = target[0] if target else max(candidates, key=os.path.getsize)

    # Record the file before the optional transcode so media is never lost
    # or blocked by a later failure.
    job["file"] = chosen
    ext = os.path.splitext(chosen)[1]
    title = job.get("title", "").strip()
    if title:
        safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:100].strip()
        job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
    else:
        job["filename"] = os.path.basename(chosen)

    # Clean up every file produced for this job (keeps only `chosen`, and
    # also removes yt-dlp temp/partial leftovers).
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"*{job_id}*")):
        if f != chosen and os.path.isfile(f):
            try:
                os.remove(f)
            except OSError:
                pass

    # Optional WhatsApp-compatible re-encode. Never blocks: on failure we
    # keep the original file and surface a warning instead.
    if format_choice != "audio" and chosen.endswith(".mp4") and _transcode_enabled():
        if not _is_whatsapp_compatible_video(chosen):
            ok, transcode_err = _transcode_for_whatsapp(chosen, job)
            if not ok:
                job["warning"] = transcode_err or "Could not convert video for WhatsApp compatibility"

    job["phase"] = None
    job["progress"] = None
    job["status"] = "done"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # yt-dlp can no longer extract TikTok, so use a real browser for metadata.
    if _is_tiktok(url):
        info = _tiktok_fetch_info(url)
        if "error" in info:
            return jsonify({"error": info["error"]}), 400
        return jsonify(info)

    cmd = [*YTDLP, "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": _info_error(result.stderr, url)}), 400

        info = parse_ytdlp_json(result.stdout)
        formats = _pick_video_formats(info.get("formats", []))

        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/playlist", methods=["POST"])
def get_playlist_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = [*YTDLP, "--flat-playlist", "-J", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": _info_error(result.stderr, url)}), 400

        info = json.loads(result.stdout)
        entries = info.get("entries", [])
        urls = [entry.get("url") for entry in entries if entry.get("url")]
        return jsonify({"urls": urls})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching playlist info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    dir_ok, dir_detail = _ensure_download_dir()
    if not dir_ok:
        return jsonify({"error": dir_detail}), 500

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "warning": job.get("warning"),
        "filename": job.get("filename"),
        "path": job.get("file"),
        "phase": job.get("phase"),
        "progress": job.get("progress"),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    path = job.get("file")
    if not path or not os.path.isfile(path):
        return jsonify({"error": "File no longer exists on disk"}), 404
    # Defense in depth: only serve files that live inside the download folder.
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(DOWNLOAD_DIR) + os.sep):
        return jsonify({"error": "Invalid file path"}), 400
    return send_file(real, as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    # A single threaded (default) server blocks every request while one slow
    # request (e.g. a TikTok info fetch, a browser download or a transcode) is
    # in flight — that's the "it just sits there" bug. Serve concurrently.
    app.run(host=host, port=port, threaded=True)
