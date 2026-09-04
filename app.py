import os
import uuid
import glob
import json
import subprocess
import threading
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

jobs = {}

VIDEO_EXPORT_FORMATS = frozenset({"mp4", "mkv", "mov"})
SUPPORTED_EXPORT_FORMATS = VIDEO_EXPORT_FORMATS | {"mp3"}
LEGACY_EXPORT_FORMATS = {"video": "mp4", "audio": "mp3"}
YTDLP_RUNTIME_OPTIONS = (
    ("YTDLP_COOKIES_FILE", "--cookies"),
    ("YTDLP_PROXY", "--proxy"),
    ("YTDLP_USER_AGENT", "--user-agent"),
)


def normalize_export_format(value):
    """Return a supported export format, including legacy API aliases."""
    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    normalized = LEGACY_EXPORT_FORMATS.get(normalized, normalized)
    return normalized if normalized in SUPPORTED_EXPORT_FORMATS else None


def build_ytdlp_base_command(no_playlist=False):
    """Build shared yt-dlp arguments from trusted deployment settings."""
    cmd = ["yt-dlp"]
    for environment_name, option in YTDLP_RUNTIME_OPTIONS:
        value = os.environ.get(environment_name, "").strip()
        if value:
            cmd += [option, value]
    if no_playlist:
        cmd.append("--no-playlist")
    return cmd


def format_ytdlp_error(stderr):
    """Return a concise error, with deployment guidance for Bilibili 412."""
    stderr = (stderr or "").strip()
    if "[BiliBili]" in stderr and "HTTP Error 412" in stderr:
        return (
            "Bilibili blocked this server (HTTP 412). Mount a fresh browser "
            "cookies.txt via YTDLP_COOKIES_FILE and/or configure YTDLP_PROXY, "
            "then retry."
        )
    return (
        stderr.splitlines()[-1]
        if stderr
        else "yt-dlp failed without an error message"
    )


def build_download_command(url, export_format, format_id, out_template):
    """Build a yt-dlp command that produces the requested container."""
    cmd = build_ytdlp_base_command(no_playlist=True)
    cmd += ["-o", out_template]

    if export_format == "mp3":
        cmd += ["-x", "--audio-format", "mp3"]
    else:
        selector = (
            f"{format_id}+bestaudio/best"
            if format_id
            else "bestvideo+bestaudio/best"
        )
        cmd += [
            "-f",
            selector,
            "--merge-output-format",
            export_format,
            "--recode-video",
            export_format,
        ]

    cmd.append(url)
    return cmd


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


def run_download(job_id, url, export_format, format_id):
    job = jobs[job_id]
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")
    cmd = build_download_command(url, export_format, format_id, out_template)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            job["status"] = "error"
            job["error"] = format_ytdlp_error(result.stderr)
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        target = [
            f
            for f in files
            if os.path.splitext(f)[1].lower() == f".{export_format}"
        ]
        if not target:
            job["status"] = "error"
            job["error"] = (
                "Download completed but no "
                f"{export_format.upper()} file was produced"
            )
            return
        chosen = target[0]

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
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = build_ytdlp_base_command(no_playlist=True) + ["-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": format_ytdlp_error(result.stderr)}), 400

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

    cmd = build_ytdlp_base_command() + ["--flat-playlist", "-J", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": format_ytdlp_error(result.stderr)}), 400

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
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    export_format = normalize_export_format(data.get("format", "mp4"))
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if export_format is None:
        supported = ", ".join(sorted(SUPPORTED_EXPORT_FORMATS))
        return jsonify({
            "error": f"Unsupported export format. Choose one of: {supported}"
        }), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}

    thread = threading.Thread(
        target=run_download,
        args=(job_id, url, export_format, format_id),
    )
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
        "filename": job.get("filename"),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
