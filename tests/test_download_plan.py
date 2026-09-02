import tempfile
import unittest
from pathlib import Path

from download_plan import (
    build_download_command,
    resolve_plan,
    validate_final_file,
    validate_resume_plan,
)


URL = "https://example.com/sample"


def sample_info():
    return {
        "id": "sample-id",
        "extractor_key": "generic",
        "formats": [
            {
                "format_id": "18",
                "ext": "mp4",
                "protocol": "https",
                "height": 360,
                "tbr": 500,
                "vcodec": "avc1",
                "acodec": "mp4a",
                "filesize": 100,
            },
            {
                "format_id": "137",
                "ext": "mp4",
                "protocol": "https",
                "height": 1080,
                "tbr": 5000,
                "vcodec": "avc1",
                "acodec": "none",
                "filesize": 1000,
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "protocol": "https",
                "tbr": 130,
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "filesize": 200,
            },
        ],
    }


class DownloadPlanTests(unittest.TestCase):
    def test_resolve_plan_pins_video_and_audio_ids(self):
        plan = resolve_plan(URL, "video", None, info=sample_info())
        self.assertEqual(plan["video_id"], "sample-id")
        self.assertEqual(plan["format_selector"], "137+140")
        self.assertEqual([item["id"] for item in plan["formats"]], ["137", "140"])

    def test_audio_plan_pins_audio_format(self):
        plan = resolve_plan(URL, "audio", None, info=sample_info())
        self.assertEqual(plan["format_selector"], "140")
        self.assertEqual(plan["formats"][0]["acodec"], "mp4a.40.2")

    def test_command_uses_fixed_selector_and_argument_separator(self):
        plan = resolve_plan(URL, "video", None, info=sample_info())
        with tempfile.TemporaryDirectory() as directory:
            command = build_download_command(plan, Path(directory))
        self.assertIn("137+140", command)
        self.assertEqual(command[-2:], ["--", URL])
        self.assertIn("--continue", command)
        self.assertIn("--part", command)
        self.assertNotIn(URL, command[:-2])

    def test_changed_format_cannot_resume(self):
        stored = resolve_plan(URL, "video", None, info=sample_info())
        changed_info = sample_info()
        changed_info["formats"][1] = {
            **changed_info["formats"][1],
            "format_id": "248",
        }
        current = resolve_plan(URL, "video", None, info=changed_info)
        with self.assertRaisesRegex(ValueError, "changed"):
            validate_resume_plan(stored, current)

    def test_final_path_cannot_escape_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            root.mkdir()
            outside = Path(directory) / "outside.mp4"
            outside.write_bytes(b"media")
            with self.assertRaises(ValueError):
                validate_final_file(root, outside, "video")

    def test_final_path_returns_task_relative_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            root.mkdir()
            output = root / "media.mp4"
            output.write_bytes(b"media")
            result = validate_final_file(root, output, "video")
        self.assertEqual(result["relative_path"], "media.mp4")
        self.assertEqual(result["filename"], "media.mp4")
        self.assertEqual(result["size_bytes"], 5)


if __name__ == "__main__":
    unittest.main()
