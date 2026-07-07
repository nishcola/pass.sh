"""Shared vault-unlock orchestration.

Both the CLI and the TUI need the same policy when opening a vault: try a
live cached agent session first (skipping the master password and the
expensive KDF entirely), otherwise verify the given password, rate-limiting
repeated failures, and start a session on success. This module is the one
place that policy lives, so presentation layers only ever call into it
rather than re-deriving it.
"""

from pathlib import Path

from . import agent, ratelimit, storage


class UnlockError(Exception):
    """Raised with a user-facing message: wrong password, corrupted vault,
    missing vault, or an active rate-limit lockout."""


def try_cached_session(vault_path: Path) -> tuple[bytes, dict, dict] | None:
    """Return (key, kdf_params, entries) from a live, non-expired session, or None."""
    cached = agent.get_cached_key(vault_path)
    if cached is None:
        return None

    key, kdf_params = cached
    try:
        entries = storage.read_entries(vault_path, key)
    except storage.VaultError:
        return None  # stale session (e.g. vault replaced on disk)
    return key, kdf_params, entries


def unlock(vault_path: Path, password: bytes) -> tuple[bytes, dict, dict]:
    """Verify `password` against the vault and start a cached session on success.

    Raises UnlockError (with a message safe to show the user) if currently
    rate-limited, or if the password is wrong / the vault is corrupted.
    """
    try:
        ratelimit.check(vault_path)
    except ratelimit.RateLimitedError as exc:
        raise UnlockError(str(exc)) from exc

    try:
        key, kdf_params, entries = storage.load_vault(vault_path, password)
    except storage.VaultError as exc:
        ratelimit.record_failure(vault_path)
        raise UnlockError(str(exc)) from exc

    ratelimit.record_success(vault_path)
    agent.start_session(vault_path, key, kdf_params)
    return key, kdf_params, entries
