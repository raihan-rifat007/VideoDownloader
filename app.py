import os
import uuid
import threading
from flask import Flask, request, jsonify, send_file, render_template

import core

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

jobs = {}


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    try:
        kind = "audio" if format_choice == "audio" else "video"
        result = core.download(url, kind=kind, format_id=format_id,
                               output_dir=DOWNLOAD_DIR, name=job.get("title"))
        job["status"] = "done"
        job["file"] = result["file"]
        job["filename"] = os.path.basename(result["file"])
    except Exception as e:  # noqa: BLE001 - surface unexpected failures to the UI
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    try:
        return jsonify(core.probe(url))
    except core.ReclipError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/playlist", methods=["POST"])
def get_playlist_info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    try:
        return jsonify({"urls": core.expand_playlist(url)})
    except core.ReclipError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}
    thread = threading.Thread(target=run_download,
                              args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"status": job["status"], "error": job.get("error"),
                    "filename": job.get("filename")})


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
