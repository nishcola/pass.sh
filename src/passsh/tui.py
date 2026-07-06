"""Terminal UI entry point for pass.sh, built on Textual.

This is a presentation layer only: screens here call into the existing
crypto/storage/clipboard modules for anything that touches the vault --
none of that logic is reimplemented here.
"""

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Static


class MainScreen(Screen):
    """The app's home screen. The vault entry list will live here."""

    # "app.quit" (not "quit") because Textual dispatches an action to the
    # exact node where the binding is declared -- MainScreen itself has no
    # action_quit method, only App does, so the namespace prefix is required
    # to route there instead of silently failing to dispatch.
    BINDINGS = [("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Vault contents will go here.", id="body")
        yield Footer()


class PassShApp(App):
    """Textual TUI for pass.sh.

    Uses Textual's screen-stack (`push_screen`/`pop_screen`) so later modal
    screens -- add/edit forms, unlock prompts -- can be layered on top of
    `MainScreen` without restructuring navigation.
    """

    TITLE = "pass.sh"

    def on_mount(self) -> None:
        self.push_screen(MainScreen())


def run() -> None:
    PassShApp().run()


if __name__ == "__main__":
    run()
