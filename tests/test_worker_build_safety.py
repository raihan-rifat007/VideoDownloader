from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\s*$\n(?P<body>.*?)(?=^  [A-Za-z0-9][A-Za-z0-9_.-]*:\s*$|^\S|\Z)",
        compose,
    )
    assert match is not None, f"Missing Compose service: {service}"
    return match.group("body")


def test_worker_image_does_not_retain_uv_cache_or_recursively_chown_large_trees() -> None:
    dockerfile = read("Dockerfile.worker")

    assert "UV_NO_CACHE=1 \\" in dockerfile
    assert not re.search(
        r"\bchown\s+-R\b[^\n]*(?:/data|/models|/cache|/opt/venv)", dockerfile
    )
    assert "install -d -o 1000 -g 1000" in dockerfile
    for path in ("/data/jobs", "/models/huggingface", "/cache/deepfilter"):
        assert path in dockerfile


def test_base_compose_cannot_implicitly_build_or_pull_the_worker() -> None:
    worker = service_block(read("docker-compose.yml"), "asr-worker")

    assert "build:" not in worker
    assert "pull_policy: never" in worker

    build_service = service_block(
        read("docker-compose.worker-build.yml"), "asr-worker"
    )
    assert "build:" in build_service
    assert "dockerfile: Dockerfile.worker" in build_service
    assert "image: reclip-asr-worker:latest" in build_service


def test_worker_build_wrapper_serializes_and_checks_the_vhdx_drive() -> None:
    script = read("build-worker.ps1")

    assert "$env:LOCALAPPDATA" in script
    assert "$DockerDataPath" in script
    assert "GetPathRoot" in script
    assert "AvailableFreeSpace" in script
    assert "$minimumFreeGB = 80" in script
    assert "MinimumFreeGB" not in script
    assert "$PreflightOnly" in script
    assert "Global\\ReClipAsrWorkerBuild" in script
    assert "WaitOne(0)" in script
    assert "AbandonedMutexException" in script
    assert "docker buildx history ls" in script
    assert "finally" in script
    assert "ReleaseMutex" in script
    assert "--parallel 1" in script
    assert "docker-compose.worker-build.yml" in script
    assert "build asr-worker" in script


def test_documented_worker_builds_use_the_guarded_wrapper() -> None:
    readme = read("README.md")
    env_example = read(".env.example")

    assert ".\\build-worker.ps1" in readme
    assert "docker compose up -d --no-build" in readme
    assert "build-worker.ps1" in env_example
    assert "up -d --no-build" in env_example
