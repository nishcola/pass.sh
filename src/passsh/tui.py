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
from textual.widgets import Footer, Header, Input, Static

from . import session, storage


class MainScreen(Screen):
    """The app's home screen. The vault entry list will live here."""

    # "app.quit" (not "quit") because Textual dispatches an action to the
    # exact node where the binding is declared -- MainScreen itself has no
    # action_quit method, only App does, so the namespace prefix is required
    # to route there instead of silently failing to dispatch.
    BINDINGS = [("q", "app.quit", "Quit")]

    def __init__(self, vault_path: Path, key: bytes, kdf_params: dict, entries: dict) -> None:
        super().__init__()
        self.vault_path = vault_path
        self.key = key
        self.kdf_params = kdf_params
        self.entries = entries

    def compose(self) -> ComposeResult:
        yield Header()
        count = len(self.entries)
        noun = "entry" if count == 1 else "entries"
        yield Static(f"Unlocked. {count} {noun} in the vault.", id="body")
        yield Footer()


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
