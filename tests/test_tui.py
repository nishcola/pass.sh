import pytest
from textual.widgets import Input, Static

from passsh import agent, storage
from passsh.tui import MainScreen, PassShApp, UnlockScreen

MASTER_PW = "correct horse battery staple"


@pytest.fixture
def vault_path(tmp_path):
    path = tmp_path / "vault.json"
    storage.create_vault(path, MASTER_PW.encode("utf-8"))
    yield path
    agent.lock(path)


# --- MainScreen (already-unlocked state) ---


async def test_main_screen_shows_entry_count(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(MainScreen(vault_path, key=b"k" * 32, kdf_params={}, entries={}))
        await pilot.pause()
        body = app.screen.query_one("#body", Static)
        assert str(body.render()) == "Unlocked. 0 entries in the vault."


async def test_main_screen_singular_entry_wording(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(
            MainScreen(vault_path, key=b"k" * 32, kdf_params={}, entries={"github": {}})
        )
        await pilot.pause()
        body = app.screen.query_one("#body", Static)
        assert str(body.render()) == "Unlocked. 1 entry in the vault."


async def test_q_key_quits_from_main_screen(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(MainScreen(vault_path, key=b"k" * 32, kdf_params={}, entries={}))
        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running


async def test_screen_stack_pattern_supports_push_and_pop(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        base_depth = len(app.screen_stack)

        app.push_screen(MainScreen(vault_path, key=b"k" * 32, kdf_params={}, entries={}))
        await pilot.pause()
        assert len(app.screen_stack) == base_depth + 1

        app.pop_screen()
        await pilot.pause()
        assert len(app.screen_stack) == base_depth


# --- app startup: unlock screen vs. cached-session skip ---


async def test_app_shows_unlock_screen_when_no_cached_session(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test():
        assert isinstance(app.screen, UnlockScreen)


async def test_app_skips_unlock_screen_when_session_already_cached(vault_path):
    from passsh import session as session_module

    session_module.unlock(vault_path, MASTER_PW.encode("utf-8"))  # populates the agent cache

    app = PassShApp(vault_path)
    async with app.run_test():
        assert isinstance(app.screen, MainScreen)


# --- unlock screen behavior ---


async def test_unlock_screen_has_masked_password_input(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test():
        password_input = app.screen.query_one("#password", Input)
        assert password_input.password is True


async def test_unlock_with_wrong_password_shows_inline_error_and_allows_retry(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, UnlockScreen)

        password_input = app.screen.query_one("#password", Input)
        password_input.value = "wrong password"
        await pilot.press("enter")
        await pilot.pause()

        # App must still be running and still on the unlock screen -- not exited.
        assert app.is_running
        assert isinstance(app.screen, UnlockScreen)

        error = app.screen.query_one("#error", Static)
        assert str(error.render()) != ""

        # The password field is cleared, ready for a retry, and still focused.
        assert app.screen.query_one("#password", Input).value == ""


async def test_unlock_with_correct_password_switches_to_main_screen(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        password_input = app.screen.query_one("#password", Input)
        password_input.value = MASTER_PW
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        assert app.screen.entries == {}


async def test_retry_after_wrong_password_then_succeeds(vault_path):
    from passsh import ratelimit

    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        password_input = app.screen.query_one("#password", Input)
        password_input.value = "wrong password"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, UnlockScreen)

        # The first failure correctly triggers a rate-limit backoff (covered
        # separately by test_ratelimit.py); clear it here so this test can
        # isolate the retry-UI mechanic itself without a real sleep.
        ratelimit.record_success(vault_path)

        password_input = app.screen.query_one("#password", Input)
        password_input.value = MASTER_PW
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
