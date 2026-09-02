import tempfile
import threading
import unittest
from pathlib import Path

from job_store import JobStore


def job_record(job_id="a" * 32, state="failed", attempt_no=1):
    return {
        "job_id": job_id,
        "source_url": "https://example.com/sample.mp4",
        "title": "Sample",
        "format_choice": "video",
        "requested_format_id": None,
        "state": state,
        "attempt_no": attempt_no,
        "resource_json": {
            "extractor": "generic",
            "video_id": "sample",
            "formats": [],
        },
        "progress_json": None,
        "error_code": "download_failed",
        "error_message": "network interrupted",
        "final_relpath": None,
        "filename": None,
        "created_at": 1000,
        "updated_at": 1000,
    }


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.tempdir.name) / "jobs.sqlite3")
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def test_job_survives_close_and_reopen(self):
        record = job_record()
        self.store.insert_job(record)
        self.store.close()

        reopened = JobStore(Path(self.tempdir.name) / "jobs.sqlite3")
        reopened.initialize()
        self.assertEqual(reopened.get_job(record["job_id"])["title"], "Sample")
        reopened.close()

    def test_only_one_retry_claim_succeeds(self):
        record = job_record()
        self.store.insert_job(record)
        results = []
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            results.append(self.store.claim_retry(record["job_id"], 1, "epoch-A"))

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results, key=lambda value: value is None), [2, None])
        self.assertEqual(self.store.get_job(record["job_id"])["state"], "preparing")

    def test_old_attempt_cannot_update_new_attempt(self):
        record = job_record()
        self.store.insert_job(record)
        self.assertEqual(self.store.claim_retry(record["job_id"], 1, "epoch-A"), 2)
        self.assertFalse(
            self.store.update_attempt(record["job_id"], 1, {"state": "completed"})
        )
        self.assertEqual(self.store.get_job(record["job_id"])["state"], "preparing")

    def test_finish_attempt_records_exit_code(self):
        record = job_record(state="preparing")
        self.store.insert_job(record)
        self.assertTrue(self.store.finish_attempt(record["job_id"], 1, "failed", 2))
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT outcome, exit_code FROM attempts WHERE job_id=? AND attempt_no=1",
                (record["job_id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual((row["outcome"], row["exit_code"]), ("failed", 2))

    def test_list_jobs_has_stable_cursor(self):
        for index in range(3):
            self.store.insert_job(
                job_record(job_id=f"{index + 1:032x}")
                | {"created_at": 1000 - index, "updated_at": 1000 - index}
            )
        page = self.store.list_jobs(limit=2)
        self.assertEqual(len(page["items"]), 2)
        self.assertIsNotNone(page["next_cursor"])
        next_page = self.store.list_jobs(limit=2, cursor=page["next_cursor"])
        self.assertEqual(
            [item["job_id"] for item in next_page["items"]], [f"{3:032x}"]
        )

    def test_delete_tombstone_clears_sensitive_task_fields(self):
        record = job_record(state="completed") | {
            "final_relpath": "jobs/" + "a" * 32 + "/media.mp4",
            "filename": "Sample.mp4",
        }
        self.store.insert_job(record)
        self.assertTrue(self.store.begin_delete(record["job_id"]))
        self.store.finish_delete(record["job_id"])
        deleted = self.store.get_job(record["job_id"])
        self.assertEqual(deleted["state"], "deleted")
        self.assertIsNone(deleted["source_url"])
        self.assertIsNone(deleted["title"])
        self.assertIsNone(deleted["resource_json"])
        self.assertIsNone(deleted["final_relpath"])


if __name__ == "__main__":
    unittest.main()
