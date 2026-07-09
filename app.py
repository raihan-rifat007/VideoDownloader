import os
import uuid
import glob
import json
import time
import hmac
import subprocess
import threading
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

jobs = {}

# --- Security configuration --------------------------------------------
# All of these are optional / off-by-default so the "just run it" quick
# start keeps working, but can be turned on when exposing reclip beyond
# localhost (see README "Security" section).

# If set, every /api/* request must send a matching `X-API-Key` header.
API_KEY = os.environ.get("RECLIP_API_KEY", "").strip()

# Set RECLIP_TRUST_PROXY=1 only if reclip is running behind a proxy you
# control that sets X-Forwarded-For itself (otherwise clients could spoof
# their rate-limit identity).
TRUST_PROXY = os.environ.get("RECLIP_TRUST_PROXY", "") == "1"

RATE_LIMIT_MAX = int(os.environ.get("RECLIP_RATE_LIMIT", "20"))
RATE_LIMIT_WINDOW = int(os.environ.get("RECLIP_RATE_WINDOW", "60"))

_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)


def _client_ip():
    if TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limited(max_requests=None, window=None):
    """Simple in-memory sliding-window rate limiter, keyed by client IP + route.

    Intentionally dependency-free (no Flask-Limiter) to keep the project's
    "2 dependencies" footprint. Good enough for a single-process, self-hosted
    app; not meant to survive a restart or scale across workers.
    """
    limit = RATE_LIMIT_MAX if max_requests is None else max_requests
    win = RATE_LIMIT_WINDOW if window is None else window

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{_client_ip()}:{request.path}"
            now = time.monotonic()
            with _rate_lock:
                bucket = _rate_buckets[key]
                while bucket and now - bucket[0] > win:
                    bucket.popleft()
                if len(bucket) >= limit:
                    retry_after = int(max(0, win - (now - bucket[0]))) + 1
                    resp = jsonify({"error": "Too many requests. Please slow down."})
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(retry_after)
                    return resp
                bucket.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_api_key(fn):
    """If RECLIP_API_KEY is configured, require a matching X-API-Key header."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if API_KEY:
            supplied = request.headers.get("X-API-Key", "")
            if not supplied or not hmac.compare_digest(supplied, API_KEY):
                return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _same_origin(value):
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.netloc) and parsed.netloc == request.host


@app.before_request
def csrf_protect():
    """Block cross-site state-changing requests.

    reclip has no login/session, so the real risk isn't classic session
    CSRF — it's a third-party web page silently POSTing to a user's
    locally-bound reclip instance (e.g. http://localhost:8899/api/download)
    to trigger downloads/SSRF-style requests on their behalf. Browsers
    always send Origin (and usually Referer) on cross-origin fetch/POST,
    so rejecting mismatches blocks that path while leaving same-origin
    page usage and non-browser API clients (curl, scripts) unaffected.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if not request.path.startswith("/api/"):
        return None

    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        # No browser-supplied Origin/Referer: non-browser client. Rely on
        # the API key / rate limiting below rather than blocking it.
        return None

    if not _same_origin(source):
        return jsonify({"error": "Cross-site request blocked"}), 403
    return None


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
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = ["yt-dlp", "--no-playlist", "-o", out_template]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            job["status"] = "error"
            job["error"] = result.stderr.strip().split("\n")[-1]
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        job["status"] = "done"
        job["file"] = chosen
        ext = os.path.splitext(chosen)[1]
        title = job.get("title", "").strip()
        # Sanitize title for filename
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:100].strip()
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)
    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Download timed out (5 min limit)"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
@rate_limited(max_requests=30, window=60)
@require_api_key
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = parse_ytdlp_json(result.stdout)

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append({
                "id": f["format_id"],
                "label": f"{height}p",
                "height": height,
            })
        formats.sort(key=lambda x: x["height"], reverse=True)

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
@rate_limited(max_requests=30, window=60)
@require_api_key
def get_playlist_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--flat-playlist", "-J", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)
        entries = info.get("entries", [])
        urls = [entry.get("url") for entry in entries if entry.get("url")]
        return jsonify({"urls": urls})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching playlist info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
@rate_limited(max_requests=10, window=60)
@require_api_key
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
@require_api_key
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
    })


@app.route("/api/file/<job_id>")
@require_api_key
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
