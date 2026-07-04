"""Session agent: caches the derived vault key in memory across CLI
invocations so the master password isn't re-entered on every command, and
auto-locks (wipes the key, exits) after IDLE_TIMEOUT seconds of inactivity.

The key is only ever held in this agent process's RAM -- never written to
disk. It runs detached from the CLI process (own session, no controlling
terminal) and is reached over a Unix domain socket scoped to the vault,
placed in a 0700 directory and chmod'd 0600, so no other local user can
connect to it.
"""

import base64
import errno
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

IDLE_TIMEOUT = 300.0  # seconds
_CONNECT_TIMEOUT = 0.5
_SPAWN_WAIT = 2.0

# Sockets live in a short, fixed system-temp path (like ssh-agent/gpg-agent)
# rather than next to the vault file: AF_UNIX paths are capped at ~104-108
# bytes on Linux/macOS, and vault files can sit arbitrarily deep (long temp
# dirs, nested project folders, etc.), so colocating would silently fail to
# bind for any sufficiently long vault path.
_AGENT_DIR = Path(tempfile.gettempdir()) / "passsh-agents"


def _socket_path(vault_path: Path) -> Path:
    digest = hashlib.sha256(str(vault_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return _AGENT_DIR / f"{digest}.sock"


def _connect(vault_path: Path) -> socket.socket | None:
    sock_path = _socket_path(vault_path)
    if not sock_path.exists():
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(_CONNECT_TIMEOUT)
    try:
        sock.connect(str(sock_path))
    except OSError:
        sock.close()
        return None
    return sock


def _request(vault_path: Path, message: dict) -> dict | None:
    sock = _connect(vault_path)
    if sock is None:
        return None
    try:
        sock.sendall(json.dumps(message).encode("utf-8") + b"\n")
        line = sock.makefile("r").readline()
        if not line:
            return None
        return json.loads(line)
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        sock.close()


def get_cached_key(vault_path: Path) -> tuple[bytes, dict] | None:
    """Return (key, kdf_params) from a live, non-expired session, or None.

    A successful call counts as activity and refreshes the idle timer.
    """
    response = _request(vault_path, {"op": "get_key"})
    if response and response.get("ok"):
        return base64.b64decode(response["key"]), response["kdf_params"]
    return None


def start_session(vault_path: Path, key: bytes, kdf_params: dict) -> None:
    """Cache `key` in a (possibly newly spawned) detached agent."""
    message = {
        "op": "set_key",
        "key": base64.b64encode(key).decode("ascii"),
        "kdf_params": kdf_params,
    }

    if _request(vault_path, message) is not None:
        return  # an already-running agent accepted it

    if sys.platform == "win32":
        return  # no Unix domain sockets; sessions simply don't persist

    subprocess.Popen(
        [sys.executable, "-m", "passsh.agent", str(vault_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.monotonic() + _SPAWN_WAIT
    while time.monotonic() < deadline:
        if _request(vault_path, message) is not None:
            return
        time.sleep(0.05)


def lock(vault_path: Path) -> None:
    """Ask a running agent to immediately wipe its cached key and exit."""
    _request(vault_path, {"op": "lock"})


class _Session:
    def __init__(self) -> None:
        self.key: bytes | None = None
        self.kdf_params: dict | None = None
        self.last_activity = time.monotonic()
        self.lock = threading.Lock()


def _is_idle(session: _Session, idle_timeout: float) -> bool:
    return (time.monotonic() - session.last_activity) > idle_timeout


def _handle_message(session: _Session, message: dict, idle_timeout: float) -> dict:
    """Pure request handler, factored out so it's unit-testable without sockets."""
    op = message.get("op")

    with session.lock:
        if op == "set_key":
            session.key = base64.b64decode(message["key"])
            session.kdf_params = message["kdf_params"]
            session.last_activity = time.monotonic()
            return {"ok": True}

        if op == "get_key":
            if session.key is None or _is_idle(session, idle_timeout):
                session.key = None
                return {"ok": False, "reason": "locked"}
            session.last_activity = time.monotonic()
            return {
                "ok": True,
                "key": base64.b64encode(session.key).decode("ascii"),
                "kdf_params": session.kdf_params,
            }

        if op == "lock":
            session.key = None
            return {"ok": True}

        return {"ok": False, "reason": "unknown op"}


def _serve(vault_path: Path, idle_timeout: float = IDLE_TIMEOUT) -> None:
    sock_path = _socket_path(vault_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(sock_path.parent, 0o700)

    if sock_path.exists():
        if _connect(vault_path) is not None:
            return  # another agent is already live for this vault
        sock_path.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(sock_path))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return  # lost the race to another agent
        print(f"passsh agent: failed to bind {sock_path}: {exc}", file=sys.stderr)
        return
    os.chmod(sock_path, 0o600)
    server.listen(4)
    server.settimeout(1.0)

    session = _Session()

    try:
        while True:
            if session.key is not None and _is_idle(session, idle_timeout):
                with session.lock:
                    session.key = None  # proactively wipe on timeout, even with no requests
            if session.key is None and _is_idle(session, idle_timeout):
                break
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            with conn:
                line = conn.makefile("r").readline()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    conn.sendall(json.dumps({"ok": False, "reason": "bad request"}).encode() + b"\n")
                    continue
                response = _handle_message(session, message, idle_timeout)
                conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
    finally:
        server.close()
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    _serve(Path(sys.argv[1]))
