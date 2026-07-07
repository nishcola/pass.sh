import pytest
from textual.widgets import DataTable, Input, Static

from passsh import agent, storage
from passsh.tui import MainScreen, PassShApp, UnlockScreen, VimDataTable

MASTER_PW = "correct horse battery staple"

SAMPLE_ENTRIES = {
    "github": {"username": "alice", "password": "hunter2", "updated_at": "2026-01-02T03:04:05+00:00"},
    "gitlab": {"username": "bob", "password": "sekrit", "updated_at": "2026-03-04T05:06:07+00:00"},
    "no-timestamp": {"username": "carol", "password": "old-entry"},
}


@pytest.fixture
def vault_path(tmp_path):
    path = tmp_path / "vault.json"
    storage.create_vault(path, MASTER_PW.encode("utf-8"))
    yield path
    agent.lock(path)


def _main_screen(vault_path, entries=None):
    return MainScreen(vault_path, key=b"k" * 32, kdf_params={}, entries=entries or {})


# --- MainScreen: entry table contents ---


async def test_table_has_service_username_updated_columns_only(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", DataTable)
        column_labels = [str(col.label) for col in table.columns.values()]
        assert column_labels == ["Service", "Username", "Last updated"]


async def test_table_shows_one_row_per_entry_sorted_by_name(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", DataTable)

        assert table.row_count == 3
        rows = [table.get_row_at(i) for i in range(table.row_count)]
        names = [row[0] for row in rows]
        assert names == sorted(SAMPLE_ENTRIES)  # alphabetical


async def test_table_shows_username_and_formatted_timestamp_not_password(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", DataTable)

        rows = {table.get_row_at(i)[0]: table.get_row_at(i) for i in range(table.row_count)}
        assert rows["github"] == ["github", "alice", "2026-01-02 03:04:05 UTC"]

        # No cell anywhere in the table may contain a raw password value.
        all_cell_text = {str(cell) for row in rows.values() for cell in row}
        for entry in SAMPLE_ENTRIES.values():
            assert entry["password"] not in all_cell_text


async def test_table_shows_placeholder_for_missing_timestamp(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", DataTable)

        rows = {table.get_row_at(i)[0]: table.get_row_at(i) for i in range(table.row_count)}
        assert rows["no-timestamp"][2] == "—"


async def test_table_empty_vault_has_headers_and_zero_rows(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, {}))
        await pilot.pause()
        table = app.screen.query_one("#entries", DataTable)
        assert table.row_count == 0


# --- MainScreen: live search/filter ---


async def test_typing_in_search_filters_by_service_name(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()

        await pilot.press(*"github")
        await pilot.pause(0.3)  # past SEARCH_DEBOUNCE

        table = app.screen.query_one("#entries", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "github"


async def test_typing_in_search_filters_by_username(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()

        await pilot.press(*"bob")  # gitlab's username, not any service name
        await pilot.pause(0.3)

        table = app.screen.query_one("#entries", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "gitlab"


async def test_search_is_case_insensitive(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()

        await pilot.press(*"ALICE")
        await pilot.pause(0.3)

        table = app.screen.query_one("#entries", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "github"


async def test_clearing_search_shows_all_entries_again(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()

        await pilot.press(*"github")
        await pilot.pause(0.3)
        table = app.screen.query_one("#entries", DataTable)
        assert table.row_count == 1

        search = app.screen.query_one("#search", Input)
        search.value = ""
        await pilot.pause(0.3)
        assert table.row_count == 3


async def test_filter_is_debounced_not_applied_before_delay_elapses(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", DataTable)

        await pilot.press(*"github")
        await pilot.pause(0.02)  # well under SEARCH_DEBOUNCE=0.2
        assert table.row_count == 3  # filter has not fired yet

        await pilot.pause(0.3)
        assert table.row_count == 1  # now it has


async def test_rapid_retyping_only_applies_final_query(vault_path):
    """Each keystroke resets the debounce timer, so typing quickly should
    never filter on an intermediate (stale) query -- only the final one."""
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", DataTable)

        # Type "gith" then quickly change to "gitl" before the debounce for
        # "gith" would have fired.
        await pilot.press(*"gith")
        await pilot.pause(0.05)
        search = app.screen.query_one("#search", Input)
        search.value = ""
        await pilot.press(*"gitl")
        await pilot.pause(0.3)

        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "gitlab"


async def test_search_never_exposes_passwords(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()

        await pilot.press(*"a")
        await pilot.pause(0.3)

        table = app.screen.query_one("#entries", DataTable)
        all_cell_text = {
            str(cell) for i in range(table.row_count) for cell in table.get_row_at(i)
        }
        for entry in SAMPLE_ENTRIES.values():
            assert entry["password"] not in all_cell_text


# --- MainScreen: keyboard navigation ---


async def test_arrow_keys_move_cursor(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", VimDataTable)
        table.focus()  # search input has default focus now; navigation needs the table focused
        await pilot.pause()

        assert table.cursor_row == 0
        await pilot.press("down")
        assert table.cursor_row == 1
        await pilot.press("down")
        assert table.cursor_row == 2
        await pilot.press("up")
        assert table.cursor_row == 1


async def test_jk_keys_move_cursor_like_arrow_keys(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", VimDataTable)
        table.focus()
        await pilot.pause()

        assert table.cursor_row == 0
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("j")
        assert table.cursor_row == 2
        await pilot.press("k")
        assert table.cursor_row == 1


async def test_search_input_is_focused_on_mount(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        search = app.screen.query_one("#search", Input)
        assert app.focused is search


async def test_q_key_quits_from_main_screen(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path))
        await pilot.pause()
        # The search input has focus by default and consumes plain letter
        # keys as text, so move focus to the table first (e.g. via Tab)
        # before a single-letter shortcut like "q" can reach the screen.
        app.screen.query_one("#entries", VimDataTable).focus()
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running


async def test_screen_stack_pattern_supports_push_and_pop(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        base_depth = len(app.screen_stack)

        app.push_screen(_main_screen(vault_path))
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
