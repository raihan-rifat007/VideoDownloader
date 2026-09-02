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

    def test_cancel_blocks_late_progress(self):
        record = job_record(state="downloading") | {
            "progress_json": {"status": "downloading", "percent": 25.0},
            "error_code": None,
            "error_message": None,
        }
        self.store.insert_job(record)
        self.assertTrue(self.store.request_cancel(record["job_id"], 1))
        self.assertFalse(
            self.store.update_attempt(
                record["job_id"],
                1,
                {"state": "downloading"},
                expected_states={"preparing", "downloading", "processing"},
            )
        )
        job = self.store.get_job(record["job_id"])
        self.assertEqual(job["state"], "cancelling")
        self.assertEqual(job["last_progress_json"], record["progress_json"])
        self.assertIsNone(job["progress_json"])

    def test_repeated_cancel_does_not_change_cancelling_state(self):
        record = job_record(state="preparing")
        self.store.insert_job(record)
        self.assertTrue(self.store.request_cancel(record["job_id"], 1))
        cancelled_at = self.store.get_job(record["job_id"])["updated_at"]
        self.assertFalse(self.store.request_cancel(record["job_id"], 1))
        self.assertEqual(self.store.get_job(record["job_id"])["state"], "cancelling")
        self.assertEqual(self.store.get_job(record["job_id"])["updated_at"], cancelled_at)

    def test_finalize_attempt_updates_job_and_attempt_together(self):
        record = job_record(state="processing")
        self.store.insert_job(record)
        self.assertTrue(
            self.store.finalize_attempt(
                record["job_id"],
                1,
                "completed",
                {"filename": "Sample.mp4", "final_relpath": "jobs/a/media.mp4"},
                exit_code=0,
            )
        )
        job = self.store.get_job(record["job_id"])
        self.assertEqual((job["state"], job["filename"]), ("completed", "Sample.mp4"))
        connection = self.store._connect()
        try:
            attempt = connection.execute(
                "SELECT outcome, exit_code, finished_at FROM attempts WHERE job_id=? AND attempt_no=1",
                (record["job_id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual((attempt["outcome"], attempt["exit_code"]), ("completed", 0))
        self.assertIsNotNone(attempt["finished_at"])

    def test_cancelled_finalize_requires_cancel_request(self):
        record = job_record(state="downloading")
        self.store.insert_job(record)
        self.assertFalse(
            self.store.finalize_attempt(record["job_id"], 1, "cancelled", {})
        )
        self.assertEqual(self.store.get_job(record["job_id"])["state"], "downloading")

    def test_cancel_and_finalize_have_one_winner(self):
        record = job_record(state="downloading")
        self.store.insert_job(record)
        barrier = threading.Barrier(2)
        results = []

        def request_cancel():
            barrier.wait()
            results.append(("cancel", self.store.request_cancel(record["job_id"], 1)))

        def finalize():
            barrier.wait()
            results.append(
                (
                    "finalize",
                    self.store.finalize_attempt(
                        record["job_id"], 1, "completed", {}, exit_code=0
                    ),
                )
            )

        threads = [threading.Thread(target=request_cancel), threading.Thread(target=finalize)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(result for _, result in results), 1)
        if dict(results)["cancel"]:
            self.assertTrue(
                self.store.finalize_attempt(
                    record["job_id"], 1, "cancelled", {}, exit_code=-15
                )
            )
        job = self.store.get_job(record["job_id"])
        connection = self.store._connect()
        try:
            attempt = connection.execute(
                "SELECT outcome FROM attempts WHERE job_id=? AND attempt_no=1",
                (record["job_id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(job["state"], attempt["outcome"])

    def test_cancelled_can_be_retried_and_deleted(self):
        retry_record = job_record(job_id="b" * 32, state="cancelled")
        self.store.insert_job(retry_record)
        self.assertEqual(
            self.store.claim_retry(retry_record["job_id"], 1, "epoch-A"), 2
        )

        delete_record = job_record(job_id="c" * 32, state="cancelled")
        self.store.insert_job(delete_record)
        self.assertTrue(self.store.begin_delete(delete_record["job_id"]))

    def test_initialize_upgrades_v1_without_changing_task_data(self):
        record = job_record(state="cancelled") | {
            "final_relpath": "jobs/" + "a" * 32 + "/media.mp4",
            "filename": "Sample.mp4",
        }
        self.store.insert_job(record)
        connection = self.store._connect()
        try:
            connection.execute("PRAGMA user_version=1")
        finally:
            connection.close()

        self.store.initialize()
        connection = self.store._connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 2)
        restored = self.store.get_job(record["job_id"])
        for field in ("job_id", "attempt_no", "final_relpath", "filename", "state"):
            self.assertEqual(restored[field], record[field])

    def test_initialize_rejects_higher_schema_version(self):
        connection = self.store._connect()
        try:
            connection.execute("PRAGMA user_version=3")
        finally:
            connection.close()
        with self.assertRaisesRegex(RuntimeError, "Unsupported jobs database schema: 3"):
            self.store.initialize()

    def test_recover_changed_epoch_cancels_cancelling_job(self):
        record = job_record(state="downloading") | {
            "progress_json": {"status": "downloading", "percent": 50.0},
            "error_code": None,
            "error_message": None,
        }
        self.store.insert_job(record)
        self.assertTrue(self.store.request_cancel(record["job_id"], 1))
        connection = self.store._connect()
        try:
            connection.execute(
                "INSERT INTO service_meta(key, value) VALUES('runtime_epoch', 'epoch-old')"
            )
        finally:
            connection.close()

        result = self.store.recover_active_jobs("epoch-new")
        self.assertEqual(result, {"recovered": 1, "restart_required": False})
        job = self.store.get_job(record["job_id"])
        self.assertEqual(job["state"], "cancelled")
        self.assertEqual(job["last_progress_json"], record["progress_json"])
        connection = self.store._connect()
        try:
            attempt = connection.execute(
                "SELECT outcome FROM attempts WHERE job_id=? AND attempt_no=1",
                (record["job_id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(attempt["outcome"], "cancelled")

    def test_recover_same_epoch_requires_restart_without_mutating_tasks(self):
        record = job_record(state="downloading")
        self.store.insert_job(record)
        connection = self.store._connect()
        try:
            connection.execute(
                "INSERT INTO service_meta(key, value) VALUES('runtime_epoch', 'epoch-same')"
            )
        finally:
            connection.close()

        result = self.store.recover_active_jobs("epoch-same")
        self.assertTrue(result["restart_required"])
        self.assertEqual(result["recovered"], 0)
        self.assertEqual(self.store.get_job(record["job_id"])["state"], "downloading")

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
