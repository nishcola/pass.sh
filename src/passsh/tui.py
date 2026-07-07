"""Terminal UI entry point for pass.sh, built on Textual.

This is a presentation layer only: screens here call into the existing
crypto/storage/clipboard/session modules for anything that touches the
vault -- none of that logic is reimplemented here.
"""

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static

from . import session, storage

_UPDATED_AT_PLACEHOLDER = "—"  # em dash, shown when an entry predates the field


def _format_updated_at(entry: dict) -> str:
    value = entry.get("updated_at")
    if not value:
        return _UPDATED_AT_PLACEHOLDER
    return value.replace("T", " ").replace("+00:00", " UTC")


class VimDataTable(DataTable):
    """DataTable with vim-style j/k navigation added alongside the arrow-key
    defaults (BINDINGS merge across the class hierarchy, so this only adds
    to, not replaces, DataTable's own bindings)."""

    BINDINGS = [("j", "cursor_down", "Down"), ("k", "cursor_up", "Up")]


def _matches(query: str, name: str, entry: dict) -> bool:
    query = query.strip().lower()
    if not query:
        return True
    return query in name.lower() or query in entry.get("username", "").lower()


class MainScreen(Screen):
    """The app's home screen: a live-searchable, scrollable table of vault
    entries.

    Only service name, username, and last-updated time are shown here --
    never passwords.
    """

    # "app.quit" (not "quit") because Textual dispatches an action to the
    # exact node where the binding is declared -- MainScreen itself has no
    # action_quit method, only App does, so the namespace prefix is required
    # to route there instead of silently failing to dispatch.
    BINDINGS = [("q", "app.quit", "Quit")]

    # How long to wait after the last keystroke before actually re-filtering
    # and repopulating the table -- avoids redoing that work on every single
    # keystroke while the user is still typing, which would lag on a vault
    # with many entries.
    SEARCH_DEBOUNCE = 0.2

    def __init__(self, vault_path: Path, key: bytes, kdf_params: dict, entries: dict) -> None:
        super().__init__()
        self.vault_path = vault_path
        self.key = key
        self.kdf_params = kdf_params
        self.entries = entries
        self._search_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search by service or username...", id="search")
        yield VimDataTable(id="entries", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#entries", VimDataTable)
        table.add_columns("Service", "Username", "Last updated")
        self._populate(self.entries)
        self.query_one("#search", Input).focus()

    def _populate(self, entries: dict) -> None:
        table = self.query_one("#entries", VimDataTable)
        table.clear()
        for name in sorted(entries):
            entry = entries[name]
            table.add_row(name, entry.get("username", ""), _format_updated_at(entry), key=name)

    @on(Input.Changed, "#search")
    def handle_search_changed(self, event: Input.Changed) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        query = event.value
        self._search_timer = self.set_timer(
            self.SEARCH_DEBOUNCE, lambda: self._apply_filter(query)
        )

    def _apply_filter(self, query: str) -> None:
        filtered = {
            name: entry for name, entry in self.entries.items() if _matches(query, name, entry)
        }
        self._populate(filtered)


class UnlockScreen(Screen):
    """The first screen shown: prompts for the master password and unlocks
    the vault via the shared `session` module, retrying on failure."""

    def __init__(self, vault_path: Path) -> None:
        super().__init__()
        self.vault_path = vault_path

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("pass.sh", id="title"),
            Static("", id="error"),
            Input(placeholder="Master password", password=True, id="password"),
            id="unlock-form",
        )

    def on_mount(self) -> None:
        self.query_one("#password", Input).focus()

    @on(Input.Submitted, "#password")
    def handle_submit(self, event: Input.Submitted) -> None:
        password_input = event.input
        error = self.query_one("#error", Static)

        try:
            key, kdf_params, entries = session.unlock(
                self.vault_path, event.value.encode("utf-8")
            )
        except session.UnlockError as exc:
            error.update(str(exc))
            password_input.value = ""
            password_input.focus()
            return

        error.update("")
        self.app.switch_screen(
            MainScreen(self.vault_path, key, kdf_params, entries)
        )


class PassShApp(App):
    """Textual TUI for pass.sh.

    Uses Textual's screen-stack (`push_screen`/`pop_screen`/`switch_screen`)
    so later modal screens -- add/edit forms -- can be layered on top of
    `MainScreen` without restructuring navigation.
    """

    TITLE = "pass.sh"

    def __init__(self, vault_path: Path | None = None) -> None:
        super().__init__()
        self.vault_path = vault_path or storage.default_vault_path()

    def on_mount(self) -> None:
        # Skip the unlock prompt entirely if a session is already cached,
        # matching the CLI's behavior of never re-asking unnecessarily.
        cached = session.try_cached_session(self.vault_path)
        if cached is not None:
            key, kdf_params, entries = cached
            self.push_screen(MainScreen(self.vault_path, key, kdf_params, entries))
        else:
            self.push_screen(UnlockScreen(self.vault_path))


def run(vault_path: Path | None = None) -> None:
    PassShApp(vault_path).run()


if __name__ == "__main__":
    run()
