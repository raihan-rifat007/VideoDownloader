"""Flask routes for durable URL and upload transcription jobs."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from flask import Blueprint, Flask, current_app, jsonify, request, send_file, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from .db import (
    ACTIVE_STAGES,
    Database,
    InvalidJobState,
    JobConflict,
    JobNotFound,
)
from .profiles import DEFAULT_PROFILE, get_profile, profiles_payload
from .utils import (
    is_path_within,
    normalize_language,
    normalize_uuid,
    sanitize_filename,
    validate_public_url,
)


asr_api = Blueprint("asr_api", __name__)


def _database() -> Database:
    """Return a database matching the app's current config (useful in tests)."""

    data_dir = str(current_app.config["DATA_DIR"])
    db_path = str(current_app.config["ASR_DB_PATH"])
    cache_key = (str(Path(db_path).resolve()), str(Path(data_dir).resolve()))
    cached = current_app.extensions.get("reclip_asr_database")
    if cached is None or current_app.extensions.get("reclip_asr_database_key") != cache_key:
        cached = Database(db_path, data_dir)
        cached.recover_interrupted_uploads()
        current_app.extensions["reclip_asr_database"] = cached
        current_app.extensions["reclip_asr_database_key"] = cache_key
    return cached


def _int_query(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw in {None, ""}:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(minimum, min(value, maximum))


def _source_name(url: str) -> str:
    parsed = urlsplit(url)
    tail = Path(unquote(parsed.path)).name
    return (tail or parsed.hostname or "online media")[:240]


def _public_job(job: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    result = {
        "id": job["id"],
        "source_type": job["source_type"],
        "source_url": job.get("source_url"),
        "source_name": job.get("source_name"),
        "profile": job["profile"],
        "requested_language": job.get("requested_language"),
        "status": job["status"],
        "progress": job["progress"],
        "cancel_requested": job["cancel_requested"],
        "error": job.get("error"),
        "attempt": job.get("attempt", 0),
        "queue_position": job.get("queue_position"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "actions": {
            "cancel": job["status"] == "queued" or job["status"] in ACTIVE_STAGES,
            "retry": job["status"] in {"failed", "canceled"},
            "delete": job["status"] not in ACTIVE_STAGES,
        },
    }
    if detail:
        result["metadata"] = job.get("metadata", {})
    return result


def _public_artifact(job_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
    download_url = url_for(
        "asr_api.serve_transcription_artifact",
        job_id=job_id,
        key=artifact["key"],
        download=1,
    )
    inline_url = url_for(
        "asr_api.serve_transcription_artifact",
        job_id=job_id,
        key=artifact["key"],
        inline=1,
    )
    return {
        "key": artifact["key"],
        "filename": artifact["filename"],
        "media_type": artifact["media_type"],
        "size": artifact["size"],
        "sha256": artifact["sha256"],
        "created_at": artifact["created_at"],
        "url": download_url,
        "inline_url": inline_url,
    }


def _job_detail(database: Database, job_id: str) -> dict[str, Any]:
    job = database.get_job(job_id)
    result = _public_job(job, detail=True)
    result["stage_checkpoints"] = database.get_stage_checkpoints(job_id)
    result["artifacts"] = {
        key: _public_artifact(job_id, artifact)
        for key, artifact in database.list_artifacts(job_id).items()
    }
    return result


@asr_api.errorhandler(JobNotFound)
def _not_found(error: JobNotFound):
    return jsonify({"error": str(error)}), 404


@asr_api.errorhandler(InvalidJobState)
@asr_api.errorhandler(JobConflict)
def _conflict(error: Exception):
    return jsonify({"error": str(error)}), 409


@asr_api.errorhandler(ValueError)
def _bad_request(error: ValueError):
    return jsonify({"error": str(error)}), 400


@asr_api.app_errorhandler(RequestEntityTooLarge)
def _too_large(_error: RequestEntityTooLarge):
    if request.path.startswith("/api/transcriptions"):
        maximum = int(current_app.config["MAX_UPLOAD_BYTES"])
        return jsonify({"error": f"Upload exceeds the {maximum}-byte limit"}), 413
    return jsonify({"error": "Request is too large"}), 413


@asr_api.get("/api/system/asr")
def asr_system_status():
    profiles = profiles_payload()
    defaults = {"profile": DEFAULT_PROFILE, "language": None}
    try:
        stale_after = float(current_app.config["WORKER_STALE_SECONDS"])
        status = _database().get_system_status(stale_after)
    except (OSError, RuntimeError) as exc:
        return jsonify(
            {
                "available": False,
                "reason": f"ASR storage is unavailable: {exc}",
                "worker": None,
                "queue": {"queued": 0, "active": 0},
                "profiles": profiles,
                "defaults": defaults,
            }
        )

    worker = status["worker"]
    available = False
    reason = None
    if worker is None or not worker.get("alive"):
        reason = "No live ASR worker heartbeat"
    elif worker.get("status") in {"disabled", "error", "failed", "unavailable"}:
        reason = worker.get("details", {}).get("reason") or worker.get("details", {}).get("error") or "ASR worker is unavailable"
    else:
        device = str(
            worker.get("details", {}).get("device")
            or current_app.config.get("WHISPER_DEVICE", "cuda")
        ).lower()
        available = bool(worker.get("gpu_available")) or device == "cpu"
        if not available:
            reason = "The ASR worker has no usable NVIDIA GPU"

    worker_profiles = (worker or {}).get("details", {}).get("profiles", {})
    for name, definition in profiles.items():
        state = worker_profiles.get(name) if isinstance(worker_profiles, dict) else None
        if isinstance(state, dict):
            definition["available"] = bool(state.get("available"))
            definition["reason"] = state.get("reason")
        else:
            definition["available"] = available
            definition["reason"] = None if available else reason
    if not any(definition.get("available") for definition in profiles.values()):
        available = False
    return jsonify(
        {
            "available": available,
            "reason": reason,
            "worker": worker,
            "queue": status["queue"],
            "profiles": profiles,
            "defaults": defaults,
            "cache": (worker or {}).get("details", {}).get("cache", {}),
        }
    )


@asr_api.post("/api/transcriptions/url")
def create_url_transcriptions():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("A JSON request body is required")
    raw_urls = body.get("urls", body.get("url"))
    if isinstance(raw_urls, str):
        urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]
    elif isinstance(raw_urls, list):
        urls = raw_urls
    else:
        raise ValueError("Provide 'url' or a list of 'urls'")
    if not urls:
        raise ValueError("At least one URL is required")
    if len(urls) > int(current_app.config["MAX_URL_BATCH"]):
        raise ValueError(f"A batch may contain at most {current_app.config['MAX_URL_BATCH']} URLs")

    validated_urls = [validate_public_url(value) for value in urls]
    profile = get_profile(body.get("profile")).name
    language = normalize_language(body.get("language", body.get("requested_language")))
    database = _database()
    jobs = [
        database.create_job(
            "url",
            profile,
            requested_language=language,
            source_url=url,
            source_name=_source_name(url),
        )
        for url in validated_urls
    ]
    return jsonify({"jobs": [_public_job(job) for job in jobs]}), 201


def _discard_failed_upload(database: Database, job_id: str, directory: Path, error: Exception) -> None:
    try:
        database.mark_failed(job_id, f"Upload failed: {error}")
        database.delete_job(job_id)
    except Exception:
        pass
    if directory.exists() and is_path_within(directory, database.data_dir / "jobs"):
        shutil.rmtree(directory, ignore_errors=True)


@asr_api.post("/api/transcriptions/upload")
def create_upload_transcription():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ValueError("A multipart file field named 'file' is required")
    profile = get_profile(request.form.get("profile")).name
    language = normalize_language(
        request.form.get("language", request.form.get("requested_language"))
    )
    filename = sanitize_filename(upload.filename)
    job_id = normalize_uuid()
    database = _database()
    job = database.create_job(
        "upload",
        profile,
        requested_language=language,
        source_name=filename,
        status="uploading",
        job_id=job_id,
    )
    directory = Path(job["job_dir"])
    source_dir = directory / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = source_dir / filename
    partial = source_dir / f".{filename}.part"
    maximum = int(current_app.config["MAX_UPLOAD_BYTES"])
    total = 0
    digest = hashlib.sha256()
    try:
        with partial.open("xb") as handle:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise RequestEntityTooLarge()
                digest.update(chunk)
                handle.write(chunk)
        if total == 0:
            raise ValueError("The uploaded file is empty")
        os.replace(partial, destination)
        database.set_source_path(job_id, str(destination))
        database.register_artifact(
            job_id,
            "source",
            destination,
            filename=filename,
            media_type=upload.mimetype or None,
            sha256=digest.hexdigest(),
            size=total,
        )
        job = database.transition_job(
            job_id,
            "queued",
            progress=0,
            metadata={
                "source": {
                    "filename": filename,
                    "size_bytes": total,
                    "sha256": digest.hexdigest(),
                }
            },
        )
    except Exception as exc:
        partial.unlink(missing_ok=True)
        _discard_failed_upload(database, job_id, directory, exc)
        raise
    return jsonify({"job": _public_job(job)}), 201


@asr_api.get("/api/transcriptions")
def list_transcriptions():
    limit = _int_query("limit", 50, 1, 200)
    offset = _int_query("offset", 0, 0, 1_000_000_000)
    jobs, total = _database().list_jobs(limit=limit, offset=offset)
    return jsonify(
        {
            "jobs": [_public_job(job) for job in jobs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@asr_api.get("/api/transcriptions/<uuid:job_id>")
def get_transcription(job_id):
    return jsonify(_job_detail(_database(), str(job_id)))


@asr_api.post("/api/transcriptions/<uuid:job_id>/cancel")
def cancel_transcription(job_id):
    job = _database().request_cancel(str(job_id))
    return jsonify({"job": _public_job(job)})


@asr_api.post("/api/transcriptions/<uuid:job_id>/retry")
def retry_transcription(job_id):
    job = _database().retry_job(str(job_id))
    return jsonify({"job": _public_job(job)})


@asr_api.delete("/api/transcriptions/<uuid:job_id>")
def delete_transcription(job_id):
    database = _database()
    job = database.prepare_job_deletion(str(job_id))
    directory = Path(job["job_dir"]).resolve()
    expected_parent = (database.data_dir / "jobs").resolve()
    if directory.parent != expected_parent or directory.name != str(job_id):
        raise RuntimeError("Refusing to remove an invalid job directory")
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=False)
    database.delete_job(str(job_id))
    return "", 204


def _inline_allowed(artifact: dict[str, Any]) -> bool:
    media_type = str(artifact.get("media_type") or "").split(";", 1)[0].strip().lower()
    return media_type in {
        "text/plain",
        "application/json",
        "application/jsonl",
        "application/x-ndjson",
    }


@asr_api.get("/api/transcriptions/<uuid:job_id>/artifacts/<key>")
def serve_transcription_artifact(job_id, key: str):
    database = _database()
    job = database.get_job(str(job_id), include_queue_position=False)
    artifact = database.get_artifact(str(job_id), key)
    path = Path(artifact["path"]).resolve()
    if not is_path_within(path, job["job_dir"]) or not path.is_file():
        raise JobNotFound(f"Artifact {key} is no longer available")
    wants_inline = request.args.get("inline") == "1" and _inline_allowed(artifact)
    as_attachment = request.args.get("download") == "1" or not wants_inline
    return send_file(
        path,
        as_attachment=as_attachment,
        download_name=artifact["filename"],
        mimetype=artifact["media_type"],
        conditional=True,
    )


@asr_api.get("/api/transcriptions/<uuid:job_id>/transcript")
def view_transcript(job_id):
    """Convenience read-only view for the primary timestamped transcript."""

    database = _database()
    artifacts = database.list_artifacts(str(job_id))
    preferred_keys = ("transcript_txt", "primary_transcript", "transcript")
    artifact = next((artifacts[key] for key in preferred_keys if key in artifacts), None)
    if artifact is None:
        artifact = next(
            (
                item
                for item in artifacts.values()
                if item["filename"].lower().endswith(".txt")
                and "transcript" in item["filename"].lower()
            ),
            None,
        )
    if artifact is None:
        raise JobNotFound("The primary transcript is not available")
    path = Path(artifact["path"]).resolve()
    job = database.get_job(str(job_id), include_queue_position=False)
    if not is_path_within(path, job["job_dir"]) or not path.is_file():
        raise JobNotFound("The primary transcript is no longer available")
    return send_file(path, mimetype="text/plain; charset=utf-8", conditional=True)


def init_asr(app: Flask) -> None:
    """Configure and register the ASR API without touching legacy ReClip routes."""

    data_dir = os.environ.get("DATA_DIR", "/data")
    app.config.setdefault("DATA_DIR", data_dir)
    app.config.setdefault(
        "ASR_DB_PATH", os.environ.get("ASR_DB_PATH", str(Path(data_dir) / "reclip.sqlite3"))
    )
    app.config.setdefault(
        "MAX_UPLOAD_BYTES", int(os.environ.get("MAX_UPLOAD_BYTES", "21474836480"))
    )
    app.config.setdefault(
        "WORKER_STALE_SECONDS", float(os.environ.get("WORKER_STALE_SECONDS", "30"))
    )
    app.config.setdefault("MAX_URL_BATCH", int(os.environ.get("MAX_URL_BATCH", "100")))
    if not app.config.get("MAX_CONTENT_LENGTH"):
        app.config["MAX_CONTENT_LENGTH"] = int(app.config["MAX_UPLOAD_BYTES"])
    if "asr_api" not in app.blueprints:
        app.register_blueprint(asr_api)
