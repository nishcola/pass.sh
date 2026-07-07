"""CLI entry point for pass.sh."""

from getpass import getpass
from pathlib import Path

import click

from . import agent, clipboard, generator, session, storage

_vault_option = click.option(
    "--vault",
    "vault_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to the vault file (default: ~/.passsh/vault.json).",
)


def _resolve_path(vault_path: Path | None) -> Path:
    return vault_path or storage.default_vault_path()


def _open_vault(path: Path) -> tuple[bytes, dict, dict]:
    cached = session.try_cached_session(path)
    if cached is not None:
        return cached

    password = getpass("Master password: ")
    try:
        return session.unlock(path, password.encode("utf-8"))
    except session.UnlockError as exc:
        raise click.ClickException(str(exc))


def _prompt_new_password(label: str) -> str:
    password = getpass(f"{label}: ")
    confirm = getpass("Confirm password: ")
    if password != confirm:
        raise click.ClickException("Passwords do not match.")
    return password


@click.group()
def main() -> None:
    pass


@main.command()
@_vault_option
@click.option("--force", is_flag=True, help="Overwrite an existing vault.")
def init(vault_path: Path | None, force: bool) -> None:
    """Create a new empty encrypted vault."""
    path = _resolve_path(vault_path)

    if storage.vault_exists(path) and not force:
        raise click.ClickException(
            f"Vault already exists at {path}. Use --force to overwrite."
        )

    password = _prompt_new_password("Master password")
    if not password:
        raise click.ClickException("Master password cannot be empty.")

    storage.create_vault(path, password.encode("utf-8"))
    click.echo(f"Vault created at {path}")


@main.command()
@_vault_option
@click.argument("name")
@click.option("--username", default="", help="Username or account identifier.")
@click.option("--notes", default="", help="Optional freeform notes.")
@click.option("--force", is_flag=True, help="Overwrite an existing entry.")
def add(vault_path: Path | None, name: str, username: str, notes: str, force: bool) -> None:
    """Add a new entry to the vault."""
    path = _resolve_path(vault_path)
    key, kdf_params, entries = _open_vault(path)

    if name in entries and not force:
        raise click.ClickException(f"Entry '{name}' already exists. Use --force to overwrite.")

    entry_password = _prompt_new_password(f"Password for '{name}'")
    entries[name] = {"username": username, "password": entry_password, "notes": notes}
    storage.save_vault(path, key, kdf_params, entries)
    click.echo(f"Added '{name}'.")


@main.command()
@_vault_option
@click.argument("name")
@click.option(
    "--copy/--no-copy",
    default=True,
    help="Copy the password to the clipboard instead of printing it (default: copy).",
)
@click.option(
    "--clear-delay",
    type=float,
    default=clipboard.DEFAULT_CLEAR_DELAY,
    show_default=True,
    help="Seconds before the clipboard is auto-cleared (only applies with --copy).",
)
def get(vault_path: Path | None, name: str, copy: bool, clear_delay: float) -> None:
    """Show a stored entry. The password is copied to the clipboard by default."""
    path = _resolve_path(vault_path)
    _key, _kdf_params, entries = _open_vault(path)

    entry = entries.get(name)
    if entry is None:
        raise click.ClickException(f"No entry named '{name}'.")

    click.echo(f"Name:     {name}")
    click.echo(f"Username: {entry.get('username', '')}")
    if entry.get("notes"):
        click.echo(f"Notes:    {entry['notes']}")

    password = entry.get("password", "")
    if not copy:
        click.echo(f"Password: {password}")
        return

    try:
        clipboard.copy_with_autoclear(password, delay=clear_delay)
    except clipboard.ClipboardUnavailableError as exc:
        click.echo(f"Warning: clipboard unavailable ({exc}); printing password instead.", err=True)
        click.echo(f"Password: {password}")
        return

    click.echo(f"Password copied to clipboard (clears in {clear_delay:.0f}s).")


@main.command(name="list")
@_vault_option
def list_entries(vault_path: Path | None) -> None:
    """List all entry names."""
    path = _resolve_path(vault_path)
    _key, _kdf_params, entries = _open_vault(path)

    if not entries:
        click.echo("Vault is empty.")
        return

    for name in sorted(entries):
        username = entries[name].get("username", "")
        click.echo(f"{name}  ({username})" if username else name)


@main.command()
@_vault_option
@click.argument("name")
@click.option("--username", default=None, help="New username.")
@click.option("--notes", default=None, help="New notes.")
@click.option("--password", "change_password", is_flag=True, help="Prompt for a new password.")
def update(
    vault_path: Path | None,
    name: str,
    username: str | None,
    notes: str | None,
    change_password: bool,
) -> None:
    """Update fields on an existing entry."""
    path = _resolve_path(vault_path)
    key, kdf_params, entries = _open_vault(path)

    entry = entries.get(name)
    if entry is None:
        raise click.ClickException(f"No entry named '{name}'.")

    if username is not None:
        entry["username"] = username
    if notes is not None:
        entry["notes"] = notes
    if change_password:
        entry["password"] = _prompt_new_password(f"New password for '{name}'")

    if username is None and notes is None and not change_password:
        raise click.ClickException("Nothing to update: pass --username, --notes, or --password.")

    storage.save_vault(path, key, kdf_params, entries)
    click.echo(f"Updated '{name}'.")


@main.command()
@_vault_option
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def delete(vault_path: Path | None, name: str, yes: bool) -> None:
    """Delete an entry from the vault."""
    path = _resolve_path(vault_path)
    key, kdf_params, entries = _open_vault(path)

    if name not in entries:
        raise click.ClickException(f"No entry named '{name}'.")

    if not yes and not click.confirm(f"Delete '{name}'?"):
        click.echo("Aborted.")
        return

    del entries[name]
    storage.save_vault(path, key, kdf_params, entries)
    click.echo(f"Deleted '{name}'.")


@main.command()
@_vault_option
def lock(vault_path: Path | None) -> None:
    """Immediately end the cached session, requiring the master password next time."""
    path = _resolve_path(vault_path)
    agent.lock(path)
    click.echo("Session locked.")


@main.command()
@_vault_option
def tui(vault_path: Path | None) -> None:
    """Launch the terminal UI."""
    from . import tui as tui_module

    tui_module.run(_resolve_path(vault_path))


@main.command()
@click.option("--length", type=int, default=generator.DEFAULT_LENGTH, show_default=True)
@click.option("--symbols/--no-symbols", default=True, help="Include symbol characters.")
@click.option(
    "--exclude-ambiguous",
    is_flag=True,
    help="Exclude visually ambiguous characters (il1Lo0O).",
)
@click.option(
    "--copy/--no-copy",
    default=True,
    help="Copy the generated password to the clipboard instead of printing it.",
)
@click.option(
    "--clear-delay",
    type=float,
    default=clipboard.DEFAULT_CLEAR_DELAY,
    show_default=True,
    help="Seconds before the clipboard is auto-cleared (only applies with --copy).",
)
def generate(
    length: int, symbols: bool, exclude_ambiguous: bool, copy: bool, clear_delay: float
) -> None:
    """Generate a random password."""
    password = generator.generate_password(
        length, use_symbols=symbols, exclude_ambiguous=exclude_ambiguous
    )

    if not copy:
        click.echo(password)
        return

    try:
        clipboard.copy_with_autoclear(password, delay=clear_delay)
    except clipboard.ClipboardUnavailableError as exc:
        click.echo(f"Warning: clipboard unavailable ({exc}); printing password instead.", err=True)
        click.echo(password)
        return

    click.echo(f"Generated password copied to clipboard (clears in {clear_delay:.0f}s).")
