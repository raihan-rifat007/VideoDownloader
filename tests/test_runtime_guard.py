import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_guard import RuntimeGuard, runtime_epoch


class RuntimeGuardTests(unittest.TestCase):
    def test_epoch_contains_stable_boot_and_pid1_components_on_linux(self):
        if os.name != "posix":
            self.skipTest("container runtime identity is Linux-specific")
        with patch("runtime_guard._read_boot_id", return_value="boot-A"), patch(
            "runtime_guard._read_pid1_starttime", return_value="12345"
        ):
            self.assertEqual(runtime_epoch(), "boot-A:12345")

    def test_epoch_read_failure_is_explicit(self):
        with patch("runtime_guard._read_boot_id", side_effect=OSError("missing")):
            with self.assertRaisesRegex(RuntimeError, "runtime identity"):
                runtime_epoch()

    @unittest.skipUnless(os.name == "posix", "requires the Linux file lock")
    def test_second_process_guard_cannot_acquire_same_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.lock"
            first = RuntimeGuard(path)
            second = RuntimeGuard(path)
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
