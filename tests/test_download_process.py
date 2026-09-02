import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

from download_process import run_streaming_process


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "fake_downloader.py"


class ProcessTests(unittest.TestCase):
    def test_streams_lines_before_process_exits(self):
        lines = []
        started = time.monotonic()
        code = run_streaming_process(
            [sys.executable, str(FIXTURE), "normal"],
            lines.append,
            timeout_seconds=1,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertLess(elapsed, 1)

    def test_silent_process_times_out(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_streaming_process(
                [sys.executable, str(FIXTURE), "silent"],
                lambda line: None,
                timeout_seconds=0.2,
            )
        self.assertLess(time.monotonic() - started, 5)

    def test_returns_nonzero_exit_code_and_keeps_error_line(self):
        lines = []
        code = run_streaming_process(
            [sys.executable, str(FIXTURE), "fail"],
            lines.append,
            timeout_seconds=1,
        )
        self.assertEqual(code, 2)
        self.assertIn("ERROR: controlled test failure", lines)

    def test_callback_error_terminates_the_process(self):
        def fail_callback(line):
            raise RuntimeError("callback failed")

        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            run_streaming_process(
                [sys.executable, str(FIXTURE), "normal"],
                fail_callback,
                timeout_seconds=1,
            )

    def test_large_output_does_not_deadlock(self):
        lines = []
        code = run_streaming_process(
            [sys.executable, str(FIXTURE), "flood"],
            lines.append,
            timeout_seconds=5,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 10_000)

    def test_timeout_terminates_a_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = pathlib.Path(directory) / "child-finished.txt"
            with self.assertRaises(subprocess.TimeoutExpired):
                run_streaming_process(
                    [sys.executable, str(FIXTURE), "spawn-child", str(marker)],
                    lambda line: None,
                    timeout_seconds=0.2,
                )
            time.sleep(0.3)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
