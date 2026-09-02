"""Bounded streaming subprocess execution for ReClip downloads."""

import os
import queue
import signal
import subprocess
import threading
import time


class DeadlineTracker:
    """Track preparation, useful download progress, post-processing and hard limits."""

    def __init__(
        self,
        prepare_timeout=120,
        idle_timeout=300,
        process_timeout=1800,
        hard_timeout=21600,
        clock=time.monotonic,
    ):
        self.prepare_timeout = prepare_timeout
        self.idle_timeout = idle_timeout
        self.process_timeout = process_timeout
        self.hard_timeout = hard_timeout
        self.clock = clock
        self.started_at = clock()
        self.phase = "preparing"
        self.phase_started_at = self.started_at
        self.last_progress_at = self.started_at
        self.last_marker = None

    def observe(self, event):
        now = self.clock()
        if not isinstance(event, dict):
            return
        kind = event.get("kind")
        data = event.get("data") or {}
        if kind == "download":
            status = data.get("status")
            if status == "downloading":
                if self.phase != "downloading":
                    self.phase = "downloading"
                    self.phase_started_at = now
                marker = (data.get("downloaded_bytes"), data.get("fragment_index"))
                if marker != self.last_marker and any(value is not None for value in marker):
                    self.last_marker = marker
                    self.last_progress_at = now
            elif status == "finished":
                self.phase = "processing"
                self.phase_started_at = now
                self.last_marker = None
        elif kind == "postprocess":
            if self.phase != "processing":
                self.phase = "processing"
                self.phase_started_at = now

    def expired_reason(self, now=None):
        now = self.clock() if now is None else now
        if now - self.started_at >= self.hard_timeout:
            return "attempt_timeout"
        if self.phase == "preparing" and now - self.phase_started_at >= self.prepare_timeout:
            return "prepare_timeout"
        if self.phase == "downloading" and now - self.last_progress_at >= self.idle_timeout:
            return "idle_timeout"
        if self.phase == "processing" and now - self.phase_started_at >= self.process_timeout:
            return "process_timeout"
        return None


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


def run_streaming_process(cmd, on_line, timeout_seconds=300, *, deadline_tracker=None):
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
                timeout = subprocess.TimeoutExpired(cmd, timeout_seconds)
                timeout.reason = "attempt_timeout"
                raise timeout
            if deadline_tracker is not None:
                reason = deadline_tracker.expired_reason()
                if reason is not None:
                    _terminate_process_tree(process)
                    timeout = subprocess.TimeoutExpired(cmd, time.monotonic() - deadline + timeout_seconds)
                    timeout.reason = reason
                    raise timeout
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
