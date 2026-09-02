"""Bounded streaming subprocess execution for ReClip downloads."""

import os
import queue
import signal
import subprocess
import threading
import time


def _windows_taskkill(pid, timeout):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False
    return True


def _terminate_process_tree(process):
    if process.poll() is not None:
        return

    if os.name == "nt":
        _windows_taskkill(process.pid, timeout=1)
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()

    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            _windows_taskkill(process.pid, timeout=0.5)
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def run_streaming_process(cmd, on_line, timeout_seconds=300):
    """Run *cmd*, forwarding output lines to *on_line* before exit.

    The process is started without a shell. The main thread owns the timeout
    clock while a reader thread consumes the merged output pipe, so a silent
    or stalled child cannot make timeout handling wait forever.
    """
    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "shell": False,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(cmd, **popen_kwargs)
    callback_errors = queue.Queue(maxsize=1)

    def consume_output():
        try:
            assert process.stdout is not None
            for line in process.stdout:
                on_line(line.rstrip("\r\n"))
        except BaseException as exc:  # propagate callback and pipe failures
            try:
                callback_errors.put_nowait(exc)
            except queue.Full:
                pass
        finally:
            if process.stdout is not None:
                process.stdout.close()

    reader = threading.Thread(target=consume_output, name="reclip-output-reader")
    reader.daemon = True
    reader.start()

    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None:
            try:
                callback_error = callback_errors.get_nowait()
            except queue.Empty:
                callback_error = None
            else:
                _terminate_process_tree(process)
                raise callback_error

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(process)
                raise subprocess.TimeoutExpired(cmd, timeout_seconds)
            time.sleep(min(0.05, remaining))

        reader.join(timeout=1)
        if reader.is_alive():
            _terminate_process_tree(process)
            reader.join(timeout=1)
        try:
            callback_error = callback_errors.get_nowait()
        except queue.Empty:
            callback_error = None
        else:
            raise callback_error
        return process.returncode
    except BaseException:
        if process.poll() is None:
            _terminate_process_tree(process)
        reader.join(timeout=1)
        raise
