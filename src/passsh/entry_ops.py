"""Shared vault-entry create/update helpers.

Both the CLI and the TUI need identical semantics when adding or updating a
vault entry (timestamping, which fields get touched) -- this is the one
place that logic lives, so presentation layers call into it rather than
each re-deriving their own copy.
"""

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_entry(username: str, password: str, notes: str) -> dict:
    """Construct a brand-new entry dict, stamped with the current time."""
    return {
        "username": username,
        "password": password,
        "notes": notes,
        "updated_at": now_iso(),
    }


def apply_update(
    entry: dict,
    *,
    username: str | None = None,
    notes: str | None = None,
    password: str | None = None,
) -> None:
    """Mutate `entry` in place. `None` for a field means leave it untouched;
    any other value (including "") sets it. Always refreshes `updated_at`."""
    if username is not None:
        entry["username"] = username
    if notes is not None:
        entry["notes"] = notes
    if password is not None:
        entry["password"] = password
    entry["updated_at"] = now_iso()
