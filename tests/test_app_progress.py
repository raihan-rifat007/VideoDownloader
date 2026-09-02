import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

import app as app_module


class ApiProgressTests(unittest.TestCase):
    def setUp(self):
        with app_module.jobs_lock:
            app_module.jobs.clear()
        self.client = app_module.app.test_client()

    def _create_job(self):
        with patch.object(app_module.threading.Thread, "start"):
            response = self.client.post(
                "/api/download",
                json={"url": "https://example.com/video.mp4", "format": "video"},
            )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["job_id"]

    def test_initial_status_has_compatible_progress_fields(self):
        job_id = self._create_job()
        response = self.client.get(f"/api/status/{job_id}")
        payload = response.get_json()
        self.assertEqual(payload["status"], "downloading")
        self.assertEqual(payload["phase"], "preparing")
        self.assertEqual(payload["progress"]["scope"], "current_stream")
        self.assertIsNone(payload["progress"]["percent"])

    def test_finished_stream_is_not_done(self):
        job_id = self._create_job()
        app_module.apply_progress_event(
            job_id,
            {
                "kind": "download",
                "data": {
                    "status": "finished",
                    "downloaded_bytes": 100,
                    "total_bytes": 100,
                    "speed": 10,
                    "eta": 0,
                },
            },
            now=123.0,
        )
        payload = self.client.get(f"/api/status/{job_id}").get_json()
        self.assertEqual(payload["status"], "downloading")
        self.assertEqual(payload["phase"], "finalizing")
        self.assertIsNone(payload["progress"]["speed_bps"])
        self.assertIsNone(payload["progress"]["eta_seconds"])

    def test_next_stream_replaces_previous_stream_progress(self):
        job_id = self._create_job()
        app_module.apply_progress_event(
            job_id,
            {
                "kind": "download",
                "data": {
                    "status": "finished",
                    "downloaded_bytes": 1000,
                    "total_bytes": 1000,
                },
            },
            now=123.0,
        )
        app_module.apply_progress_event(
            job_id,
            {
                "kind": "download",
                "data": {
                    "status": "downloading",
                    "downloaded_bytes": 10,
                    "total_bytes": 100,
                    "speed": 5,
                    "eta": 18,
                },
            },
            now=124.0,
        )
        progress = self.client.get(f"/api/status/{job_id}").get_json()["progress"]
        self.assertEqual(progress["downloaded_bytes"], 10)
        self.assertEqual(progress["total_bytes"], 100)
        self.assertEqual(progress["percent"], 10.0)

    def test_terminal_job_ignores_late_progress(self):
        job_id = self._create_job()
        with app_module.jobs_lock:
            app_module.jobs[job_id].update({"status": "done", "phase": "complete"})
        app_module.apply_progress_event(
            job_id,
            {
                "kind": "download",
                "data": {
                    "status": "downloading",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                },
            },
            now=125.0,
        )
        payload = self.client.get(f"/api/status/{job_id}").get_json()
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["phase"], "complete")

    def test_unsafe_url_is_rejected_before_thread_creation(self):
        with patch.object(app_module.threading.Thread, "start") as start:
            response = self.client.post(
                "/api/download",
                json={"url": "--exec=echo unsafe", "format": "video"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Invalid URL")
        start.assert_not_called()

    def test_download_stream_updates_and_finishes_atomically(self):
        job_id = self._create_job()
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = app_module.DOWNLOAD_DIR
            app_module.DOWNLOAD_DIR = directory
            try:
                def fake_runner(cmd, on_line, timeout_seconds):
                    self.assertIn("--progress-template", cmd)
                    self.assertIn("--", cmd)
                    on_line(
                        'RECLIP_PROGRESS {"status":"downloading",'
                        '"downloaded_bytes":50,"total_bytes":100,"speed":20,"eta":3}'
                    )
                    on_line(
                        'RECLIP_POSTPROCESS {"status":"started",'
                        '"postprocessor":"FFmpeg"}'
                    )
                    Path(directory, f"{job_id}.mp4").write_bytes(b"test")
                    return 0

                with patch.object(app_module, "run_streaming_process", fake_runner):
                    app_module.run_download(
                        job_id,
                        "https://example.com/video.mp4",
                        "video",
                        None,
                    )
            finally:
                app_module.DOWNLOAD_DIR = previous_directory

        payload = self.client.get(f"/api/status/{job_id}").get_json()
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["phase"], "complete")
        self.assertEqual(payload["filename"], f"{job_id}.mp4")
        self.assertEqual(payload["progress"]["percent"], 100.0)

    def test_download_failure_exposes_clean_summary(self):
        job_id = self._create_job()

        def fake_runner(cmd, on_line, timeout_seconds):
            on_line("ERROR: private diagnostic details")
            return 2

        with patch.object(app_module, "run_streaming_process", fake_runner):
            app_module.run_download(
                job_id,
                "https://example.com/video.mp4",
                "video",
                None,
            )

        payload = self.client.get(f"/api/status/{job_id}").get_json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["phase"], "failed")
        self.assertEqual(payload["error"], "private diagnostic details")
        self.assertIsNone(payload["progress"])


if __name__ == "__main__":
    unittest.main()
