from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from flask import Flask

from reclip_asr.api import init_asr


@pytest.fixture()
def app(tmp_path: Path) -> Flask:
    instance = Flask(__name__)
    instance.config.update(
        TESTING=True,
        DATA_DIR=str(tmp_path / "data"),
        ASR_DB_PATH=str(tmp_path / "data" / "reclip.sqlite3"),
        MAX_UPLOAD_BYTES=1024 * 1024,
        MAX_CONTENT_LENGTH=1024 * 1024,
        WORKER_STALE_SECONDS=30,
        MAX_URL_BATCH=10,
    )
    init_asr(instance)
    return instance


@pytest.fixture()
def client(app: Flask):
    return app.test_client()


def _database(app: Flask):
    # The extension is created lazily by the first ASR request.
    return app.extensions["reclip_asr_database"]


def test_url_batch_creation_listing_and_private_url_rejection(client) -> None:
    response = client.post(
        "/api/transcriptions/url",
        json={
            "urls": ["https://example.com/one", "https://example.com/two"],
            "profile": "maximum",
            "language": "en",
        },
    )
    assert response.status_code == 201
    jobs = response.get_json()["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["profile"] == "maximum"
    assert jobs[0]["requested_language"] == "en"
    assert jobs[0]["queue_position"] == 1

    listing = client.get("/api/transcriptions?limit=1&offset=0")
    assert listing.status_code == 200
    payload = listing.get_json()
    assert payload["total"] == 2
    assert len(payload["jobs"]) == 1

    rejected = client.post(
        "/api/transcriptions/url", json={"url": "http://127.0.0.1/private"}
    )
    assert rejected.status_code == 400
    assert "private" in rejected.get_json()["error"].lower()


def test_streamed_upload_registers_source_without_exposing_paths(app: Flask, client) -> None:
    response = client.post(
        "/api/transcriptions/upload",
        data={
            "file": (BytesIO(b"fake-media-bytes"), "..\\unsafe?.wav"),
            "profile": "fast",
            "language": "auto",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    job = response.get_json()["job"]
    assert job["status"] == "queued"
    assert job["source_name"] == "unsafe.wav"

    detail = client.get(f"/api/transcriptions/{job['id']}")
    assert detail.status_code == 200
    payload = detail.get_json()
    source = payload["artifacts"]["source"]
    assert source["size"] == len(b"fake-media-bytes")
    assert "path" not in source
    assert "job_dir" not in payload

    download = client.get(source["url"])
    assert download.status_code == 200
    assert download.data == b"fake-media-bytes"
    assert "attachment" in download.headers["Content-Disposition"]
    assert client.get(f"/api/transcriptions/{job['id']}/artifacts/not_registered").status_code == 404


def test_transcript_inline_view_actions_and_delete(app: Flask, client) -> None:
    created = client.post(
        "/api/transcriptions/url", json={"url": "https://example.com/speech"}
    ).get_json()["jobs"][0]
    database = _database(app)
    stored = database.get_job(created["id"])
    transcript = Path(stored["job_dir"]) / "speech_transcript.txt"
    transcript.write_text("[00:00:00 - 00:00:01] Hello\n", encoding="utf-8")
    database.register_artifact(
        created["id"], "transcript_txt", transcript, media_type="text/plain"
    )

    inline = client.get(f"/api/transcriptions/{created['id']}/transcript")
    assert inline.status_code == 200
    assert b"Hello" in inline.data
    inline.close()
    artifact_inline = client.get(
        f"/api/transcriptions/{created['id']}/artifacts/transcript_txt?inline=1"
    )
    assert artifact_inline.status_code == 200
    assert "attachment" not in artifact_inline.headers.get("Content-Disposition", "")
    artifact_inline.close()

    canceled = client.post(f"/api/transcriptions/{created['id']}/cancel")
    assert canceled.status_code == 200
    assert canceled.get_json()["job"]["status"] == "canceled"
    retried = client.post(f"/api/transcriptions/{created['id']}/retry")
    assert retried.status_code == 200
    assert retried.get_json()["job"]["id"] == created["id"]
    assert retried.get_json()["job"]["attempt"] == 1

    deleted = client.delete(f"/api/transcriptions/{created['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/transcriptions/{created['id']}").status_code == 404


def test_system_status_reports_worker_and_exact_profiles(app: Flask, client) -> None:
    unavailable = client.get("/api/system/asr")
    assert unavailable.status_code == 200
    payload = unavailable.get_json()
    assert payload["available"] is False
    assert payload["defaults"]["profile"] == "standard"
    assert payload["profiles"]["fast"]["model"] == "medium"
    assert payload["profiles"]["standard"]["enhance"] is True
    assert payload["profiles"]["maximum"]["baseline"] is True

    database = _database(app)
    database.heartbeat_worker(
        "test-worker",
        status="idle",
        gpu_available=True,
        gpu_name="Test GPU",
        details={"device": "cuda", "cache": {"models": "ready"}},
    )
    available = client.get("/api/system/asr").get_json()
    assert available["available"] is True
    assert available["worker"]["gpu_name"] == "Test GPU"
    assert available["cache"] == {"models": "ready"}


def test_upload_size_limit_returns_json_413(client) -> None:
    response = client.post(
        "/api/transcriptions/upload",
        data={"file": (BytesIO(b"x" * (1024 * 1024 + 1)), "too-big.wav")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    assert "limit" in response.get_json()["error"].lower()



def test_delete_failure_keeps_history_record(app: Flask, client, monkeypatch) -> None:
    created = client.post(
        "/api/transcriptions/url",
        json={"url": "https://example.com/delete-me", "profile": "fast"},
    ).get_json()["jobs"][0]
    database = _database(app)
    database.mark_failed(created["id"], "test failure")

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("locked artifact")

    monkeypatch.setattr("reclip_asr.api.shutil.rmtree", fail_cleanup)
    with pytest.raises(OSError, match="locked artifact"):
        client.delete(f"/api/transcriptions/{created['id']}")
    assert client.get(f"/api/transcriptions/{created['id']}").status_code == 200
