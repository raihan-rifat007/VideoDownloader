from __future__ import annotations

import json
import os
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from reclip_asr.enhance_cli import chunk_ranges
from reclip_asr.db import Database
from reclip_asr.pipeline import (
    LARGE_V3_REVISION,
    MEDIUM_REVISION,
    Pipeline,
    PipelineError,
    ProcessResult,
    resolve_profile,
    validate_transcript_outputs,
)
from reclip_asr.transcribe_cli import serialize_segments, timestamp
from reclip_asr.worker import GPUStatus, healthcheck, write_health


def write_wav(path: Path, sample_rate: int = 48_000, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, round(sample_rate * seconds))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\0\0" * frames)


class MemoryDatabase:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.checkpoints: list[tuple[str, str, float, dict]] = []
        self.cancel_requested = False

    def update_job(self, job_id: str, **fields) -> None:
        self.jobs.setdefault(job_id, {}).update(fields)

    def register_artifact(
        self,
        job_id: str,
        key: str,
        path: str,
        *,
        filename: str | None = None,
        media_type: str | None = None,
        sha256: str | None = None,
        size: int | None = None,
    ) -> None:
        self.artifacts[key] = {
            "job_id": job_id,
            "path": path,
            "filename": filename,
            "media_type": media_type,
            "sha256": sha256,
            "size": size,
        }

    def is_cancel_requested(self, _job_id: str) -> bool:
        return self.cancel_requested

    def heartbeat_job(self, _job_id: str, _worker_id: str | None = None) -> bool:
        return True

    def record_stage_checkpoint(
        self,
        job_id: str,
        stage: str,
        *,
        duration_seconds: float | None = None,
        details: dict | None = None,
    ) -> None:
        self.checkpoints.append((job_id, stage, duration_seconds or 0.0, details or {}))


class SimulatedPipeline(Pipeline):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commands: list[list[str]] = []

    def _execute(self, command, timeout):  # noqa: ARG002 - test seam
        command = [str(item) for item in command]
        self.commands.append(command)
        if command[0] == "ffprobe":
            return ProcessResult(
                0,
                json.dumps(
                    {
                        "streams": [{"codec_type": "audio", "duration": "1.0"}],
                        "format": {"duration": "1.0"},
                    }
                ),
                "",
            )
        if command[0] == "ffmpeg":
            rate = int(command[command.index("-ar") + 1])
            input_path = Path(command[command.index("-i") + 1])
            with wave.open(str(input_path), "rb") as stream:
                seconds = stream.getnframes() / stream.getframerate()
            write_wav(Path(command[-1]), rate, seconds=seconds)
            return ProcessResult(0)
        if "reclip_asr.enhance_cli" in command:
            output = Path(command[command.index("--output") + 1])
            write_wav(output, 48_000)
            return ProcessResult(0)
        if "reclip_asr.transcribe_cli" in command:
            output_dir = Path(command[command.index("--output-dir") + 1])
            basename = command[command.index("--basename") + 1]
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{basename}_transcript.txt").write_text(
                "[00:00:00.000 --> 00:00:00.800] hello\n", encoding="utf-8"
            )
            (output_dir / f"{basename}_segments.jsonl").write_text(
                json.dumps({"id": 0, "start": 0.0, "end": 0.8, "text": "hello"}) + "\n",
                encoding="utf-8",
            )
            (output_dir / f"{basename}_asr_metadata.json").write_text(
                json.dumps(
                    {
                        "segment_count": 1,
                        "duration_seconds": 1.0,
                        "duration_after_vad_seconds": 0.8,
                        "detected_language": "en",
                        "language_probability": 0.99,
                    }
                ),
                encoding="utf-8",
            )
            return ProcessResult(0)
        raise AssertionError(f"Unexpected command: {command}")


