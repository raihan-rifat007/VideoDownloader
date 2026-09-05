"""Regression tests for yt-dlp path, format selection, transcode, and errors (no network)."""

import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

import app as reclip_app


class YtdlpPathTests(unittest.TestCase):
    def test_resolve_ytdlp_returns_a_runnable_command(self):
        cmd = reclip_app.YTDLP
        self.assertIsInstance(cmd, list)
        self.assertGreaterEqual(len(cmd), 1)
        first = cmd[0]
        self.assertTrue(os.path.exists(first) or shutil.which(first), first)

    @patch("app._run_ytdlp_progress", return_value=(0, ""))
    def test_run_download_invokes_resolved_ytdlp(self, mock_run):
        job_id = "testjob01"
        reclip_app.jobs[job_id] = {"status": "downloading", "title": ""}
        with patch("app.glob.glob", return_value=[]):
            reclip_app.run_download(job_id, "https://example.com/v", "video", None)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[: len(reclip_app.YTDLP)], reclip_app.YTDLP)


class FormatSelectionTests(unittest.TestCase):
    def test_pick_video_formats_excludes_av1_vp9_and_first_is_1080p_137(self):
        formats = [
            {"format_id": "313", "height": 2160, "vcodec": "vp9", "tbr": 9000},
            {"format_id": "401", "height": 2160, "vcodec": "av01.0.12M.08", "tbr": 3000},
            {"format_id": "271", "height": 1440, "vcodec": "vp9", "tbr": 3000},
            {"format_id": "400", "height": 1440, "vcodec": "av01.0.12M.08", "tbr": 1600},
            {"format_id": "248", "height": 1080, "vcodec": "vp9", "tbr": 1200},
            {"format_id": "399", "height": 1080, "vcodec": "av01.0.08M.08", "tbr": 684},
            {"format_id": "137", "height": 1080, "vcodec": "avc1.640028", "tbr": 1300},
            {"format_id": "247", "height": 720, "vcodec": "vp9", "tbr": 800},
            {"format_id": "136", "height": 720, "vcodec": "avc1.4d401f", "tbr": 400},
        ]
        picked = reclip_app._pick_video_formats(formats)
        ids = [f["id"] for f in picked]
        by_height = {f["height"]: f["id"] for f in picked}
        self.assertEqual(picked[0]["id"], "137")
        self.assertEqual(picked[0]["height"], 1080)
        self.assertEqual(by_height[1080], "137")
        self.assertEqual(by_height[720], "136")
        self.assertNotIn(2160, by_height)
        self.assertNotIn(1440, by_height)
        self.assertNotIn("401", ids)
        self.assertNotIn("400", ids)
        self.assertNotIn("313", ids)
        self.assertNotIn("271", ids)

    def test_pick_video_formats_prefers_h264_over_unknown_at_same_height(self):
        formats = [
            {"format_id": "hls-98", "height": 270, "vcodec": "avc1.4D400D", "tbr": 98},
            {"format_id": "http-256", "height": 270, "vcodec": "none", "tbr": 256, "video_ext": "mp4"},
        ]
        picked = reclip_app._pick_video_formats(formats)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["id"], "hls-98")

    def test_pick_video_formats_unknown_metadata_only_without_h264(self):
        formats = [
            {"format_id": "http-256", "height": 270, "vcodec": "none", "tbr": 256, "video_ext": "mp4"},
        ]
        picked = reclip_app._pick_video_formats(formats)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["id"], "http-256")

    def test_pick_video_formats_instagram_progressive_without_height(self):
        formats = [
            {"format_id": "1", "vcodec": "none", "video_ext": "mp4"},
            {"format_id": "dash-v", "height": 1280, "vcodec": "vp09.00.31.08.00.01.01.01.00", "tbr": 500},
        ]
        picked = reclip_app._pick_video_formats(formats)
        ids = [f["id"] for f in picked]
        self.assertIn("1", ids)
        self.assertIn("dash-v", ids)

    def test_mp4_format_string_groups_selected_video_with_aac_audio(self):
        fmt = reclip_app._mp4_format_string("137")
        self.assertEqual(
            fmt,
            "137+(bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio)/bestvideo+bestaudio/best",
        )
        self.assertNotIn("137+bestaudio[ext=m4a]/bestaudio", fmt)

    def test_mp4_format_string_default_groups_h264_video_with_aac_audio(self):
        fmt = reclip_app._mp4_format_string()
        expected_prefix = (
            "(bestvideo[vcodec^=avc1][ext=mp4]/bestvideo[vcodec^=avc1]/"
            "bestvideo[ext=mp4]/bestvideo)+(bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio)"
        )
        self.assertTrue(fmt.startswith(expected_prefix))
        self.assertTrue(fmt.endswith("/bestvideo+bestaudio/best"))
        self.assertEqual(fmt.count("/bestvideo+bestaudio/best"), 1)

    def test_mp4_format_string_fallback_keeps_video_audio_pair(self):
        for fmt in (
            reclip_app._mp4_format_string("137"),
            reclip_app._mp4_format_string(),
        ):
            primary, fallback = fmt.split("/bestvideo+bestaudio/best", 1)
            self.assertIn("+(", primary)
            self.assertEqual(fallback, "")


