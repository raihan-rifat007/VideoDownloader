import pytest
import core
import app as webapp


@pytest.fixture
def client():
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def test_info_route_uses_core_probe(client, monkeypatch):
    monkeypatch.setattr(core, "probe",
                        lambda url: {"title": "T", "uploader": "", "duration": 1,
                                     "thumbnail": "", "formats": []})
    resp = client.post("/api/info", json={"url": "u"})
    assert resp.status_code == 200
    assert resp.get_json()["title"] == "T"


def test_info_route_maps_reclip_error_to_400(client, monkeypatch):
    def boom(url):
        raise core.ReclipError("bad url")
    monkeypatch.setattr(core, "probe", boom)
    resp = client.post("/api/info", json={"url": "u"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad url"


def test_playlist_route_uses_core(client, monkeypatch):
    monkeypatch.setattr(core, "expand_playlist", lambda url: ["a", "b"])
    resp = client.post("/api/playlist", json={"url": "u"})
    assert resp.get_json()["urls"] == ["a", "b"]


def test_run_download_populates_job_on_success(monkeypatch):
    monkeypatch.setattr(core, "download",
                        lambda *a, **k: {"file": "/downloads/Clip.mp4", "title": "Clip", "ext": ".mp4"})
    webapp.jobs["j1"] = {"status": "downloading", "url": "u", "title": "Clip"}
    webapp.run_download("j1", "u", "video", None)
    job = webapp.jobs["j1"]
    assert job["status"] == "done"
    assert job["file"] == "/downloads/Clip.mp4"
    assert job["filename"] == "Clip.mp4"


def test_run_download_records_reclip_error(monkeypatch):
    def boom(*a, **k):
        raise core.ReclipError("download failed")
    monkeypatch.setattr(core, "download", boom)
    webapp.jobs["j2"] = {"status": "downloading", "url": "u", "title": "T"}
    webapp.run_download("j2", "u", "video", None)
    assert webapp.jobs["j2"]["status"] == "error"
    assert webapp.jobs["j2"]["error"] == "download failed"


def test_info_without_json_content_type_returns_json_400(client):
    resp = client.post("/api/info", data="not json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No URL provided"


def test_playlist_without_json_content_type_returns_json_400(client):
    resp = client.post("/api/playlist", data="")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No URL provided"


def test_download_without_json_content_type_returns_json_400(client):
    resp = client.post("/api/download", data="x=1")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No URL provided"
