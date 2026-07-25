from __future__ import annotations

import time
from pathlib import Path

import pytest

from reclip_asr.db import Database, InvalidJobState
from reclip_asr.profiles import PROFILES


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    instance = Database(tmp_path / "state" / "jobs.sqlite3", tmp_path / "data")
    instance.initialize()
    return instance


def test_profiles_match_product_contract() -> None:
    fast = PROFILES["fast"]
    standard = PROFILES["standard"]
    maximum = PROFILES["maximum"]

    assert (fast.model, fast.beam_size, fast.enhance, fast.baseline) == (
        "medium",
        1,
        False,
        False,
    )
    assert (standard.model, standard.beam_size, standard.enhance, standard.baseline) == (
        "large-v3",
        5,
        True,
        False,
    )
    assert (maximum.model, maximum.beam_size, maximum.enhance, maximum.baseline) == (
        "large-v3",
        5,
        True,
        True,
    )
    assert all(profile.preparation_sample_rate == 48_000 for profile in PROFILES.values())
    assert all(profile.whisper_sample_rate == 16_000 for profile in PROFILES.values())


def test_sqlite_uses_wal_and_queue_positions_are_stable(database: Database) -> None:
    first = database.create_job("url", "standard", source_url="https://example.com/one")
    second = database.create_job("url", "fast", source_url="https://example.com/two")

    assert database.get_job(first["id"])["queue_position"] == 1
    assert database.get_job(second["id"])["queue_position"] == 2
    with database._connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_claim_is_serial_and_stale_active_job_is_recovered(database: Database) -> None:
    first = database.create_job("url", "standard", source_url="https://example.com/one")
    database.create_job("url", "standard", source_url="https://example.com/two")

    claimed = database.claim_next_job("worker-a")
    assert claimed is not None
    assert claimed["id"] == first["id"]
    assert claimed["status"] == "acquiring"
    assert database.claim_next_job("worker-b") is None

    time.sleep(0.01)
    recovered = database.claim_next_job("worker-b", stale_after_seconds=0)
    assert recovered is not None
    assert recovered["id"] == first["id"]
    assert recovered["status"] == "acquiring"
    assert recovered["claimed_by"] == "worker-b"


def test_worker_never_claims_http_owned_uploading_job(database: Database) -> None:
    uploading = database.create_job(
        "upload", "standard", source_name="large.wav", status="uploading"
    )
    queued = database.create_job(
        "url", "standard", source_url="https://example.com/ready"
    )

    time.sleep(0.01)
    claimed = database.claim_next_job("worker", stale_after_seconds=0)

    assert claimed is not None
    assert claimed["id"] == queued["id"]
    assert database.get_job(uploading["id"])["status"] == "uploading"


def test_cancel_retry_and_progress_percent(database: Database) -> None:
    job = database.create_job("url", "standard", source_url="https://example.com/media")
    canceled = database.request_cancel(job["id"])
    assert canceled["status"] == "canceled"
    assert canceled["cancel_requested"] is True

    retried = database.retry_job(job["id"])
    assert retried["id"] == job["id"]
    assert retried["attempt"] == 1
    assert retried["status"] == "queued"
    with pytest.raises(InvalidJobState):
        database.retry_job(job["id"])

    claimed = database.claim_next_job("worker")
    progressed = database.transition_job(
        job["id"], "transcribing", worker_id="worker", progress=75
    )
    assert claimed is not None
    assert progressed["progress"] == 75
    completed = database.mark_completed(job["id"], worker_id="worker")
    assert completed["progress"] == 100


def test_registered_artifacts_are_constrained_and_checksummed(database: Database) -> None:
    job = database.create_job("upload", "fast", source_name="sample.wav")
    source = Path(job["job_dir"]) / "source.wav"
    source.write_bytes(b"RIFF-audio")

    artifact = database.register_artifact(job["id"], "source", source)
    assert artifact["size"] == len(b"RIFF-audio")
    assert database.verify_artifact(job["id"], "source") is True
    with pytest.raises(ValueError):
        database.register_artifact(job["id"], "escape", database.data_dir / "outside.wav")

    database.record_stage_checkpoint(
        job["id"], "probing", duration_seconds=0.2, details={"artifacts": ["source"]}
    )
    assert database.last_recoverable_stage(job["id"]) == "probing"
    source.write_bytes(b"corrupted")
    assert database.verify_artifact(job["id"], "source") is False
    assert database.last_recoverable_stage(job["id"]) is None



def test_cancel_during_upload_finalization_becomes_terminal(database: Database) -> None:
    job = database.create_job("upload", "fast", source_name="sample.wav", status="uploading")
    canceling = database.request_cancel(job["id"])
    assert canceling["status"] == "uploading"
    assert canceling["cancel_requested"] is True

    finalized = database.transition_job(job["id"], "queued")
    assert finalized["status"] == "canceled"
    assert finalized["cancel_requested"] is True
    assert database.claim_next_job("worker") is None
    assert database.retry_job(job["id"])["status"] == "queued"


def test_web_restart_recovers_interrupted_uploads(database: Database) -> None:
    abandoned = database.create_job("upload", "standard", source_name="partial.wav", status="uploading")
    canceled = database.create_job("upload", "fast", source_name="canceled.wav", status="uploading")
    database.request_cancel(canceled["id"])

    assert database.recover_interrupted_uploads() == 2
    failed_job = database.get_job(abandoned["id"])
    canceled_job = database.get_job(canceled["id"])
    assert failed_job["status"] == "failed"
    assert "interrupted" in failed_job["error"].lower()
    assert canceled_job["status"] == "canceled"
