import unittest

from download_filename import build_download_filename, job_download_filename


JOB_ID = "12345678" + "a" * 24


class DownloadFilenameTests(unittest.TestCase):
    def test_expected_names(self):
        cases = [
            ("熊猫的一天", ".mp4", "熊猫的一天.mp4"),
            ("熊猫的一天", ".mp3", "熊猫的一天.mp3"),
            ("示例.MP4", ".mp4", "示例.mp4"),
            ("CON", ".mp4", "视频-CON.mp4"),
            ("CON.notes", ".mp4", "视频-CON.notes.mp4"),
            (None, ".mp4", "视频-12345678.mp4"),
            ("///???", ".mp4", "视频-12345678.mp4"),
        ]
        for title, extension, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    build_download_filename(title, JOB_ID, extension), expected
                )

    def test_long_title_preserves_extension(self):
        name = build_download_filename("中" * 500, JOB_ID, ".mp4")
        self.assertLessEqual(len(name.encode("utf-8")), 180)
        self.assertTrue(name.endswith(".mp4"))

    def test_legacy_record_is_not_mutated(self):
        job = {
            "job_id": JOB_ID,
            "state": "completed",
            "title": "旧视频",
            "filename": "media.mp4",
            "final_relpath": "jobs/x/media.mp4",
        }
        before = dict(job)
        self.assertEqual(job_download_filename(job), "旧视频.mp4")
        self.assertEqual(job, before)

    def test_unknown_extension_is_not_disguised(self):
        with self.assertRaises(ValueError):
            build_download_filename("示例", JOB_ID, ".exe")


if __name__ == "__main__":
    unittest.main()
