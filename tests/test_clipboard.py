import io
import subprocess
import sys
import time

import pyperclip
import pytest

from passsh import clipboard


class _FakeClipboard:
    def __init__(self):
        self.value = ""

    def copy(self, text):
        self.value = text

    def paste(self):
        return self.value


@pytest.fixture
def fake_clipboard(monkeypatch):
    fake = _FakeClipboard()
    monkeypatch.setattr(clipboard.pyperclip, "copy", fake.copy)
    monkeypatch.setattr(clipboard.pyperclip, "paste", fake.paste)
    return fake


class _FakeStdin:
    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


class _FakePopen:
    """Records how it was invoked instead of spawning a real process."""

    last_instance = None

    def __init__(self, args, stdin=None, stdout=None, stderr=None, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None  # let the test inspect it after "close"
        _FakePopen.last_instance = self


def test_copy_sets_clipboard_immediately(fake_clipboard, monkeypatch):
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)
    clipboard.copy_with_autoclear("s3cr3t", delay=10)
    assert fake_clipboard.paste() == "s3cr3t"


def test_copy_with_autoclear_does_not_block_caller(fake_clipboard, monkeypatch):
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)

    start = time.monotonic()
    clipboard.copy_with_autoclear("s3cr3t", delay=30)
    elapsed = time.monotonic() - start

    assert elapsed < 1, "copy_with_autoclear must return immediately, not wait for `delay`"


def test_spawns_detached_worker_with_value_over_stdin(fake_clipboard, monkeypatch):
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)

    clipboard.copy_with_autoclear("s3cr3t", delay=7)

    proc = _FakePopen.last_instance
    assert proc.args == [sys.executable, "-m", "passsh.clipboard", "7"]
    assert proc.stdin.getvalue() == b"s3cr3t"
    # value must never be passed as an argv element (visible in `ps`)
    assert "s3cr3t" not in proc.args
    if sys.platform == "win32":
        assert proc.kwargs["creationflags"] == (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        assert proc.kwargs["start_new_session"] is True


def test_clipboard_unavailable_raises(monkeypatch):
    def _raise_copy(_text):
        raise pyperclip.PyperclipException("no clipboard mechanism found")

    monkeypatch.setattr(clipboard.pyperclip, "copy", _raise_copy)

    with pytest.raises(clipboard.ClipboardUnavailableError):
        clipboard.copy_with_autoclear("s3cr3t", delay=10)


# --- worker-side behavior (runs in the detached child process for real) ---


def test_worker_clears_unchanged_clipboard(fake_clipboard, monkeypatch):
    fake_clipboard.copy("s3cr3t")
    monkeypatch.setattr(clipboard.sys, "stdin", _FakeStdin(b"s3cr3t"))

    clipboard._run_clear_worker(delay=0.01)

    assert fake_clipboard.paste() == ""


def test_worker_leaves_changed_clipboard_alone(fake_clipboard, monkeypatch):
    fake_clipboard.copy("s3cr3t")
    monkeypatch.setattr(clipboard.sys, "stdin", _FakeStdin(b"s3cr3t"))

    def _sleep_and_change(_seconds):
        fake_clipboard.copy("something-else")  # user copies something else meanwhile

    monkeypatch.setattr(clipboard.time, "sleep", _sleep_and_change)

    clipboard._run_clear_worker(delay=0.01)

    assert fake_clipboard.paste() == "something-else"
