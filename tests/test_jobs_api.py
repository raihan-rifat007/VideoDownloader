import unittest
from unittest.mock import patch

import app as app_module


class FakeApiService:
    def __init__(self):
        self.calls = []

    def create(self, data):
        self.calls.append(("create", data))
        return {"job_id": "a" * 32, "attempt_no": 1}

    def status(self, job_id):
        return {"status": "error", "state": "failed", "job_id": job_id}

    def list_jobs(self, limit=50, cursor=None):
        return {"items": [], "next_cursor": None}

    def resume(self, job_id):
        self.calls.append(("resume", job_id))
        return {"job_id": job_id, "attempt_no": 2}

    def restart(self, job_id):
        self.calls.append(("restart", job_id))
        return {"job_id": "b" * 32, "attempt_no": 1}

    def cancel(self, job_id, attempt_no):
        self.calls.append(("cancel", job_id, attempt_no))
        return {
            "job_id": job_id,
            "attempt_no": attempt_no,
            "state": "cancelling",
        }

    def delete(self, job_id):
        self.calls.append(("delete", job_id))

    def file_path(self, job_id):
        raise KeyError("not found")


class JobsApiTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeApiService()
        self.client = app_module.app.test_client()

    def test_download_uses_durable_service(self):
        with patch.object(app_module, "_get_job_service", return_value=self.service):
            response = self.client.post(
                "/api/download",
                json={"url": "https://example.com/sample", "format": "video"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"job_id": "a" * 32, "attempt_no": 1})
        self.assertEqual(self.service.calls[0][0], "create")

    def test_invalid_url_is_rejected_before_service(self):
        with patch.object(app_module, "_get_job_service") as get_service:
            response = self.client.post(
                "/api/download",
                json={"url": "--exec=unsafe", "format": "video"},
            )
        self.assertEqual(response.status_code, 400)
        get_service.assert_not_called()

    def test_jobs_lifecycle_endpoints(self):
        job_id = "a" * 32
        with patch.object(app_module, "_get_job_service", return_value=self.service):
            self.assertEqual(self.client.get("/api/jobs").status_code, 200)
            self.assertEqual(self.client.get(f"/api/status/{job_id}").status_code, 200)
            self.assertEqual(self.client.post(f"/api/jobs/{job_id}/resume").status_code, 202)
            self.assertEqual(self.client.post(f"/api/jobs/{job_id}/restart").status_code, 201)
            cancel_response = self.client.post(
                f"/api/jobs/{job_id}/cancel", json={"attempt_no": 3}
            )
            self.assertEqual(cancel_response.status_code, 202)
            self.assertEqual(cancel_response.get_json()["state"], "cancelling")
            self.assertEqual(self.client.delete(f"/api/jobs/{job_id}").status_code, 204)
        self.assertEqual(
            [call[0] for call in self.service.calls],
            ["resume", "restart", "cancel", "delete"],
        )

    def test_unicode_attachment_filename_and_original_bytes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from urllib.parse import quote

        with TemporaryDirectory() as directory:
            media = Path(directory) / "media.mp4"
            media.write_bytes(b"test-content")
            with patch.object(app_module, "_get_job_service", return_value=self.service):
                with patch.object(
                    self.service,
                    "file_path",
                    return_value=(media, "熊猫的一天.mp4"),
                ):
                    response = self.client.get("/api/file/" + "a" * 32)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"test-content")
                disposition = response.headers["Content-Disposition"]
                self.assertIn("attachment", disposition)
                self.assertIn(quote("熊猫的一天.mp4"), disposition)
                self.assertNotIn("\r", disposition)
                self.assertNotIn("\n", disposition)
            finally:
                response.close()

    def test_real_service_uses_one_name_across_endpoints(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from urllib.parse import unquote

        from job_service import JobService
        from job_store import JobStore
        from tests.test_job_service import FakeRunner, ImmediateThread, sample_info

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            store.initialize()
            runner = FakeRunner()
            runner.mode = "success"
            service = JobService(
                store,
                root / "downloads",
                runtime_epoch="filename-test",
                metadata_loader=lambda url: sample_info(),
                runner=runner,
                thread_factory=ImmediateThread,
            )
            response = None
            try:
                job_id = service.create(
                    {
                        "url": "https://example.com/sample",
                        "format": "video",
                        "title": "熊猫的一天",
                        "format_id": None,
                    }
                )["job_id"]
                before = store.get_job(job_id)
                with patch.object(
                    app_module, "_get_job_service", return_value=service
                ):
                    status = self.client.get(f"/api/status/{job_id}").get_json()
                    listing = self.client.get("/api/jobs").get_json()
                    response = self.client.get(f"/api/file/{job_id}")

                name = "熊猫的一天.mp4"
                self.assertEqual(status["filename"], name)
                item = next(j for j in listing["items"] if j["job_id"] == job_id)
                self.assertEqual(item["filename"], name)
                self.assertEqual(response.status_code, 200)
                self.assertIn(name, unquote(response.headers["Content-Disposition"]))
                self.assertEqual(response.data, b"completed media")
                self.assertEqual(store.get_job(job_id), before)
                self.assertTrue(
                    (root / "downloads" / "jobs" / job_id / "media.mp4").is_file()
                )
            finally:
                if response is not None:
                    response.close()
                store.close()

    def test_cancel_requires_an_attempt_number(self):
        job_id = "a" * 32
        with patch.object(app_module, "_get_job_service", return_value=self.service):
            response = self.client.post(f"/api/jobs/{job_id}/cancel", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.calls, [])


if __name__ == "__main__":
    unittest.main()
