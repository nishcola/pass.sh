import base64
import json

import pytest
from click.testing import CliRunner

from passsh import agent
from passsh.cli import main

MASTER_PW = "correct horse battery staple"


@pytest.fixture
def vault_path(tmp_path):
    path = tmp_path / "vault.json"
    yield path
    agent.lock(path)  # end any session this test spawned, so it doesn't linger


def _invoke(runner, args, input_text=""):
    return runner.invoke(main, args, input=input_text)


def _init_vault(runner, vault):
    result = _invoke(runner, ["init", "--vault", str(vault)], f"{MASTER_PW}\n{MASTER_PW}\n")
    assert result.exit_code == 0, result.output


def _add_entry(runner, vault, name, *, master_pw=MASTER_PW, username=None, password="hunter2"):
    args = ["add", "--vault", str(vault), name]
    if username:
        args += ["--username", username]
    result = _invoke(runner, args, f"{master_pw}\n{password}\n{password}\n")
    assert result.exit_code == 0, result.output


# --- init ---


def test_init_creates_vault(vault_path):
    runner = CliRunner()
    result = _invoke(runner, ["init", "--vault", str(vault_path)], f"{MASTER_PW}\n{MASTER_PW}\n")

    assert result.exit_code == 0
    assert vault_path.exists()
    assert "Vault created" in result.output


def test_init_refuses_to_overwrite_without_force(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)

    result = _invoke(runner, ["init", "--vault", str(vault_path)], f"{MASTER_PW}\n{MASTER_PW}\n")
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_init_password_mismatch_fails(vault_path):
    runner = CliRunner()
    result = _invoke(runner, ["init", "--vault", str(vault_path)], f"{MASTER_PW}\nsomethingelse\n")

    assert result.exit_code != 0
    assert "do not match" in result.output
    assert not vault_path.exists()


# --- add / get / list ---


