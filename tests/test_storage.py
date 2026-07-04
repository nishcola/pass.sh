import pytest

from passsh import storage


def test_create_and_load_round_trip(tmp_path):
    vault = tmp_path / "vault.json"
    storage.create_vault(vault, b"master-pw")

    key, kdf_params, entries = storage.load_vault(vault, b"master-pw")
    assert entries == {}
    assert kdf_params["algorithm"] == "argon2id"
    assert key is not None


def test_wrong_password_raises_vault_error(tmp_path):
    vault = tmp_path / "vault.json"
    storage.create_vault(vault, b"master-pw")

    with pytest.raises(storage.VaultError):
        storage.load_vault(vault, b"wrong-pw")


def test_missing_vault_raises_vault_error(tmp_path):
    with pytest.raises(storage.VaultError):
        storage.load_vault(tmp_path / "nope.json", b"anything")


def test_save_and_reload_entries(tmp_path):
    vault = tmp_path / "vault.json"
    storage.create_vault(vault, b"master-pw")
    key, kdf_params, entries = storage.load_vault(vault, b"master-pw")

    entries["github"] = {"username": "alice", "password": "hunters2", "notes": ""}
    storage.save_vault(vault, key, kdf_params, entries)

    _key2, _kdf_params2, reloaded = storage.load_vault(vault, b"master-pw")
    assert reloaded == {"github": {"username": "alice", "password": "hunters2", "notes": ""}}


def test_read_entries_with_cached_key_skips_kdf(tmp_path):
    vault = tmp_path / "vault.json"
    storage.create_vault(vault, b"master-pw")
    key, kdf_params, entries = storage.load_vault(vault, b"master-pw")
    entries["site"] = {"username": "bob", "password": "p", "notes": ""}
    storage.save_vault(vault, key, kdf_params, entries)

    assert storage.read_entries(vault, key) == entries


def test_read_entries_with_stale_key_raises(tmp_path):
    vault = tmp_path / "vault.json"
    storage.create_vault(vault, b"master-pw")

    wrong_key = bytes(32)
    with pytest.raises(storage.VaultError):
        storage.read_entries(vault, wrong_key)


def test_vault_directory_is_owner_only(tmp_path):
    vault = tmp_path / "sub" / "vault.json"
    storage.create_vault(vault, b"master-pw")

    mode = vault.parent.stat().st_mode & 0o777
    assert mode == 0o700
