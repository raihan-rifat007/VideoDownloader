import json
import os
import subprocess
import pytest
import core


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["yt-dlp"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_ytdlp_error_returns_last_stderr_line():
    result = _completed(returncode=1, stderr="line one\nERROR: the real reason\n")
    assert core._ytdlp_error(result) == "ERROR: the real reason"


def test_ytdlp_error_falls_back_when_empty():
    assert core._ytdlp_error(_completed(returncode=1, stderr="")) == "yt-dlp failed"


def test_run_raises_reclip_error_on_timeout(monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=60)
    monkeypatch.setattr(core.subprocess, "run", boom)
    with pytest.raises(core.ReclipError):
        core._run(["-j", "url"], 60)


def test_run_uses_current_interpreter(monkeypatch):
    captured = {}

    def fake(cmd, **kwargs):
        captured["cmd"] = cmd
        return _completed()

    monkeypatch.setattr(core.subprocess, "run", fake)
    core._run(["-j", "url"], 60)
    assert captured["cmd"] == [core.sys.executable, "-m", "yt_dlp", "-j", "url"]


def test_run_normalizes_process_start_failure(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(core.subprocess, "run", boom)
    with pytest.raises(core.ReclipError):
        core._run(["-j", "url"], 60)


def test_probe_keeps_best_format_per_height_sorted_desc(monkeypatch):
    info = {
        "title": "Sample", "uploader": "Chan", "duration": 10, "thumbnail": "t.jpg",
        "formats": [
            {"format_id": "a", "height": 720, "vcodec": "avc1", "tbr": 1000},
            {"format_id": "b", "height": 720, "vcodec": "avc1", "tbr": 2000},
            {"format_id": "c", "height": 1080, "vcodec": "avc1", "tbr": 3000},
            {"format_id": "d", "height": None, "vcodec": "none", "tbr": 50},
        ],
    }
    monkeypatch.setattr(core, "_run",
                        lambda cmd, timeout: _completed(stdout=json.dumps(info)))
    result = core.probe("url")
    assert result["title"] == "Sample"
    assert [f["height"] for f in result["formats"]] == [1080, 720]
    assert result["formats"][1]["id"] == "b"  # higher tbr wins at 720
    assert result["formats"][0]["label"] == "1080p"


def test_probe_raises_on_ytdlp_failure(monkeypatch):
    monkeypatch.setattr(core, "_run",
                        lambda cmd, timeout: _completed(returncode=1, stderr="ERROR: nope"))
    with pytest.raises(core.ReclipError):
        core.probe("url")


def test_probe_raises_reclip_error_on_bad_json(monkeypatch):
    monkeypatch.setattr(core, "_run", lambda cmd, timeout: _completed(returncode=0, stdout="not json"))
    with pytest.raises(core.ReclipError):
        core.probe("url")


def test_expand_playlist_returns_entry_urls(monkeypatch):
    info = {"entries": [{"url": "u1"}, {"url": "u2"}, {"title": "no url"}]}
    monkeypatch.setattr(core, "_run",
                        lambda cmd, timeout: _completed(stdout=json.dumps(info)))
    assert core.expand_playlist("url") == ["u1", "u2"]


def test_expand_playlist_raises_on_failure(monkeypatch):
    monkeypatch.setattr(core, "_run",
                        lambda cmd, timeout: _completed(returncode=1, stderr="ERROR: bad"))
    with pytest.raises(core.ReclipError):
        core.expand_playlist("url")


def test_probe_handles_null_formats(monkeypatch):
    info = {"title": "T", "formats": None}
    monkeypatch.setattr(core, "_run",
                        lambda cmd, timeout: _completed(stdout=json.dumps(info)))
    result = core.probe("url")
    assert result["formats"] == []


def test_probe_skips_formats_without_format_id(monkeypatch):
    info = {
        "title": "T",
        "formats": [
            {"height": 720, "vcodec": "avc1", "tbr": 5000},
            {"format_id": "a", "height": 720, "vcodec": "avc1", "tbr": 1000},
        ],
    }
    monkeypatch.setattr(core, "_run",
                        lambda cmd, timeout: _completed(stdout=json.dumps(info)))
    result = core.probe("url")
    assert [f["id"] for f in result["formats"]] == ["a"]


def test_expand_playlist_handles_null_entries(monkeypatch):
    monkeypatch.setattr(core, "_run",
                        lambda cmd, timeout: _completed(stdout=json.dumps({"entries": None})))
    assert core.expand_playlist("url") == []


def _fake_download_run(created_name):
    """Return a fake _run that writes created_name into the -o template's dir."""
    def _run(cmd, timeout):
        out_template = cmd[cmd.index("-o") + 1]
        work = os.path.dirname(out_template)
        with open(os.path.join(work, created_name), "w") as fh:
            fh.write("data")
        return _completed(returncode=0)
    return _run


def test_sanitize_filename_strips_illegal_and_caps():
    assert core.sanitize_filename('a/b:c*?"<>|d') == "abcd"
    assert len(core.sanitize_filename("x" * 200)) == 100


def test_download_video_moves_titled_mp4(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_run", _fake_download_run("My Clip.mp4"))
    result = core.download("url", kind="video", output_dir=str(tmp_path))
    assert result["ext"] == ".mp4"
    assert result["title"] == "My Clip"
    assert os.path.basename(result["file"]) == "My Clip.mp4"
    assert os.path.exists(result["file"])


def test_download_honors_name_override(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_run", _fake_download_run("Original.mp3"))
    result = core.download("url", kind="audio", output_dir=str(tmp_path), name="renamed")
    assert os.path.basename(result["file"]) == "renamed.mp3"


def test_download_raises_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_run", lambda cmd, timeout: _completed(returncode=0))
    with pytest.raises(core.ReclipError):
        core.download("url", output_dir=str(tmp_path))


def test_download_builds_quality_capped_selector(monkeypatch, tmp_path):
    captured = {}

    def _run(cmd, timeout):
        captured["cmd"] = cmd
        work = os.path.dirname(cmd[cmd.index("-o") + 1])
        open(os.path.join(work, "V.mp4"), "w").close()
        return _completed(returncode=0)

    monkeypatch.setattr(core, "_run", _run)
    core.download("url", kind="video", quality=720, output_dir=str(tmp_path))
    joined = " ".join(captured["cmd"])
    assert "height<=?720" in joined


def test_download_video_remuxes_fallback_to_mp4(monkeypatch, tmp_path):
    captured = {}

    def _run(cmd, timeout):
        captured["cmd"] = cmd
        work = os.path.dirname(cmd[cmd.index("-o") + 1])
        open(os.path.join(work, "V.mp4"), "w").close()
        return _completed(returncode=0)

    monkeypatch.setattr(core, "_run", _run)
    core.download("url", kind="video", output_dir=str(tmp_path))
    assert captured["cmd"][captured["cmd"].index("--remux-video") + 1] == "mp4"


def test_download_rejects_non_mp4_video_output(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_run", _fake_download_run("Fallback.webm"))
    with pytest.raises(core.ReclipError, match="no MP4 file"):
        core.download("url", kind="video", output_dir=str(tmp_path))


def test_download_name_of_only_illegal_chars_is_sanitized(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_run", _fake_download_run("X.mp4"))
    result = core.download("url", kind="video", output_dir=str(tmp_path), name="???")
    base = os.path.basename(result["file"])
    assert base != "???.mp4"
    assert not any(c in base for c in r'\/:*?"<>|')


def test_download_avoids_clobbering_existing_file(monkeypatch, tmp_path):
    existing = tmp_path / "My Clip.mp4"
    existing.write_text("old")
    monkeypatch.setattr(core, "_run", _fake_download_run("My Clip.mp4"))
    result = core.download("url", kind="video", output_dir=str(tmp_path))
    assert os.path.basename(result["file"]) == "My Clip (1).mp4"
    assert existing.exists()
    assert existing.read_text() == "old"


def test_download_handles_glob_metachars_in_output_dir(monkeypatch, tmp_path):
    out_dir = tmp_path / "Videos [4K]"
    monkeypatch.setattr(core, "_run", _fake_download_run("Clip.mp4"))
    result = core.download("url", kind="video", output_dir=str(out_dir))
    assert os.path.basename(result["file"]) == "Clip.mp4"
    assert os.path.exists(result["file"])


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_download_unwritable_output_dir_raises_reclip_error(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_run", _fake_download_run("Clip.mp4"))
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(core.ReclipError):
            core.download("url", output_dir=str(ro))
    finally:
        ro.chmod(0o700)


def test_download_output_dir_under_file_raises_reclip_error(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_run", _fake_download_run("Clip.mp4"))
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    with pytest.raises(core.ReclipError):
        core.download("url", output_dir=str(blocker / "sub"))


def test_download_separates_url_from_options(monkeypatch, tmp_path):
    captured = {}

    def _run(cmd, timeout):
        captured["cmd"] = cmd
        work = os.path.dirname(cmd[cmd.index("-o") + 1])
        open(os.path.join(work, "V.mp4"), "w").close()
        return _completed(returncode=0)

    monkeypatch.setattr(core, "_run", _run)
    core.download("-dashed-url", output_dir=str(tmp_path))
    assert captured["cmd"][-2:] == ["--", "-dashed-url"]


def test_probe_separates_url_from_options(monkeypatch):
    captured = {}

    def _run(cmd, timeout):
        captured["cmd"] = cmd
        return _completed(stdout=json.dumps({"title": "T", "formats": []}))

    monkeypatch.setattr(core, "_run", _run)
    core.probe("-dashed-url")
    assert captured["cmd"][-2:] == ["--", "-dashed-url"]


def test_download_passes_timeout_to_run(monkeypatch, tmp_path):
    captured = {}

    def _run(cmd, timeout):
        captured["timeout"] = timeout
        work = os.path.dirname(cmd[cmd.index("-o") + 1])
        open(os.path.join(work, "V.mp4"), "w").close()
        return _completed(returncode=0)

    monkeypatch.setattr(core, "_run", _run)
    core.download("url", output_dir=str(tmp_path), timeout=42)
    assert captured["timeout"] == 42


VTT_SAMPLE = """WEBVTT
Kind: captions
Language: en

1
00:00:01.000 --> 00:00:03.000
Hello there

2
00:00:03.000 --> 00:00:05.000
<00:00:03.500><c>Hello there</c>

3
00:00:05.000 --> 00:00:07.000
General Kenobi
"""


def test_vtt_to_text_cleans_and_dedupes():
    text = core._vtt_to_text(VTT_SAMPLE)
    assert text == "Hello there\nGeneral Kenobi"


def test_transcript_returns_text_when_subs_exist(monkeypatch, tmp_path):
    def _run(cmd, timeout):
        work = os.path.dirname(cmd[cmd.index("-o") + 1])
        with open(os.path.join(work, "sub.en.vtt"), "w") as fh:
            fh.write(VTT_SAMPLE)
        return _completed(returncode=0)
    monkeypatch.setattr(core, "_run", _run)
    monkeypatch.setattr(core, "_raw_info",
                        lambda url: {"subtitles": {"en": [], "de": []}, "automatic_captions": {}})
    result = core.transcript("url", lang="en")
    assert result["text"].startswith("Hello there")
    assert result["lang"] == "en"
    assert result["auto"] is False
    assert result["available_langs"] == ["de", "en"]


def test_transcript_no_subs_reports_available(monkeypatch):
    monkeypatch.setattr(core, "_run", lambda cmd, timeout: _completed(returncode=0))
    monkeypatch.setattr(core, "_raw_info",
                        lambda url: {"subtitles": {"fr": []}, "automatic_captions": {"en": []}})
    result = core.transcript("url", lang="en")
    assert result["text"] is None
    assert set(result["available_langs"]) == {"en", "fr"}


def test_transcript_marks_auto_generated(monkeypatch):
    def _run(cmd, timeout):
        if "--write-auto-sub" in cmd:
            work = os.path.dirname(cmd[cmd.index("-o") + 1])
            with open(os.path.join(work, "sub.en.vtt"), "w") as fh:
                fh.write(VTT_SAMPLE)
        return _completed(returncode=0)
    monkeypatch.setattr(core, "_run", _run)
    monkeypatch.setattr(core, "_raw_info",
                        lambda url: {"subtitles": {}, "automatic_captions": {"en": []}})
    result = core.transcript("url", lang="en")
    assert result["auto"] is True
    assert result["text"]


def test_vtt_to_text_drops_note_lines():
    vtt = "WEBVTT\n\nNOTE this is a comment\n\n1\n00:00:01.000 --> 00:00:02.000\nReal line\n"
    text = core._vtt_to_text(vtt)
    assert "this is a comment" not in text
    assert "Real line" in text


def test_vtt_to_text_parses_cue_blocks_statefully():
    vtt = """WEBVTT

NOTE this is a comment
comment continuation

cue-name
00:00:01.000 --> 00:00:02.000
2024
"""
    assert core._vtt_to_text(vtt) == "2024"


def test_transcript_tempdir_error_is_reclip_error(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("temporary storage denied")

    monkeypatch.setattr(core.tempfile, "mkdtemp", denied)
    with pytest.raises(core.ReclipError, match="temporary storage denied"):
        core.transcript("url")


def test_transcript_subtitle_listing_error_is_reclip_error(monkeypatch):
    monkeypatch.setattr(core, "_run", lambda cmd, timeout: _completed())

    def denied(path):
        raise PermissionError("subtitle listing denied")

    monkeypatch.setattr(core.os, "listdir", denied)
    with pytest.raises(core.ReclipError, match="subtitle listing denied"):
        core.transcript("url")


def test_transcript_subtitle_read_error_is_reclip_error(monkeypatch):
    monkeypatch.setattr(
        core, "_fetch_subs",
        lambda url, lang, auto, work: os.path.join(work, "missing.en.vtt"),
    )
    monkeypatch.setattr(core, "_raw_info", lambda url: {"subtitles": {},
                                                         "automatic_captions": {}})
    with pytest.raises(core.ReclipError, match="missing.en.vtt"):
        core.transcript("url")