@pytest.mark.parametrize(
    ("name", "model", "beam", "enhance", "baseline"),
    [
        ("fast", "medium", 1, False, False),
        ("standard", "large-v3", 5, True, False),
        ("maximum", "large-v3", 5, True, True),
    ],
)
def test_profiles_are_shared_and_deterministic(name, model, beam, enhance, baseline):
    profile = resolve_profile(name)
    assert (profile.model, profile.beam_size, profile.enhance, profile.baseline) == (
        model,
        beam,
        enhance,
        baseline,
    )
    assert len(MEDIUM_REVISION) == len(LARGE_V3_REVISION) == 40


def test_segment_serialization_is_timestamped_jsonl():
    segment = SimpleNamespace(
        start=1.2345,
        end=2.3456,
        text="  hello   world ",
        avg_logprob=-0.2,
        compression_ratio=1.1,
        no_speech_prob=0.01,
        words=None,
    )
    transcript, jsonl, count = serialize_segments([segment])
    assert timestamp(3661.005) == "01:01:01.005"
    assert transcript == "[00:00:01.234 --> 00:00:02.346] hello world\n"
    assert json.loads(jsonl)["text"] == "hello world"
    assert count == 1


def test_deepfilter_chunk_ranges_have_half_second_overlap():
    ranges = chunk_ranges(130 * 48_000, 60 * 48_000, int(0.5 * 48_000))
    assert ranges[0] == (0, 60 * 48_000)
    assert ranges[1][0] == ranges[0][1] - int(0.5 * 48_000)
    assert ranges[-1][1] == 130 * 48_000


def test_output_validation_rejects_non_monotonic_segments(tmp_path):
    transcript = tmp_path / "transcript.txt"
    segments = tmp_path / "segments.jsonl"
    metadata = tmp_path / "metadata.json"
    transcript.write_text("speech\n", encoding="utf-8")
    segments.write_text(
        json.dumps({"start": 1.0, "end": 2.0, "text": "one"})
        + "\n"
        + json.dumps({"start": 0.5, "end": 2.5, "text": "two"})
        + "\n",
        encoding="utf-8",
    )
    metadata.write_text(
        json.dumps({"segment_count": 2, "duration_seconds": 3, "detected_language": "en"}),
        encoding="utf-8",
    )
    with pytest.raises(PipelineError, match="Non-monotonic"):
        validate_transcript_outputs(transcript, segments, metadata, 3.0)


@pytest.mark.parametrize(
    ("profile", "enhance_count", "transcribe_count"),
    [("fast", 0, 1), ("standard", 1, 1), ("maximum", 1, 2)],
)
def test_pipeline_profiles_and_restart_reuse(tmp_path, profile, enhance_count, transcribe_count):
    data_root = tmp_path / "data"
    job_dir = data_root / "jobs" / f"job-{profile}"
    source = job_dir / "source" / "recording.wav"
    write_wav(source)
    database = MemoryDatabase()
    job = {
        "id": f"job-{profile}",
        "source_type": "upload",
        "source_name": "recording.wav",
        "source_path": str(source),
        "profile": profile,
        "requested_language": None,
        "job_dir": str(job_dir),
    }
    pipeline = SimulatedPipeline(
        database,
        data_root=data_root,
        models_dir=tmp_path / "models",
        deepfilter_cache=tmp_path / "cache",
        device="cpu",
        worker_id="test-worker",
        poll_seconds=0.01,
    )
    metadata = pipeline.run(job)
    assert metadata is not None
    assert database.jobs[job["id"]]["status"] == "completed"
    assert metadata["profile"] == profile
    assert (job_dir / "transcripts" / "transcript.txt").is_file()
    assert (job_dir / "checksums.json").is_file()
    assert sum("reclip_asr.enhance_cli" in command for command in pipeline.commands) == enhance_count
    assert sum("reclip_asr.transcribe_cli" in command for command in pipeline.commands) == transcribe_count
    assert all(Path(item["path"]).is_absolute() for item in database.artifacts.values())

    resumed = SimulatedPipeline(
        database,
        data_root=data_root,
        models_dir=tmp_path / "models",
        deepfilter_cache=tmp_path / "cache",
        device="cpu",
        worker_id="test-worker",
    )
    assert resumed.run(job) is not None
    assert resumed.commands == []


