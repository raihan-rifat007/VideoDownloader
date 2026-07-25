"""Regression coverage for the original ReClip MP4/MP3 contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as reclip_app


@pytest.fixture()
def client():
    reclip_app.app.config.update(TESTING=True)
    reclip_app.jobs.clear()
    with reclip_app.app.test_client() as test_client:
        yield test_client
    reclip_app.jobs.clear()


def test_info_keeps_best_format_per_resolution(client, monkeypatch):
    payload = {
        "title": "Demo",
        "thumbnail": "https://example.test/thumb.jpg",
        "duration": 42,
        "uploader": "Uploader",
        "formats": [
            {"format_id": "low", "height": 720, "vcodec": "avc1", "tbr": 900},
            {"format_id": "best", "height": 720, "vcodec": "avc1", "tbr": 1800},
            {"format_id": "1080", "height": 1080, "vcodec": "avc1", "tbr": 2500},
            {"format_id": "audio", "height": None, "vcodec": "none", "tbr": 128},
        ],
    }

    def fake_run(command, **_kwargs):
        assert command[:3] == ["yt-dlp", "--no-playlist", "-j"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload) + "\n", stderr="")

    monkeypatch.setattr(reclip_app.subprocess, "run", fake_run)
    response = client.post("/api/info", json={"url": "https://example.test/video"})

    assert response.status_code == 200
    result = response.get_json()
    assert result["title"] == "Demo"
    assert result["formats"] == [
        {"height": 1080, "id": "1080", "label": "1080p"},
        {"height": 720, "id": "best", "label": "720p"},
    ]


def test_playlist_expansion_preserves_available_entries(client, monkeypatch):
    payload = {
        "entries": [
            {"url": "https://example.test/watch/one"},
            {"url": None},
            {"url": "https://example.test/watch/two"},
        ]
    }

    def fake_run(command, **_kwargs):
        assert command[:3] == ["yt-dlp", "--flat-playlist", "-J"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(reclip_app.subprocess, "run", fake_run)
    response = client.post("/api/playlist", json={"url": "https://example.test/list"})

    assert response.status_code == 200
    assert response.get_json()["urls"] == [
        "https://example.test/watch/one",
        "https://example.test/watch/two",
    ]


class ImmediateThread:
    def __init__(self, target, args):
        self.target = target
        self.args = args
        self.daemon = False

    def start(self):
        self.target(*self.args)


@pytest.mark.parametrize(
    ("format_choice", "format_id", "extension"),
    [("video", "137", ".mp4"), ("audio", None, ".mp3")],
)
def test_download_status_and_file_contract(
    client, monkeypatch, tmp_path, format_choice, format_id, extension
):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        template = command[command.index("-o") + 1]
        output = Path(template.replace("%(ext)s", extension.removeprefix(".")))
        output.write_bytes(b"media")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(reclip_app, "DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(reclip_app.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(reclip_app.subprocess, "run", fake_run)

    response = client.post(
        "/api/download",
        json={
            "url": "https://example.test/video",
            "format": format_choice,
            "format_id": format_id,
            "title": "Demo: clip",
        },
    )
    assert response.status_code == 200
    job_id = response.get_json()["job_id"]
    status = client.get(f"/api/status/{job_id}")
    assert status.get_json()["status"] == "done"
    assert status.get_json()["filename"] == f"Demo clip{extension}"

    download = client.get(f"/api/file/{job_id}")
    try:
        assert download.status_code == 200
        assert download.data == b"media"
    finally:
        download.close()

    command = commands[0]
    if format_choice == "video":
        assert "137+bestaudio/best" in command
    else:
        assert command[command.index("--audio-format") + 1] == "mp3"
