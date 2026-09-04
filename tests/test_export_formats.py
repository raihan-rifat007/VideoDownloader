import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as reclip_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ExportFormatTest(unittest.TestCase):
    def test_runtime_options_are_shared_with_download_commands(self):
        environment = {
            "YTDLP_COOKIES_FILE": "/run/secrets/reclip-cookies",
            "YTDLP_PROXY": "http://proxy.example:8080",
            "YTDLP_USER_AGENT": "ReClip browser agent",
        }

        with patch.dict(os.environ, environment, clear=True):
            command = reclip_app.build_download_command(
                "https://example.com/video",
                "mkv",
                None,
                "/downloads/job.%(ext)s",
            )

        self.assertEqual(
            command[:7],
            [
                "yt-dlp",
                "--cookies",
                environment["YTDLP_COOKIES_FILE"],
                "--proxy",
                environment["YTDLP_PROXY"],
                "--user-agent",
                environment["YTDLP_USER_AGENT"],
            ],
        )
        self.assertIn("--no-playlist", command)

    def test_legacy_and_explicit_export_formats_are_normalized(self):
        expected = {
            "video": "mp4",
            "audio": "mp3",
            "mp4": "mp4",
            "mp3": "mp3",
            "mkv": "mkv",
            "mov": "mov",
            " MOV ": "mov",
        }

        for supplied, normalized in expected.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(
                    reclip_app.normalize_export_format(supplied), normalized
                )

        for supplied in ("avi", "webm", "", None, 42):
            with self.subTest(supplied=supplied):
                self.assertIsNone(reclip_app.normalize_export_format(supplied))

    def test_video_commands_request_the_selected_container(self):
        for export_format in ("mp4", "mkv", "mov"):
            with self.subTest(export_format=export_format):
                command = reclip_app.build_download_command(
                    "https://example.com/video",
                    export_format,
                    "137",
                    "/downloads/job.%(ext)s",
                )

                self.assertIn("--merge-output-format", command)
                self.assertEqual(
                    command[command.index("--merge-output-format") + 1],
                    export_format,
                )
                self.assertIn("--recode-video", command)
                self.assertEqual(
                    command[command.index("--recode-video") + 1], export_format
                )
                self.assertEqual(command[-1], "https://example.com/video")

    def test_mp3_command_uses_audio_extraction(self):
        command = reclip_app.build_download_command(
            "https://example.com/video",
            "mp3",
            None,
            "/downloads/job.%(ext)s",
        )

        self.assertIn("-x", command)
        self.assertEqual(command[command.index("--audio-format") + 1], "mp3")
        self.assertNotIn("--merge-output-format", command)
        self.assertNotIn("--recode-video", command)

    def test_download_uses_the_requested_output_extension(self):
        completed = type("Completed", (), {"returncode": 0, "stderr": ""})()

        for export_format in ("mp4", "mp3", "mkv", "mov"):
            with self.subTest(export_format=export_format), tempfile.TemporaryDirectory() as tmp:
                job_id = f"job-{export_format}"
                output = Path(tmp) / f"{job_id}.{export_format}"
                output.write_bytes(b"media")
                reclip_app.jobs[job_id] = {
                    "status": "downloading",
                    "title": "Example",
                }

                with patch.object(reclip_app, "DOWNLOAD_DIR", tmp), patch.object(
                    reclip_app.subprocess, "run", return_value=completed
                ):
                    reclip_app.run_download(
                        job_id,
                        "https://example.com/video",
                        export_format,
                        None,
                    )

                job = reclip_app.jobs.pop(job_id)
                self.assertEqual(job["status"], "done")
                self.assertEqual(job["file"], str(output))
                self.assertEqual(job["filename"], f"Example.{export_format}")

    def test_download_endpoint_rejects_unsupported_formats(self):
        client = reclip_app.app.test_client()

        with patch.object(reclip_app.threading, "Thread") as thread:
            response = client.post(
                "/api/download",
                json={"url": "https://example.com/video", "format": "avi"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported export format", response.get_json()["error"])
        thread.assert_not_called()

    def test_download_endpoint_keeps_legacy_api_values_compatible(self):
        client = reclip_app.app.test_client()

        for supplied, expected in (("video", "mp4"), ("audio", "mp3")):
            with self.subTest(supplied=supplied), patch.object(
                reclip_app.threading, "Thread"
            ) as thread:
                response = client.post(
                    "/api/download",
                    json={
                        "url": "https://example.com/video",
                        "format": supplied,
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(thread.call_args.kwargs["args"][2], expected)
                reclip_app.jobs.pop(response.get_json()["job_id"])

    def test_bilibili_412_returns_actionable_deployment_error(self):
        client = reclip_app.app.test_client()
        failed = type(
            "Completed",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": (
                    "ERROR: [BiliBili] BV1example: Unable to download webpage: "
                    "HTTP Error 412: Precondition Failed"
                ),
            },
        )()

        with patch.object(reclip_app.subprocess, "run", return_value=failed):
            response = client.post(
                "/api/info",
                json={"url": "https://www.bilibili.com/video/BV1example"},
            )

        self.assertEqual(response.status_code, 400)
        error = response.get_json()["error"]
        self.assertIn("Bilibili blocked this server", error)
        self.assertIn("YTDLP_COOKIES_FILE", error)
        self.assertIn("YTDLP_PROXY", error)

    def test_frontend_exposes_every_supported_format(self):
        page = (PROJECT_ROOT / "templates" / "index.html").read_text(
            encoding="utf-8"
        )

        for export_format in ("mp4", "mp3", "mkv", "mov"):
            with self.subTest(export_format=export_format):
                self.assertIn(f'data-format="{export_format}"', page)

        self.assertIn("format: currentFormat", page)


if __name__ == "__main__":
    unittest.main()