def test_cancellation_stops_before_media_commands(tmp_path):
    data_root = tmp_path / "data"
    job_dir = data_root / "jobs" / "cancel-me"
    source = job_dir / "source" / "recording.wav"
    write_wav(source)
    database = MemoryDatabase()
    database.cancel_requested = True
    pipeline = SimulatedPipeline(database, data_root=data_root, device="cpu", worker_id="worker")
    result = pipeline.run(
        {
            "id": "cancel-me",
            "source_type": "upload",
            "source_path": str(source),
            "profile": "fast",
            "job_dir": str(job_dir),
        }
    )
    assert result is None
    assert pipeline.commands == []
    assert database.jobs["cancel-me"]["status"] == "canceled"


def test_pipeline_uses_durable_database_contract(tmp_path):
    data_root = tmp_path / "data"
    database = Database(data_root / "queue.sqlite3", data_root)
    job = database.create_job(
        "upload", "fast", source_name="recording.wav", status="uploading"
    )
    source = Path(job["job_dir"]) / "source" / "recording.wav"
    write_wav(source)
    database.set_source_path(job["id"], str(source))
    database.register_artifact(job["id"], "source", source)
    database.transition_job(job["id"], "queued", progress=0)
    claimed = database.claim_next_job("test-worker")
    assert claimed is not None

    pipeline = SimulatedPipeline(
        database,
        data_root=data_root,
        models_dir=tmp_path / "models",
        deepfilter_cache=tmp_path / "cache",
        device="cpu",
        worker_id="test-worker",
    )
    assert pipeline.run(claimed) is not None
    completed = database.get_job(job["id"])
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert database.verify_artifact(job["id"], "metadata")
    assert database.verify_artifact(job["id"], "checksums")


def test_healthcheck_accepts_recent_unavailable_heartbeat(tmp_path, monkeypatch):
    heartbeat_path = tmp_path / "heartbeat.json"
    monkeypatch.setenv("ASR_HEARTBEAT_FILE", str(heartbeat_path))
    write_health(
        "worker",
        "unavailable",
        "cuda",
        GPUStatus(False, reason="GPU unavailable"),
        None,
    )
    assert healthcheck(max_age_seconds=30) == 0
    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    payload["updated_at_epoch"] = time.time() - 60
    heartbeat_path.write_text(json.dumps(payload), encoding="utf-8")
    assert healthcheck(max_age_seconds=30) == 1


def test_changed_source_invalidates_all_downstream_recovery(tmp_path):
    data_root = tmp_path / "data"
    job_dir = data_root / "jobs" / "changed-source"
    source = job_dir / "source" / "recording.wav"
    write_wav(source, seconds=1.0)
    database = MemoryDatabase()
    job = {
        "id": "changed-source",
        "source_type": "upload",
        "source_name": "recording.wav",
        "source_path": str(source),
        "profile": "fast",
        "job_dir": str(job_dir),
    }
    first = SimulatedPipeline(
        database,
        data_root=data_root,
        models_dir=tmp_path / "models",
        deepfilter_cache=tmp_path / "cache",
        device="cpu",
        worker_id="test-worker",
    )
    assert first.run(job) is not None
    old_manifest = json.loads((job_dir / "checksums.json").read_text(encoding="utf-8"))
    old_source_sha = old_manifest["artifacts"]["source"]["sha256"]

    write_wav(source, seconds=1.25)
    resumed = SimulatedPipeline(
        database,
        data_root=data_root,
        models_dir=tmp_path / "models",
        deepfilter_cache=tmp_path / "cache",
        device="cpu",
        worker_id="test-worker",
    )
    assert resumed.run(job) is not None
    new_manifest = json.loads((job_dir / "checksums.json").read_text(encoding="utf-8"))
    assert new_manifest["artifacts"]["source"]["sha256"] != old_source_sha
    assert any(command[0] == "ffprobe" for command in resumed.commands)
    assert sum(command[0] == "ffmpeg" for command in resumed.commands) == 2
    assert sum("reclip_asr.transcribe_cli" in command for command in resumed.commands) == 1
