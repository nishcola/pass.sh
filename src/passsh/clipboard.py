"""Clipboard helpers with an auto-clearing background timer.

The copy happens in the CLI process itself. The clear runs in a fully
detached worker process (this same module invoked as `python -m
passsh.clipboard <delay>`), so the delay never blocks -- or even requires
-- the CLI process to stay alive: the shell prompt returns immediately
after the copy, and the clear still fires later on its own, independent of
the parent's lifetime.
"""

import subprocess
import sys
import time

import pyperclip

DEFAULT_CLEAR_DELAY = 15.0


class ClipboardUnavailableError(Exception):
    """Raised when the system has no usable clipboard mechanism."""


def copy_with_autoclear(value: str, delay: float = DEFAULT_CLEAR_DELAY) -> None:
    """Copy `value` to the clipboard and spawn a detached worker that clears
    it after `delay` seconds, but only if the clipboard still holds `value`.

    The worker is a separate OS process, started detached from this one (own
    session, stdio redirected away), so this function returns as soon as the
    process is spawned -- it never waits on `delay`. The value is passed to
    the worker over a pipe on stdin, never as a command-line argument or
    environment variable, so it does not appear in `ps`/process-listing
    output.
    """
    try:
        pyperclip.copy(value)
    except pyperclip.PyperclipException as exc:
        raise ClipboardUnavailableError(str(exc)) from exc

    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "-m", "passsh.clipboard", str(delay)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **popen_kwargs,
    )
    proc.stdin.write(value.encode("utf-8"))
    proc.stdin.close()


def _run_clear_worker(delay: float) -> None:
    value = sys.stdin.buffer.read().decode("utf-8")
    time.sleep(delay)
    try:
        if pyperclip.paste() == value:
            pyperclip.copy("")
    except pyperclip.PyperclipException:
        pass


if __name__ == "__main__":
    _run_clear_worker(float(sys.argv[1]))
