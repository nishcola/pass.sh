"""Failed master-password attempt tracking with exponential backoff.

Only non-secret metadata (a failure count and a lockout deadline) is
persisted here -- never the password or a derived key -- so this does not
conflict with the "never store the master password or key" rule.
"""

import json
import time
from pathlib import Path

BASE_DELAY = 1.0  # seconds
MAX_DELAY = 60.0  # cap
MAX_TRACKED_FAILURES = 8  # 2**7 * BASE_DELAY already exceeds MAX_DELAY


class RateLimitedError(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Too many failed attempts. Try again in {retry_after:.0f}s.")


def _state_path(vault_path: Path) -> Path:
    return vault_path.with_name(vault_path.name + ".lockstate")


def _load(vault_path: Path) -> dict:
    path = _state_path(vault_path)
    if not path.exists():
        return {"failures": 0, "locked_until": 0.0}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"failures": 0, "locked_until": 0.0}


def _save(vault_path: Path, state: dict) -> None:
    _state_path(vault_path).write_text(json.dumps(state))


def check(vault_path: Path) -> None:
    """Raise RateLimitedError if currently inside a backoff lockout window."""
    state = _load(vault_path)
    remaining = state.get("locked_until", 0.0) - time.time()
    if remaining > 0:
        raise RateLimitedError(remaining)


def record_failure(vault_path: Path) -> None:
    state = _load(vault_path)
    failures = min(state.get("failures", 0) + 1, MAX_TRACKED_FAILURES)
    delay = min(BASE_DELAY * (2 ** (failures - 1)), MAX_DELAY)
    _save(vault_path, {"failures": failures, "locked_until": time.time() + delay})


def record_success(vault_path: Path) -> None:
    path = _state_path(vault_path)
    if path.exists():
        path.unlink()