class WhatsAppTranscodeTests(unittest.TestCase):
    def test_whatsapp_compatible_h264_aac_lc(self):
        streams = [
            {"codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a", "profile": "LC"},
        ]
        with patch("app._ffprobe_streams", return_value=streams):
            self.assertTrue(reclip_app._is_whatsapp_compatible_video("/fake.mp4"))

    def test_whatsapp_incompatible_vp9(self):
        streams = [
            {"codec_type": "video", "codec_name": "vp9", "codec_tag_string": "vp09", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a", "profile": "LC"},
        ]
        with patch("app._ffprobe_streams", return_value=streams):
            self.assertFalse(reclip_app._is_whatsapp_compatible_video("/fake.mp4"))

    def test_whatsapp_incompatible_hevc(self):
        streams = [
            {"codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a", "profile": "LC"},
        ]
        with patch("app._ffprobe_streams", return_value=streams):
            self.assertFalse(reclip_app._is_whatsapp_compatible_video("/fake.mp4"))

    def test_whatsapp_incompatible_he_aac(self):
        streams = [
            {"codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a", "profile": "HE-AAC"},
        ]
        with patch("app._ffprobe_streams", return_value=streams):
            self.assertFalse(reclip_app._is_whatsapp_compatible_video("/fake.mp4"))

    @patch("app.os.replace")
    @patch("app.os.remove")
    @patch("app.subprocess.run")
    @patch("app.shutil.which")
    def test_transcode_failure_preserves_original(self, mock_which, mock_run, mock_remove, mock_replace):
        mock_which.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fail")
        path = "/tmp/test_video.mp4"
        with patch("app.os.path.isfile", return_value=True):
            ok, err = reclip_app._transcode_for_whatsapp(path)
        self.assertFalse(ok)
        self.assertIn("conversion", err.lower())
        mock_replace.assert_not_called()

    @patch("app.os.replace")
    @patch("app._is_whatsapp_compatible_video")
    @patch("app.subprocess.run")
    @patch("app.shutil.which")
    def test_transcode_success_replaces_file(self, mock_which, mock_run, mock_compatible, mock_replace):
        mock_which.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        mock_compatible.return_value = True
        path = "/tmp/test_video.mp4"
        ok, err = reclip_app._transcode_for_whatsapp(path)
        self.assertTrue(ok)
        self.assertIsNone(err)
        mock_replace.assert_called_once()

    @patch("app.shutil.which")
    def test_transcode_missing_ffmpeg_returns_clear_error(self, mock_which):
        mock_which.return_value = None
        ok, err = reclip_app._transcode_for_whatsapp("/tmp/test_video.mp4")
        self.assertFalse(ok)
        self.assertIn("ffmpeg not found", err)

    @patch("app.subprocess.run")
    @patch("app.shutil.which")
    def test_transcode_uses_main_profile_without_fixed_level(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        path = "/tmp/test_video.mp4"
        with patch("app._is_whatsapp_compatible_video", return_value=True):
            with patch("app.os.replace"):
                reclip_app._transcode_for_whatsapp(path)
        cmd = mock_run.call_args[0][0]
        self.assertIn("-profile:v", cmd)
        self.assertIn("main", cmd)
        self.assertNotIn("-level", cmd)


class RunDownloadTranscodeTests(unittest.TestCase):
    @patch("app._transcode_for_whatsapp")
    @patch("app._is_whatsapp_compatible_video")
    @patch("app.glob.glob")
    @patch("app._run_ytdlp_progress", return_value=(0, ""))
    def test_run_download_transcode_failure_keeps_original_available(
        self, mock_run, mock_glob, mock_compatible, mock_transcode,
    ):
        job_id = "transfail01"
        fake_path = os.path.join(reclip_app.DOWNLOAD_DIR, f"{job_id}.mp4")
        mock_glob.return_value = [fake_path]
        mock_compatible.return_value = False
        mock_transcode.return_value = (False, "Video conversion for WhatsApp failed")

        with open(fake_path, "wb") as f:
            f.write(b"fake incompatible video")

        reclip_app.jobs[job_id] = {"status": "downloading", "title": ""}
        try:
            reclip_app.run_download(job_id, "https://example.com/v", "video", None)
            job = reclip_app.jobs[job_id]
            # Media must never be blocked: keep the file, mark done, and warn.
            self.assertEqual(job["status"], "done")
            self.assertEqual(job.get("file"), fake_path)
            self.assertIn("conversion", job.get("warning", "").lower())
            self.assertTrue(os.path.isfile(fake_path))
        finally:
            if os.path.isfile(fake_path):
                os.remove(fake_path)

    @patch.dict(os.environ, {"RECLIP_WHATSAPP_COMPAT": "0"}, clear=False)
    @patch("app.glob.glob")
    @patch("app._run_ytdlp_progress", return_value=(0, ""))
    def test_run_download_ignores_partial_files(self, mock_run, mock_glob):
        job_id = "partial01"
        real = os.path.join(reclip_app.DOWNLOAD_DIR, f"{job_id}.mp4")
        part = os.path.join(reclip_app.DOWNLOAD_DIR, f"{job_id}.mp4.part")
        mock_glob.return_value = [real, part]
        with open(real, "wb") as f:
            f.write(b"real")
        with open(part, "wb") as f:
            f.write(b"partial")
        reclip_app.jobs[job_id] = {"status": "downloading", "title": ""}
        try:
            reclip_app.run_download(job_id, "https://example.com/v", "video", None)
            job = reclip_app.jobs[job_id]
            self.assertEqual(job["status"], "done")
            self.assertEqual(job.get("file"), real)
            self.assertFalse(os.path.exists(part))
        finally:
            for p in (real, part):
                if os.path.isfile(p):
                    os.remove(p)


class FileServingPathTests(unittest.TestCase):
    def test_file_serving_rejects_path_outside_download_dir(self):
        client = reclip_app.app.test_client()
        reclip_app.jobs["outside"] = {
            "status": "done",
            "file": "/etc/hostname",
            "filename": "hostname",
        }
        resp = client.get("/api/file/outside")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid file path", resp.get_json()["error"])

    def test_file_serving_missing_file_returns_404(self):
        client = reclip_app.app.test_client()
        reclip_app.jobs["missing"] = {
            "status": "done",
            "file": os.path.join(reclip_app.DOWNLOAD_DIR, "nope.mp4"),
            "filename": "nope.mp4",
        }
        resp = client.get("/api/file/missing")
        self.assertEqual(resp.status_code, 404)

    def test_resolve_download_dir_respects_env_override(self):
        with patch.dict(os.environ, {"RECLIP_DOWNLOAD_DIR": "~/Downloads/ReClip"}, clear=False):
            self.assertEqual(
                reclip_app._resolve_download_dir(),
                os.path.abspath(os.path.join(os.path.expanduser("~"), "Downloads", "ReClip")),
            )


class ErrorExtractionTests(unittest.TestCase):
    def test_ytdlp_error_prefers_error_line(self):
        stderr = (
            "WARNING: foo\n"
            "ERROR: [TikTok] 123: Log in for access\n"
            "Some trailing noise\n"
        )
        self.assertTrue(reclip_app._ytdlp_error(stderr).startswith("ERROR:"))

    def test_ytdlp_error_falls_back_to_last_line(self):
        stderr = "download failed\nnetwork timeout"
        self.assertEqual(reclip_app._ytdlp_error(stderr), "network timeout")

    def test_instagram_carousel_error_message(self):
        stderr = "ERROR: [Instagram] abc: No video formats found!\nERROR: [Instagram] def: No video formats found!\n"
        url = "https://www.instagram.com/p/DXl9N_GAuCj/"
        msg = reclip_app._info_error(stderr, url)
        self.assertEqual(msg, reclip_app.INSTAGRAM_PHOTO_CAROUSEL_ERROR)
        self.assertNotIn("login", msg.lower())
        self.assertIn("not yet support", msg.lower())

    @patch("app.subprocess.run")
    def test_instagram_carousel_detected_via_playlist_json(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "_type": "playlist",
                "playlist_count": 3,
                "entries": [None, None, None],
            }),
            stderr="",
        )
        stderr = "ERROR: [Instagram] abc: No video formats found!\n"
        url = "https://www.instagram.com/p/test/"
        self.assertEqual(
            reclip_app._instagram_photo_carousel_error(stderr, url),
            reclip_app.INSTAGRAM_PHOTO_CAROUSEL_ERROR,
        )


if __name__ == "__main__":
    unittest.main()
