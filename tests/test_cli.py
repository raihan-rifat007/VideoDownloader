import json
import core
import cli


def test_info_json_output(monkeypatch, capsys):
    monkeypatch.setattr(core, "probe",
                        lambda url: {"title": "T", "uploader": "U", "duration": 5,
                                     "thumbnail": "", "formats": []})
    rc = cli.main(["info", "some-url", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["title"] == "T"


def test_download_single_json(monkeypatch, capsys):
    monkeypatch.setattr(core, "download",
                        lambda *a, **k: {"file": "/d/Clip.mp4", "title": "Clip", "ext": ".mp4"})
    rc = cli.main(["download", "u", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["file"] == "/d/Clip.mp4"


def test_download_multi_returns_array(monkeypatch, capsys):
    monkeypatch.setattr(core, "download",
                        lambda url, **k: {"file": f"/d/{url}.mp4", "title": url, "ext": ".mp4"})
    rc = cli.main(["download", "u1", "u2", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [r["title"] for r in out] == ["u1", "u2"]


def test_name_with_multiple_urls_is_rejected(monkeypatch, capsys):
    rc = cli.main(["download", "u1", "u2", "--name", "x", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "error" in out


def test_reclip_error_exit_code_and_stderr(monkeypatch, capsys):
    def boom(url):
        raise core.ReclipError("no such video")
    monkeypatch.setattr(core, "probe", boom)
    rc = cli.main(["info", "u"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no such video" in captured.err


def test_download_forwards_args_to_core(monkeypatch, capsys):
    captured = {}
    def fake(url, **k):
        captured["url"] = url
        captured.update(k)
        return {"file": "/d/C.mp4", "title": "C", "ext": ".mp4"}
    monkeypatch.setattr(core, "download", fake)
    cli.main(["download", "u", "--quality", "720", "-o", "/out", "--name", "n",
              "--timeout", "600", "--json"])
    assert captured["url"] == "u"
    assert captured["kind"] == "video"
    assert captured["quality"] == 720
    assert captured["output_dir"] == "/out"
    assert captured["name"] == "n"
    assert captured["timeout"] == 600


def test_download_audio_forwards_kind(monkeypatch, capsys):
    captured = {}
    def fake(url, **k):
        captured.update(k)
        return {"file": "/d/C.mp3", "title": "C", "ext": ".mp3"}
    monkeypatch.setattr(core, "download", fake)
    rc = cli.main(["download", "u", "--audio", "--json"])
    assert rc == 0
    assert captured["kind"] == "audio"
    assert captured["quality"] is None


def test_audio_with_quality_is_rejected(monkeypatch, capsys):
    def fail(*a, **k):
        raise AssertionError("core.download should not be called")
    monkeypatch.setattr(core, "download", fail)
    rc = cli.main(["download", "u", "--audio", "--quality", "720", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out == {"error": "--quality does not apply to --audio downloads"}


def test_download_default_timeout_is_forwarded(monkeypatch, capsys):
    captured = {}
    def fake(url, **k):
        captured.update(k)
        return {"file": "/d/C.mp4", "title": "C", "ext": ".mp4"}
    monkeypatch.setattr(core, "download", fake)
    cli.main(["download", "u", "--json"])
    assert captured["timeout"] == core.DOWNLOAD_TIMEOUT


def test_download_multi_partial_failure_reports_per_url(monkeypatch, capsys):
    def fake(url, **k):
        if url == "bad":
            raise core.ReclipError("boom")
        return {"file": f"/d/{url}.mp4", "title": url, "ext": ".mp4"}
    monkeypatch.setattr(core, "download", fake)
    rc = cli.main(["download", "good", "bad", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out[0]["file"] == "/d/good.mp4"
    assert out[1] == {"url": "bad", "error": "boom"}


def test_download_multi_partial_failure_human_output(monkeypatch, capsys):
    def fake(url, **k):
        if url == "bad":
            raise core.ReclipError("boom")
        return {"file": f"/d/{url}.mp4", "title": url, "ext": ".mp4"}
    monkeypatch.setattr(core, "download", fake)
    rc = cli.main(["download", "good", "bad"])
    cap = capsys.readouterr()
    assert rc == 1
    assert "Saved: /d/good.mp4" in cap.out
    assert "error: bad: boom" in cap.err


def test_download_human_prints_saved(monkeypatch, capsys):
    monkeypatch.setattr(core, "download",
                        lambda *a, **k: {"file": "/d/Clip.mp4", "title": "Clip", "ext": ".mp4"})
    rc = cli.main(["download", "u"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Saved:" in out and "/d/Clip.mp4" in out


def test_info_human_prints_block(monkeypatch, capsys):
    monkeypatch.setattr(core, "probe",
                        lambda url: {"title": "T", "uploader": "U", "duration": 5,
                                     "thumbnail": "", "formats": [{"id": "a", "label": "1080p", "height": 1080}]})
    cli.main(["info", "u"])
    out = capsys.readouterr().out
    assert "Title: T" in out
    assert "Uploader: U" in out
    assert "1080p" in out


def test_playlist_human_prints_each_url(monkeypatch, capsys):
    monkeypatch.setattr(core, "expand_playlist", lambda url: ["a", "b"])
    cli.main(["playlist", "u"])
    assert capsys.readouterr().out.splitlines() == ["a", "b"]


def test_transcript_human_prints_text(monkeypatch, capsys):
    monkeypatch.setattr(core, "transcript",
                        lambda url, lang=None: {"text": "hello world", "lang": "en",
                                                "auto": False, "available_langs": ["en"]})
    rc = cli.main(["transcript", "u"])
    assert rc == 0
    assert "hello world" in capsys.readouterr().out


def test_transcript_no_subs_message_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(core, "transcript",
                        lambda url, lang=None: {"text": None, "lang": None,
                                                "auto": None, "available_langs": ["fr"]})
    rc = cli.main(["transcript", "u"])
    cap = capsys.readouterr()
    assert rc == 0
    assert "fr" in cap.err


def test_transcript_filesystem_error_returns_json(monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise PermissionError("temporary storage denied")

    monkeypatch.setattr(core.tempfile, "mkdtemp", denied)
    rc = cli.main(["transcript", "u", "--json"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "Cannot process subtitle files: temporary storage denied",
    }