def test_add_then_list(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _add_entry(runner, vault_path, "github", username="alice")

    result = _invoke(runner, ["list", "--vault", str(vault_path)], "\n")
    assert result.exit_code == 0
    assert "github  (alice)" in result.output


def test_add_duplicate_without_force_fails(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _add_entry(runner, vault_path, "github")

    result = _invoke(
        runner, ["add", "--vault", str(vault_path), "github"], "\nhunter3\nhunter3\n"
    )
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_get_no_copy_prints_password(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _add_entry(runner, vault_path, "github")

    result = _invoke(runner, ["get", "--vault", str(vault_path), "github", "--no-copy"], "\n")
    assert result.exit_code == 0
    assert "Password: hunter2" in result.output


def test_get_nonexistent_entry_fails(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)

    # No session cached yet (init doesn't open one), so this is the real prompt.
    result = _invoke(
        runner, ["get", "--vault", str(vault_path), "nope", "--no-copy"], f"{MASTER_PW}\n"
    )
    assert result.exit_code != 0
    assert "No entry named" in result.output


# --- update / delete ---


def test_update_username_and_password(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _add_entry(runner, vault_path, "github")

    # `_add_entry` already cached a session, so `update` won't re-prompt for the
    # master password here -- only the new-password + confirm prompts happen.
    result = _invoke(
        runner,
        ["update", "--vault", str(vault_path), "github", "--username", "bob", "--password"],
        "newpw\nnewpw\n",
    )
    assert result.exit_code == 0, result.output

    get_result = _invoke(runner, ["get", "--vault", str(vault_path), "github", "--no-copy"], "\n")
    assert "Username: bob" in get_result.output
    assert "Password: newpw" in get_result.output


def test_update_with_no_options_fails(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _add_entry(runner, vault_path, "github")

    result = _invoke(runner, ["update", "--vault", str(vault_path), "github"], "\n")
    assert result.exit_code != 0
    assert "Nothing to update" in result.output


def test_delete_with_yes(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _add_entry(runner, vault_path, "github")

    result = _invoke(runner, ["delete", "--vault", str(vault_path), "github", "--yes"], "\n")
    assert result.exit_code == 0

    list_result = _invoke(runner, ["list", "--vault", str(vault_path)], "\n")
    assert "Vault is empty" in list_result.output


def test_delete_requires_confirmation_without_yes(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _add_entry(runner, vault_path, "github")

    result = _invoke(runner, ["delete", "--vault", str(vault_path), "github"], "\nn\n")
    assert result.exit_code == 0
    assert "Aborted" in result.output

    list_result = _invoke(runner, ["list", "--vault", str(vault_path)], "\n")
    assert "github" in list_result.output


# --- auth, sessions, rate limiting ---


def test_wrong_master_password_rejected(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)

    result = _invoke(runner, ["list", "--vault", str(vault_path)], "wrongpassword\n")
    assert result.exit_code != 0
    assert "Incorrect master password" in result.output


def test_session_reuse_skips_second_password_prompt(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)

    first = _invoke(runner, ["list", "--vault", str(vault_path)], f"{MASTER_PW}\n")
    assert first.exit_code == 0

    second = _invoke(runner, ["list", "--vault", str(vault_path)], "")  # no password on stdin
    assert second.exit_code == 0
    assert "Master password" not in second.output


def test_lock_command_forces_reprompt(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)
    _invoke(runner, ["list", "--vault", str(vault_path)], f"{MASTER_PW}\n")

    lock_result = _invoke(runner, ["lock", "--vault", str(vault_path)], "")
    assert lock_result.exit_code == 0

    result = _invoke(runner, ["list", "--vault", str(vault_path)], "")  # cache is gone
    assert result.exit_code != 0
    assert "Master password" in result.output  # proves it actually re-prompted


def test_rate_limit_blocks_immediate_retry_after_failure(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)

    first = _invoke(runner, ["list", "--vault", str(vault_path)], "wrongpassword\n")
    assert first.exit_code != 0
    assert "Incorrect master password" in first.output

    second = _invoke(runner, ["list", "--vault", str(vault_path)], f"{MASTER_PW}\n")
    assert second.exit_code != 0
    assert "Too many failed attempts" in second.output


# --- tampered vault detection, end to end through the CLI ---


def test_tampered_vault_detected_via_cli(vault_path):
    runner = CliRunner()
    _init_vault(runner, vault_path)

    document = json.loads(vault_path.read_text())
    raw = bytearray(base64.b64decode(document["ciphertext"]))
    raw[0] ^= 0x01  # flip one bit of real ciphertext bytes
    document["ciphertext"] = base64.b64encode(bytes(raw)).decode("ascii")
    vault_path.write_text(json.dumps(document))

    result = _invoke(runner, ["list", "--vault", str(vault_path)], f"{MASTER_PW}\n")
    assert result.exit_code != 0
    assert "Incorrect master password or corrupted vault" in result.output


def test_tampered_kdf_header_detected_via_cli(vault_path):
    """Tampering with the (plaintext) KDF params must also fail, since they're bound as AAD."""
    runner = CliRunner()
    _init_vault(runner, vault_path)

    document = json.loads(vault_path.read_text())
    document["kdf"]["time_cost"] = 1  # attempt to downgrade the cost parameters
    vault_path.write_text(json.dumps(document))

    result = _invoke(runner, ["list", "--vault", str(vault_path)], f"{MASTER_PW}\n")
    assert result.exit_code != 0
    assert "Incorrect master password or corrupted vault" in result.output


# --- generate ---


def test_generate_no_copy_respects_length():
    runner = CliRunner()
    result = runner.invoke(main, ["generate", "--no-copy", "--length", "24"])
    assert result.exit_code == 0
    assert len(result.output.strip()) == 24


def test_generate_no_symbols_excludes_symbols():
    runner = CliRunner()
    result = runner.invoke(
        main, ["generate", "--no-copy", "--length", "200", "--no-symbols"]
    )
    assert result.exit_code == 0
    password = result.output.strip()
    assert all(c.isalnum() for c in password)
