import os
import uuid
import glob
import json
import subprocess
import threading
import time
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, render_template

from download_process import run_streaming_process
from progress import normalize_download_progress, parse_progress_line

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

jobs = {}
jobs_lock = threading.RLock()
DOWNLOAD_TIMEOUT = 300

DOWNLOAD_PROGRESS_TEMPLATE = (
    "download:RECLIP_PROGRESS "
    "%(progress.{status,downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta})j"
)
POSTPROCESS_PROGRESS_TEMPLATE = (
    "postprocess:RECLIP_POSTPROCESS "
    "%(progress.{status,postprocessor})j"
)


def _empty_progress():
    return normalize_download_progress({}, now=None)


def apply_progress_event(job_id, event, now=None):
    """Apply one parsed event without changing a terminal job."""
    if now is None:
        now = time.time()

    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job["status"] in ("done", "error"):
            return

        data = event.get("data", {}) if isinstance(event, dict) else {}
        if not isinstance(data, dict):
            return

        if event.get("kind") == "download":
            if data.get("status") == "downloading":
                job["phase"] = "downloading"
                job["progress"] = normalize_download_progress(data, now)
            elif data.get("status") == "finished":
                progress = normalize_download_progress(data, now)
                progress["speed_bps"] = None
                progress["eta_seconds"] = None
                job["phase"] = "finalizing"
                job["progress"] = progress
        elif event.get("kind") == "postprocess":
            job["phase"] = "processing"
            job["progress"] = None


def _mark_job_error(job_id, message):
    with jobs_lock:
        job = jobs.get(job_id)
        if job and job["status"] not in ("done", "error"):
            job["status"] = "error"
            job["phase"] = "failed"
            job["error"] = message
            job["progress"] = None


def is_safe_url(url):
    """Reject anything that isn't a plain http(s) URL.

    This also blocks strings starting with ``-``/``--`` which yt-dlp would
    otherwise parse as CLI options (e.g. ``--exec``), letting a caller
    smuggle arbitrary flags into the subprocess invocation.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


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
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--progress",
        "--progress-delta",
        "0.5",
        "--progress-template",
        DOWNLOAD_PROGRESS_TEMPLATE,
        "--progress-template",
        POSTPROCESS_PROGRESS_TEMPLATE,
        "-o",
        out_template,
    ]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    # "--" stops yt-dlp from treating a URL that begins with "-" as an
    # option (e.g. "--exec=..."), which would otherwise allow arbitrary
    # command execution.
    cmd += ["--", url]

    last_error_lines = []

    def handle_line(line):
        event = parse_progress_line(line)
        if event is not None:
            apply_progress_event(job_id, event)
            return

        if line.startswith("ERROR:") or line.startswith("WARNING:"):
            last_error_lines.append(line[:1000])
            del last_error_lines[:-20]

    try:
        with jobs_lock:
            if job_id not in jobs:
                return
            jobs[job_id]["phase"] = "preparing"

        returncode = run_streaming_process(
            cmd,
            handle_line,
            timeout_seconds=DOWNLOAD_TIMEOUT,
        )
        if returncode != 0:
            message = last_error_lines[-1] if last_error_lines else f"yt-dlp exited with code {returncode}"
            _mark_job_error(job_id, message.replace("ERROR: ", ""))
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            _mark_job_error(job_id, "Download completed but no file was found")
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

        ext = os.path.splitext(chosen)[1]
        # Sanitize title for filename
        with jobs_lock:
            job = jobs.get(job_id)
            if not job or job["status"] == "error":
                return
            title = job.get("title", "").strip()
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:100].strip()
            filename = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            filename = os.path.basename(chosen)
        with jobs_lock:
            job = jobs.get(job_id)
            if not job or job["status"] == "error":
                return
            complete_progress = _empty_progress()
            complete_progress["percent"] = 100.0
            job.update(
                {
                    "status": "done",
                    "phase": "complete",
                    "progress": complete_progress,
                    "file": chosen,
                    "filename": filename,
                }
            )
    except subprocess.TimeoutExpired:
        _mark_job_error(job_id, "Download timed out (5 min limit)")
        # The process runner has already terminated the child process tree.
        for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*")):
            try:
                os.remove(f)
            except OSError:
                pass
    except Exception as e:
        _mark_job_error(job_id, str(e))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not is_safe_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    cmd = ["yt-dlp", "--no-playlist", "-j", "--", url]
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
def get_playlist_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not is_safe_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    cmd = ["yt-dlp", "--flat-playlist", "-J", "--", url]
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
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not is_safe_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    job_id = uuid.uuid4().hex[:10]
    with jobs_lock:
        jobs[job_id] = {
            "status": "downloading",
            "phase": "preparing",
            "progress": _empty_progress(),
            "url": url,
            "title": title,
        }

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({
            "status": job["status"],
            "error": job.get("error"),
            "filename": job.get("filename"),
            "phase": job.get("phase"),
            "progress": job.get("progress"),
        })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job["status"] != "done":
            return jsonify({"error": "File not ready"}), 404
        file_path = job["file"]
        filename = job["filename"]
    return send_file(file_path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
