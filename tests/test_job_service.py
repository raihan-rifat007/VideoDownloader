import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from download_process import ProcessCancelled
from job_service import JobService
from job_store import JobStore


def sample_info():
    return {
        "id": "sample-id",
        "extractor_key": "generic",
        "title": "Sample title",
        "formats": [
            {
                "format_id": "137",
                "ext": "mp4",
                "protocol": "https",
                "height": 1080,
                "tbr": 5000,
                "vcodec": "avc1",
                "acodec": "none",
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "protocol": "https",
                "tbr": 130,
                "vcodec": "none",
                "acodec": "mp4a",
            },
        ],
    }


class ImmediateThread:
    def __init__(self, target, args, **kwargs):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


class FakeRunner:
    def __init__(self):
        self.mode = "fail"
        self.calls = []
        self.kwargs = []

    def __call__(self, command, on_line, timeout_seconds, **kwargs):
        self.calls.append(command)
        self.kwargs.append(kwargs)
        if self.mode == "fail":
            on_line("ERROR: controlled network interruption")
            return 2
        output_template = Path(command[command.index("-o") + 1])
        output = Path(str(output_template).replace("%(ext)s", "mp4"))
        output.write_bytes(b"completed media")
        on_line(
            'RECLIP_PROGRESS '
            + json.dumps(
                {
                    "status": "downloading",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                    "speed": 20,
                    "eta": 3,
                }
            )
        )
        on_line("RECLIP_FINAL " + json.dumps(str(output)))
        return 0


class BlockingRunner:
    def __init__(self):
        self.started = threading.Event()

    def __call__(self, command, on_line, timeout_seconds, **kwargs):
        cancel_event = kwargs["cancel_event"]
        self.started.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        raise ProcessCancelled()


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "downloads"
        self.store = JobStore(Path(self.tempdir.name) / "jobs.sqlite3")
        self.store.initialize()
        self.runner = FakeRunner()
        self.service = JobService(
            self.store,
            self.root,
            runtime_epoch="epoch-A",
            metadata_loader=lambda url: sample_info(),
            runner=self.runner,
            thread_factory=ImmediateThread,
        )

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def create_request(self):
        return {
            "url": "https://example.com/sample",
            "format": "video",
            "format_id": None,
            "title": "Sample title",
        }

    def test_failed_task_can_resume_in_same_directory(self):
        created = self.service.create(self.create_request())
        failed = self.store.get_job(created["job_id"])
        task_dir = self.root / "jobs" / created["job_id"]
        self.assertEqual(failed["state"], "failed")

        self.runner.mode = "success"
        resumed = self.service.resume(created["job_id"])

        self.assertEqual(resumed["job_id"], created["job_id"])
        self.assertEqual(resumed["attempt_no"], 2)
        completed = self.store.get_job(created["job_id"])
        self.assertEqual(completed["state"], "completed")
        self.assertTrue(task_dir.joinpath("media.mp4").exists())
        self.assertEqual(len(self.runner.calls), 2)
        self.assertEqual(
            self.runner.calls[0][self.runner.calls[0].index("-o") + 1],
            self.runner.calls[1][self.runner.calls[1].index("-o") + 1],
        )
        file_path, filename = self.service.file_path(created["job_id"])
        self.assertEqual(file_path, task_dir / "media.mp4")
        self.assertEqual(filename, "Sample title.mp4")
        self.assertIsNotNone(self.runner.kwargs[1]["deadline_tracker"])

    def test_completed_task_has_one_public_filename(self):
        self.runner.mode = "success"
        created = self.service.create(self.create_request())
        job_id = created["job_id"]
        before = self.store.get_job(job_id)
        path, filename = self.service.file_path(job_id)

        self.assertEqual(path.name, "media.mp4")
        self.assertEqual(filename, "Sample title.mp4")
        self.assertEqual(self.service.status(job_id)["filename"], filename)
        listed = self.service.list_jobs()["items"]
        self.assertEqual(
            next(j for j in listed if j["job_id"] == job_id)["filename"], filename
        )
        self.assertEqual(self.store.get_job(job_id), before)
        self.assertEqual(path.read_bytes(), b"completed media")

    def test_restart_creates_new_task_and_keeps_failed_task(self):
        created = self.service.create(self.create_request())
        restarted = self.service.restart(created["job_id"])
        self.assertNotEqual(restarted["job_id"], created["job_id"])
        self.assertEqual(self.store.get_job(created["job_id"])["state"], "failed")
        self.assertEqual(self.store.get_job(restarted["job_id"])["state"], "failed")

    def test_resume_rejects_changed_source_plan(self):
        created = self.service.create(self.create_request())

        def changed_info(url):
            data = sample_info()
            data["id"] = "changed-id"
            return data

        self.service.metadata_loader = changed_info
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.resume(created["job_id"])
        self.assertEqual(self.store.get_job(created["job_id"])["state"], "failed")

    def test_cancel_stops_current_attempt_and_marks_it_cancelled(self):
        runner = BlockingRunner()
        service = JobService(
            self.store,
            self.root,
            runtime_epoch="epoch-A",
            metadata_loader=lambda url: sample_info(),
            runner=runner,
            thread_factory=threading.Thread,
        )

        created = service.create(self.create_request())
        self.assertTrue(runner.started.wait(timeout=1))

        response = service.cancel(created["job_id"], 1)
        self.assertIn(response["state"], {"cancelling", "cancelled"})

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = self.store.get_job(created["job_id"])
            if job["state"] == "cancelled":
                break
            time.sleep(0.02)
        self.assertEqual(self.store.get_job(created["job_id"])["state"], "cancelled")
        self.assertEqual(self.store.get_job(created["job_id"])["attempt_no"], 1)


if __name__ == "__main__":
    unittest.main()
