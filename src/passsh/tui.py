"""Terminal UI entry point for pass.sh, built on Textual.

This is a presentation layer only: screens here call into the existing
crypto/storage/clipboard/session modules for anything that touches the
vault -- none of that logic is reimplemented here.
"""

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from . import clipboard, entry_ops, session, storage

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


class EntryFormScreen(ModalScreen[bool]):
    """Modal form for adding a new entry or editing an existing one.

    Add mode (`entry_name=None`): all fields start blank, a password is
    required, and the service name must be non-empty and not already taken.

    Edit mode (`entry_name=<name>`): the service name is fixed (renaming
    isn't supported, matching the CLI's `update` command), username/notes
    are pre-filled, and the password field is left blank -- submitting with
    it blank keeps the existing password unchanged, exactly like the CLI's
    `update --password` flag being optional.

    On save, mutates `entries` in place and persists via `storage.save_vault`
    -- through `entry_ops.build_entry`/`apply_update`, the same functions the
    CLI's `add`/`update` commands use. Dismisses with True if something was
    saved, False if cancelled, so the caller knows whether to refresh.
    """

    BINDINGS = [("escape", "dismiss_form", "Cancel")]

    DEFAULT_CSS = """
    EntryFormScreen {
        align: center middle;
    }
    #entry-form {
        width: 60;
        height: auto;
        border: thick $primary;
        padding: 1 2;
        background: $surface;
    }
    #password-row {
        height: 3;
    }
    #password-row Input {
        width: 1fr;
    }
    #form-buttons {
        height: 3;
        align: right middle;
    }
    #form-error {
        color: $error;
    }
    """

    def __init__(
        self,
        vault_path: Path,
        key: bytes,
        kdf_params: dict,
        entries: dict,
        *,
        entry_name: str | None = None,
    ) -> None:
        super().__init__()
        self.vault_path = vault_path
        self.key = key
        self.kdf_params = kdf_params
        self.entries = entries
        self.entry_name = entry_name

    @property
    def is_edit(self) -> bool:
        return self.entry_name is not None

    def compose(self) -> ComposeResult:
        existing = self.entries.get(self.entry_name, {}) if self.is_edit else {}
        title = f"Edit '{self.entry_name}'" if self.is_edit else "Add entry"
        password_placeholder = "(leave blank to keep current)" if self.is_edit else "Password"

        with Vertical(id="entry-form"):
            yield Static(title, id="form-title")
            yield Static("", id="form-error")
            yield Label("Service")
            yield Input(
                value=self.entry_name or "",
                placeholder="Service name",
                id="service",
                disabled=self.is_edit,
            )
            yield Label("Username")
            yield Input(value=existing.get("username", ""), placeholder="Username", id="username")
            yield Label("Password")
            with Horizontal(id="password-row"):
                yield Input(placeholder=password_placeholder, password=True, id="password")
                yield Button("Show", id="toggle-password")
            yield Label("Notes")
            yield Input(value=existing.get("notes", ""), placeholder="Notes", id="notes")
            with Horizontal(id="form-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def on_mount(self) -> None:
        first_field = "username" if self.is_edit else "service"
        self.query_one(f"#{first_field}", Input).focus()

    @on(Button.Pressed, "#toggle-password")
    def toggle_password_visibility(self) -> None:
        password_input = self.query_one("#password", Input)
        password_input.password = not password_input.password
        self.query_one("#toggle-password", Button).label = (
            "Hide" if not password_input.password else "Show"
        )

    @on(Button.Pressed, "#cancel")
    def handle_cancel(self) -> None:
        self.dismiss(False)

    def action_dismiss_form(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#save")
    def handle_save(self) -> None:
        error = self.query_one("#form-error", Static)
        username = self.query_one("#username", Input).value
        password = self.query_one("#password", Input).value
        notes = self.query_one("#notes", Input).value

        if self.is_edit:
            name = self.entry_name
            entry = self.entries[name]
            entry_ops.apply_update(
                entry, username=username, notes=notes, password=password or None
            )
        else:
            name = self.query_one("#service", Input).value.strip()
            if not name:
                error.update("Service name is required.")
                return
            if name in self.entries:
                error.update(f"Entry '{name}' already exists.")
                return
            if not password:
                error.update("Password is required.")
                return
            self.entries[name] = entry_ops.build_entry(username, password, notes)

        storage.save_vault(self.vault_path, self.key, self.kdf_params, self.entries)
        self.dismiss(True)


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Yes/no confirmation modal for deleting an entry. Dismisses with True
    if the user confirmed, False otherwise (button, escape, or click-away
    all count as "no")."""

    BINDINGS = [("escape", "dismiss_no", "Cancel")]

    DEFAULT_CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: 50;
        height: auto;
        border: thick $error;
        padding: 1 2;
        background: $surface;
    }
    #confirm-buttons {
        height: 3;
        align: right middle;
    }
    """

    def __init__(self, entry_name: str) -> None:
        super().__init__()
        self.entry_name = entry_name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(
                f"Delete '{self.entry_name}'? This cannot be undone.", id="confirm-message"
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Delete", id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    @on(Button.Pressed, "#cancel")
    def handle_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm")
    def handle_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss_no(self) -> None:
        self.dismiss(False)


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
    #
    # Single-letter bindings (a/c/d/q) only fire when the table has focus --
    # the search Input consumes plain letters as text, so they can't reach
    # the screen while the user is typing a query.
    BINDINGS = [
        ("q", "app.quit", "Quit"),
        ("a", "add_entry", "Add"),
        ("c", "copy_password", "Copy"),
        ("d", "delete_entry", "Delete"),
        ("slash", "focus_search", "Search"),
        ("escape", "focus_table", "Back to list"),
    ]

    # How long the copy-confirmation status message counts down for, in
    # seconds -- kept in sync with the clipboard's own auto-clear delay so
    # the on-screen countdown matches when the clipboard actually clears.
    COPY_STATUS_DELAY = clipboard.DEFAULT_CLEAR_DELAY

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
        self._status_timer = None
        self._status_remaining = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search by service or username...", id="search")
        yield VimDataTable(id="entries", cursor_type="row")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#entries", VimDataTable)
        table.add_columns("Service", "Username", "Last updated")
        self._populate(self.entries)
        # The table (not the search box) has default focus -- focusing the
        # Input by default hid the footer's key hints behind its own
        # (empty) binding set and left users with no way to discover a/c/d/q.
        table.focus()

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

    def _refresh(self) -> None:
        """Re-render the table from `self.entries`, respecting whatever
        search query (if any) is currently in the search box."""
        self._apply_filter(self.query_one("#search", Input).value)

    def action_add_entry(self) -> None:
        self.app.push_screen(
            EntryFormScreen(self.vault_path, self.key, self.kdf_params, self.entries),
            self._handle_form_result,
        )

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_focus_table(self) -> None:
        self.query_one("#entries", VimDataTable).focus()

    def _current_row_key(self) -> str | None:
        """The entry name under the table cursor, or None if the table is
        empty (e.g. an empty vault, or a search query with no matches)."""
        table = self.query_one("#entries", VimDataTable)
        if table.row_count == 0:
            return None
        row_key, _column_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        return row_key.value

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def action_copy_password(self) -> None:
        name = self._current_row_key()
        if name is None:
            return
        password = self.entries[name].get("password", "")

        try:
            clipboard.copy_with_autoclear(password, delay=self.COPY_STATUS_DELAY)
        except clipboard.ClipboardUnavailableError as exc:
            self._set_status(f"Clipboard unavailable: {exc}")
            return

        self._start_copy_countdown(name)

    def _start_copy_countdown(self, name: str) -> None:
        if self._status_timer is not None:
            self._status_timer.stop()
        self._status_remaining = int(self.COPY_STATUS_DELAY)
        self._set_status(f"Copied password for '{name}' (clears in {self._status_remaining}s)")
        self._status_timer = self.set_interval(1.0, self._tick_copy_countdown)

    def _tick_copy_countdown(self) -> None:
        self._status_remaining -= 1
        if self._status_remaining <= 0:
            self._status_timer.stop()
            self._status_timer = None
            self._set_status("Clipboard cleared.")
            return
        self._set_status(f"Clipboard clears in {self._status_remaining}s")

    def action_delete_entry(self) -> None:
        name = self._current_row_key()
        if name is None:
            return
        self.app.push_screen(
            ConfirmDeleteScreen(name), lambda confirmed: self._handle_delete_result(name, confirmed)
        )

    def _handle_delete_result(self, name: str, confirmed: bool | None) -> None:
        if not confirmed or name not in self.entries:
            return
        del self.entries[name]
        storage.save_vault(self.vault_path, self.key, self.kdf_params, self.entries)
        self._refresh()
        self._set_status(f"Deleted '{name}'.")

    @on(DataTable.RowSelected, "#entries")
    def handle_row_selected(self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        if name is None:
            return
        self.app.push_screen(
            EntryFormScreen(
                self.vault_path, self.key, self.kdf_params, self.entries, entry_name=name
            ),
            self._handle_form_result,
        )

    def _handle_form_result(self, saved: bool | None) -> None:
        if saved:
            self._refresh()

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
