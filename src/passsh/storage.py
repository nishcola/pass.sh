"""Encrypted vault file read/write."""

import base64
import json
import os
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag

from . import crypto

VAULT_VERSION = 1
EMPTY_VAULT_CONTENTS = json.dumps({"entries": {}}).encode("utf-8")


class VaultError(Exception):
    """Raised for missing vaults, wrong passwords, or corrupted/tampered files."""


def default_vault_path() -> Path:
    return Path.home() / ".passsh" / "vault.json"


def vault_exists(path: Path) -> bool:
    return path.exists()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _kdf_header(kdf_params: dict) -> dict:
    return {"version": VAULT_VERSION, "kdf": kdf_params}


def _new_kdf_params(salt: bytes) -> dict:
    return {
        "algorithm": "argon2id",
        "salt": _b64(salt),
        "time_cost": crypto.ARGON2_TIME_COST,
        "memory_cost": crypto.ARGON2_MEMORY_COST,
        "parallelism": crypto.ARGON2_PARALLELISM,
        "key_length": crypto.KEY_SIZE,
    }


def _atomic_write_json(path: Path, document: dict) -> None:
    """Write `document` to `path` atomically (temp file + rename).

    The temp file is created in the same directory as `path` so the final
    `os.replace` is an atomic rename on the same filesystem, and it only
    ever holds the already-encrypted document -- never decrypted vault
    contents -- so no plaintext touches disk even transiently.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".vault-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(document, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def create_vault(path: Path, password: bytes) -> None:
    """Create a new empty encrypted vault at `path`.

    The derived key exists only in memory for the duration of this call and
    is never written to disk. Callers are responsible for checking
    `vault_exists` first if overwriting should be prevented.
    """
    salt = crypto.generate_salt()
    key = crypto.derive_key(password, salt)
    kdf_params = _new_kdf_params(salt)

    _write_vault(path, key, kdf_params, entries={})


def _read_document(path: Path) -> dict:
    if not vault_exists(path):
        raise VaultError(f"No vault found at {path}. Run 'pass-sh init' first.")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise VaultError(f"Vault file is corrupted: {exc}") from exc


def _decrypt_entries(document: dict, key: bytes, *, invalid_key_message: str) -> dict:
    try:
        header = _kdf_header(document["kdf"])
        aad = json.dumps(header, sort_keys=True).encode("utf-8")
        blob = base64.b64decode(document["ciphertext"])
    except (KeyError, ValueError) as exc:
        raise VaultError(f"Vault file is corrupted: {exc}") from exc

    try:
        plaintext = crypto.decrypt(blob, key, aad)
    except InvalidTag as exc:
        raise VaultError(invalid_key_message) from exc

    try:
        return json.loads(plaintext)["entries"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise VaultError(f"Vault file is corrupted: {exc}") from exc


def load_vault(path: Path, password: bytes) -> tuple[bytes, dict, dict]:
    """Decrypt the vault at `path` with `password`, returning (key, kdf_params, entries).

    `key` and `kdf_params` should be passed back into `save_vault` so a
    re-save reuses the same salt/cost parameters the vault was created with.
    """
    document = _read_document(path)

    try:
        kdf_params = document["kdf"]
        salt = base64.b64decode(kdf_params["salt"])
    except (KeyError, ValueError) as exc:
        raise VaultError(f"Vault file is corrupted: {exc}") from exc

    key = crypto.derive_key(
        password,
        salt,
        time_cost=kdf_params["time_cost"],
        memory_cost=kdf_params["memory_cost"],
        parallelism=kdf_params["parallelism"],
    )

    entries = _decrypt_entries(
        document, key, invalid_key_message="Incorrect master password or corrupted vault."
    )
    return key, kdf_params, entries


def read_entries(path: Path, key: bytes) -> dict:
    """Decrypt vault entries with an already-derived `key`.

    Used when a session agent supplies a cached key, skipping the
    (deliberately expensive) Argon2id derivation on every command.
    """
    document = _read_document(path)
    return _decrypt_entries(
        document, key, invalid_key_message="Cached session key no longer matches the vault."
    )


def _write_vault(path: Path, key: bytes, kdf_params: dict, entries: dict) -> None:
    header = _kdf_header(kdf_params)
    aad = json.dumps(header, sort_keys=True).encode("utf-8")
    plaintext = json.dumps({"entries": entries}).encode("utf-8")
    blob = crypto.encrypt(plaintext, key, aad)

    document = dict(header)
    document["encryption"] = {"algorithm": "AES-256-GCM"}
    document["ciphertext"] = _b64(blob)

    _atomic_write_json(path, document)


def save_vault(path: Path, key: bytes, kdf_params: dict, entries: dict) -> None:
    """Re-encrypt `entries` under `key` and atomically overwrite the vault."""
    _write_vault(path, key, kdf_params, entries)
