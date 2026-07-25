"""Single-consumer ASR worker for the durable SQLite queue."""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database
from .pipeline import Pipeline, atomic_write_json


@dataclass(frozen=True)
class GPUStatus:
    available: bool
    name: str | None = None
    memory_total_mib: int | None = None
    driver_version: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RuntimeStatus:
    available: bool
    reason: str | None = None
    supported_compute_types: tuple[str, ...] = ()
    versions: dict[str, str | None] | None = None


def detect_gpu() -> GPUStatus:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GPUStatus(False, reason=f"nvidia-smi unavailable: {type(exc).__name__}")
    if result.returncode != 0 or not result.stdout.strip():
        return GPUStatus(False, reason="No NVIDIA GPU is visible inside the worker container")
    first = result.stdout.splitlines()[0]
    parts = [part.strip() for part in first.split(",", 2)]
    try:
        memory = int(parts[1]) if len(parts) > 1 else None
    except ValueError:
        memory = None
    return GPUStatus(
        True,
        name=parts[0] or None,
        memory_total_mib=memory,
        driver_version=parts[2] if len(parts) > 2 else None,
    )


def probe_runtime(device: str, compute_type: str) -> RuntimeStatus:
    """Probe the actual ASR runtime in a disposable child process."""

    environment = os.environ.copy()
    environment["ASR_PROBE_DEVICE"] = device
    environment["ASR_PROBE_COMPUTE_TYPE"] = compute_type
    code = r'''
import importlib.metadata as metadata
import json
import os
from pathlib import Path

import ctranslate2
import faster_whisper
import torch
import torchaudio
from df.enhance import enhance  # noqa: F401

device = os.environ["ASR_PROBE_DEVICE"]
compute_type = os.environ["ASR_PROBE_COMPUTE_TYPE"]
supported = sorted(ctranslate2.get_supported_compute_types(device))
if compute_type not in supported:
    raise RuntimeError(f"CTranslate2 does not support {compute_type} on {device}: {supported}")
if device == "cuda" and not torch.cuda.is_available():
    raise RuntimeError("PyTorch cannot use the requested CUDA device")
model_dir = Path(os.environ.get(
    "DEEPFILTER_MODEL_DIR", "/opt/deepfilter-models/DeepFilterNet3"
))
if not (model_dir / "config.ini").is_file() or not (model_dir / "checkpoints").is_dir():
    raise RuntimeError("The pinned DeepFilterNet3 model is unavailable")
packages = {}
for package in (
    "faster-whisper", "ctranslate2", "torch", "torchaudio",
    "deepfilternet", "deepfilterlib", "numpy",
):
    try:
        packages[package] = metadata.version(package)
    except metadata.PackageNotFoundError:
        packages[package] = None
print(json.dumps({"supported_compute_types": supported, "versions": packages}, sort_keys=True))
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RuntimeStatus(False, reason=f"ASR runtime probe failed: {type(exc).__name__}")
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        detail = lines[-1][-500:] if lines else "unknown runtime error"
        return RuntimeStatus(False, reason=f"ASR runtime is unavailable: {detail}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        return RuntimeStatus(
            True,
            supported_compute_types=tuple(payload.get("supported_compute_types", ())),
            versions=payload.get("versions") or {},
        )
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return RuntimeStatus(False, reason=f"ASR runtime probe returned invalid data: {type(exc).__name__}")
def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _worker_id() -> str:
    configured = os.environ.get("ASR_WORKER_ID", "").strip()
    if configured:
        return configured[:200]
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _health_path() -> Path:
    return Path(os.environ.get("ASR_HEARTBEAT_FILE", "/data/worker-heartbeat.json"))




def _directory_populated(path: Path) -> bool:
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False
def write_health(
    worker_id: str,
    status: str,
    device: str,
    gpu: GPUStatus,
    current_job_id: str | None,
    message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "worker_id": worker_id,
        "pid": os.getpid(),
        "updated_at": _timestamp(),
        "updated_at_epoch": time.time(),
        "status": status,
        "device": device,
        "gpu": asdict(gpu),
        "current_job_id": current_job_id,
        "message": message,
    }
    atomic_write_json(_health_path(), payload)
    return payload


def healthcheck(max_age_seconds: float | None = None) -> int:
    max_age = max_age_seconds or _env_float("ASR_HEARTBEAT_MAX_AGE_SECONDS", 90.0)
    try:
        payload = json.loads(_health_path().read_text(encoding="utf-8"))
        age = time.time() - float(payload["updated_at_epoch"])
        # Availability is intentionally not part of container liveness.  An
        # unavailable heartbeat keeps MP4/MP3 service healthy while the UI can
        # explain why Transcript mode is disabled.
        return 0 if -5.0 <= age <= max_age else 1
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 1


class Worker:
    def __init__(
        self,
        database: Database,
        worker_id: str,
        device: str,
        compute_type: str,
        poll_seconds: float,
        stale_seconds: float,
        once: bool = False,
    ) -> None:
        self.database = database
        self.worker_id = worker_id
        self.device = device
        self.compute_type = compute_type
        self.poll_seconds = poll_seconds
        self.stale_seconds = stale_seconds
        self.once = once
        self.stopping = False
        self.gpu = GPUStatus(False, reason="GPU check has not run")
        self.runtime = RuntimeStatus(False, reason="ASR runtime probe has not run")
        self.current_job_id: str | None = None
        self.last_gpu_check = 0.0
        self.last_runtime_check = 0.0
        self.last_heartbeat = 0.0

    def stop(self, *_: Any) -> None:
        self.stopping = True

    def _refresh_gpu(self, force: bool = False) -> None:
        interval = _env_float("ASR_GPU_RECHECK_SECONDS", 15.0)
        if force or time.monotonic() - self.last_gpu_check >= interval:
            self.gpu = detect_gpu()
            self.last_gpu_check = time.monotonic()

    @property
    def device_available(self) -> bool:
        return self.device == "cpu" or self.gpu.available

    def _refresh_runtime(self, force: bool = False) -> None:
        if not self.device_available:
            return
        interval = _env_float("ASR_RUNTIME_RECHECK_SECONDS", 60.0)
        if force or time.monotonic() - self.last_runtime_check >= interval:
            self.runtime = probe_runtime(self.device, self.compute_type)
            self.last_runtime_check = time.monotonic()

    @property
    def available(self) -> bool:
        return self.device_available and self.runtime.available

    @property
    def unavailable_reason(self) -> str | None:
        if not self.device_available:
            return self.gpu.reason or "The requested compute device is unavailable"
        return self.runtime.reason

    def heartbeat(self, status: str, job_id: str | None = None, message: str | None = None, force: bool = False) -> None:
        interval = _env_float("ASR_HEARTBEAT_SECONDS", 5.0)
        if not force and time.monotonic() - self.last_heartbeat < interval:
            return
        models_dir = Path(os.environ.get(
            "WHISPER_MODEL_DIR", os.environ.get("ASR_MODELS_DIR", "/models")
        ))
        deepfilter_cache = Path(os.environ.get(
            "DEEPFILTER_CACHE_DIR", os.environ.get("ASR_DEEPFILTER_CACHE", "/cache/deepfilter")
        ))
        profile_status = {
            name: {"available": self.available, "reason": self.unavailable_reason}
            for name in ("fast", "standard", "maximum")
        }
        details = {
            "device": self.device,
            "compute_type": self.compute_type,
            "memory_total_mib": self.gpu.memory_total_mib,
            "driver_version": self.gpu.driver_version,
            "reason": self.unavailable_reason,
            "message": message,
            "python": platform.python_version(),
            "runtime": asdict(self.runtime),
            "profiles": profile_status,
            "cache": {
                "models_dir": str(models_dir),
                "whisper_populated": _directory_populated(models_dir),
                "deepfilter_dir": str(deepfilter_cache),
                "deepfilter_populated": _directory_populated(deepfilter_cache),
                "pinned_deepfilter_model": os.environ.get("DEEPFILTER_MODEL_DIR"),
            },
        }
        self.database.heartbeat_worker(
            self.worker_id,
            status=status,
            gpu_available=self.gpu.available,
            gpu_name=self.gpu.name,
            current_job_id=job_id,
            details=details,
        )
        write_health(self.worker_id, status, self.device, self.gpu, job_id, message)
        self.last_heartbeat = time.monotonic()

    def pipeline_heartbeat(self, job_id: str | None) -> None:
        self.heartbeat("running", job_id, force=False)

    def run(self) -> int:
        self.database.initialize()
        self._refresh_gpu(force=True)
        self._refresh_runtime(force=True)
        print(
            json.dumps(
                {
                    "worker_id": self.worker_id,
                    "device": self.device,
                    "gpu_available": self.gpu.available,
                    "gpu_name": self.gpu.name,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        while not self.stopping:
            self._refresh_gpu()
            self._refresh_runtime()
            if not self.available:
                self.heartbeat("unavailable", message=self.unavailable_reason, force=True)
                if self.once:
                    return 0
                self._wait()
                continue

            self.heartbeat("idle", force=True)
            job = self.database.claim_next_job(self.worker_id, stale_after_seconds=self.stale_seconds)
            if job is None:
                if self.once:
                    return 0
                self._wait()
                continue

            self.current_job_id = str(job["id"])
            self.heartbeat("running", self.current_job_id, force=True)
            pipeline = Pipeline(
                self.database,
                data_root=self.database.data_dir,
                models_dir=os.environ.get(
                    "WHISPER_MODEL_DIR", os.environ.get("ASR_MODELS_DIR", "/models")
                ),
                deepfilter_cache=os.environ.get(
                    "DEEPFILTER_CACHE_DIR",
                    os.environ.get("ASR_DEEPFILTER_CACHE", "/cache/deepfilter"),
                ),
                device=self.device,
                compute_type=self.compute_type,
                heartbeat=self.pipeline_heartbeat,
                worker_id=self.worker_id,
                poll_seconds=min(1.0, max(0.1, self.poll_seconds)),
                should_stop=lambda: self.stopping,
            )
            pipeline.run(job)
            self.current_job_id = None
            self.heartbeat("idle", force=True)
            if self.once:
                return 0
        self.heartbeat("stopping", self.current_job_id, force=True)
        return 0

    def _wait(self) -> None:
        deadline = time.monotonic() + self.poll_seconds
        while not self.stopping and time.monotonic() < deadline:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the serial ReClip ASR worker")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.healthcheck:
        return healthcheck()
    device = os.environ.get("ASR_DEVICE", "cuda").strip().lower()
    if device not in {"cuda", "cpu"}:
        print("ASR_DEVICE must be cuda or cpu", file=sys.stderr)
        return 2
    compute_type = os.environ.get(
        "ASR_COMPUTE_TYPE", "int8_float16" if device == "cuda" else "int8"
    ).strip()
    database = Database.from_env()
    worker = Worker(
        database,
        worker_id=_worker_id(),
        device=device,
        compute_type=compute_type,
        poll_seconds=_env_float("ASR_WORKER_POLL_SECONDS", 2.0),
        stale_seconds=_env_float("WORKER_STALE_SECONDS", 30.0),
        once=args.once,
    )
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    try:
        return worker.run()
    except Exception as exc:
        try:
            write_health(worker.worker_id, "failed", device, worker.gpu, worker.current_job_id, str(exc)[:500])
        except Exception:
            pass
        print(f"worker_failed={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
