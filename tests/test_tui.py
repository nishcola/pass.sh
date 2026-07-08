import copy

import pyperclip
import pytest
from textual.widgets import DataTable, Input, Static

from passsh import agent, clipboard, storage
from passsh.tui import (
    ConfirmDeleteScreen,
    EntryFormScreen,
    MainScreen,
    PassShApp,
    UnlockScreen,
    VimDataTable,
)

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


@pytest.fixture
def real_vault(vault_path):
    """A vault path plus the *real* key/kdf_params derived from MASTER_PW,
    for tests that need saves to actually round-trip through disk."""
    key, kdf_params, _entries = storage.load_vault(vault_path, MASTER_PW.encode("utf-8"))
    return vault_path, key, kdf_params


class _FakeSystemClipboard:
    def __init__(self):
        self.value = ""

    def copy(self, text):
        self.value = text

    def paste(self):
        return self.value


class _FakePopen:
    """Stands in for the detached auto-clear worker process so tests never
    spawn a real subprocess (see test_clipboard.py, same pattern)."""

    def __init__(self, args, stdin=None, stdout=None, stderr=None, **kwargs):
        self.stdin = type("_Stdin", (), {"write": lambda *a: None, "close": lambda *a: None})()


@pytest.fixture
def fake_clipboard(monkeypatch):
    """Isolates MainScreen's copy action from the real OS clipboard and from
    spawning a real detached clear-worker process."""
    fake = _FakeSystemClipboard()
    monkeypatch.setattr(clipboard.pyperclip, "copy", fake.copy)
    monkeypatch.setattr(clipboard.pyperclip, "paste", fake.paste)
    monkeypatch.setattr(clipboard.subprocess, "Popen", _FakePopen)
    return fake


def _main_screen(vault_path, entries=None, key=None, kdf_params=None):
    return MainScreen(
        vault_path, key=key or b"k" * 32, kdf_params=kdf_params or {}, entries=entries or {}
    )


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
        app.screen.query_one("#search", Input).focus()  # table has default focus now

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
        app.screen.query_one("#search", Input).focus()

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
        app.screen.query_one("#search", Input).focus()

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
        app.screen.query_one("#search", Input).focus()

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
        app.screen.query_one("#search", Input).focus()
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
        app.screen.query_one("#search", Input).focus()
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
        app.screen.query_one("#search", Input).focus()

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

        assert table.cursor_row == 0
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("j")
        assert table.cursor_row == 2
        await pilot.press("k")
        assert table.cursor_row == 1


async def test_table_is_focused_on_mount(vault_path):
    # The table (not the search box) has default focus, so the footer's key
    # hints (a/c/d/q) are visible right away and single-letter shortcuts
    # work without an extra Tab press.
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", VimDataTable)
        assert app.focused is table


async def test_slash_focuses_search_and_escape_returns_to_table(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, SAMPLE_ENTRIES))
        await pilot.pause()
        table = app.screen.query_one("#entries", VimDataTable)
        search = app.screen.query_one("#search", Input)

        await pilot.press("slash")
        await pilot.pause()
        assert app.focused is search

        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is table


async def test_q_key_quits_from_main_screen(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path))
        await pilot.pause()
        # The table has default focus, so no extra step is needed before a
        # single-letter shortcut like "q" can reach the screen.
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


# --- EntryFormScreen: add mode ---


async def test_pressing_a_opens_add_modal(real_vault):
    vault_path, key, kdf_params = real_vault
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        screen = _main_screen(vault_path, {}, key=key, kdf_params=kdf_params)
        app.push_screen(screen)
        await pilot.pause()
        app.screen.query_one("#entries", VimDataTable).focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        assert isinstance(app.screen, EntryFormScreen)
        assert app.screen.is_edit is False


async def test_add_requires_service_name(real_vault):
    vault_path, key, kdf_params = real_vault
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, {}))
        await pilot.pause()

        app.screen.query_one("#password", Input).value = "somepassword"
        await pilot.click("#save")
        await pilot.pause()

        assert isinstance(app.screen, EntryFormScreen)  # still open, not dismissed
        assert "required" in str(app.screen.query_one("#form-error", Static).render()).lower()


async def test_add_requires_password(real_vault):
    vault_path, key, kdf_params = real_vault
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, {}))
        await pilot.pause()

        app.screen.query_one("#service", Input).value = "newsite"
        await pilot.click("#save")
        await pilot.pause()

        assert isinstance(app.screen, EntryFormScreen)
        assert "password" in str(app.screen.query_one("#form-error", Static).render()).lower()


async def test_add_rejects_duplicate_service_name(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, entries))
        await pilot.pause()

        app.screen.query_one("#service", Input).value = "github"
        app.screen.query_one("#password", Input).value = "whatever"
        await pilot.click("#save")
        await pilot.pause()

        assert isinstance(app.screen, EntryFormScreen)
        assert "already exists" in str(app.screen.query_one("#form-error", Static).render())


async def test_add_saves_new_entry_and_persists_to_disk(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = {}
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        screen = _main_screen(vault_path, entries, key=key, kdf_params=kdf_params)
        app.push_screen(screen)
        await pilot.pause()
        app.screen.query_one("#entries", VimDataTable).focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, EntryFormScreen)

        app.screen.query_one("#service", Input).value = "newsite"
        app.screen.query_one("#username", Input).value = "dave"
        app.screen.query_one("#password", Input).value = "s3cret!"
        app.screen.query_one("#notes", Input).value = "personal account"
        await pilot.click("#save")
        await pilot.pause()

        # Dismissed back to MainScreen, table refreshed in place.
        assert isinstance(app.screen, MainScreen)
        table = app.screen.query_one("#entries", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "newsite"

        # And it's genuinely on disk, decryptable with the real master password.
        _key2, _kdf2, reloaded = storage.load_vault(vault_path, MASTER_PW.encode("utf-8"))
        assert reloaded["newsite"]["username"] == "dave"
        assert reloaded["newsite"]["password"] == "s3cret!"
        assert reloaded["newsite"]["notes"] == "personal account"


async def test_cancel_button_discards_changes(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = {}
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, entries))
        await pilot.pause()

        app.screen.query_one("#service", Input).value = "abandoned"
        app.screen.query_one("#password", Input).value = "whatever"
        await pilot.click("#cancel")
        await pilot.pause()

        assert "abandoned" not in entries


async def test_escape_key_cancels_form_without_saving(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = {}
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, entries))
        await pilot.pause()

        app.screen.query_one("#service", Input).value = "abandoned"
        await pilot.press("escape")
        await pilot.pause()

        assert entries == {}


async def test_password_toggle_reveals_and_hides(real_vault):
    vault_path, key, kdf_params = real_vault
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, {}))
        await pilot.pause()

        password_input = app.screen.query_one("#password", Input)
        assert password_input.password is True  # masked by default

        await pilot.click("#toggle-password")
        await pilot.pause()
        assert password_input.password is False

        # Button needs its press/active state to settle between synthetic
        # clicks in tests, or a second rapid click can be swallowed -- this
        # is a Pilot/Button test-timing quirk (confirmed by toggling the
        # same handler directly, with no click simulation involved, which
        # works correctly back-to-back with no delay at all).
        await pilot.pause(0.3)
        await pilot.click("#toggle-password")
        await pilot.pause()
        assert password_input.password is True


# --- EntryFormScreen: edit mode ---


async def test_edit_prefills_username_and_notes_but_not_password(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, entries, entry_name="github"))
        await pilot.pause()

        assert app.screen.query_one("#service", Input).value == "github"
        assert app.screen.query_one("#service", Input).disabled is True
        assert app.screen.query_one("#username", Input).value == "alice"
        assert app.screen.query_one("#password", Input).value == ""  # never pre-filled


async def test_enter_on_row_opens_edit_modal_prefilled(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        screen = _main_screen(vault_path, entries, key=key, kdf_params=kdf_params)
        app.push_screen(screen)
        await pilot.pause()
        table = app.screen.query_one("#entries", VimDataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("enter")  # cursor starts on row 0 -> alphabetically "github"
        await pilot.pause()

        assert isinstance(app.screen, EntryFormScreen)
        assert app.screen.is_edit is True
        assert app.screen.entry_name == "github"
        assert app.screen.query_one("#username", Input).value == "alice"


async def test_edit_leaving_password_blank_keeps_existing_password(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, entries, entry_name="github"))
        await pilot.pause()

        app.screen.query_one("#username", Input).value = "alice2"
        # password field left blank
        await pilot.click("#save")
        await pilot.pause()

        assert entries["github"]["username"] == "alice2"
        assert entries["github"]["password"] == "hunter2"  # unchanged


async def test_edit_with_new_password_updates_it(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(EntryFormScreen(vault_path, key, kdf_params, entries, entry_name="github"))
        await pilot.pause()

        app.screen.query_one("#password", Input).value = "new-password-99"
        await pilot.click("#save")
        await pilot.pause()

        assert entries["github"]["password"] == "new-password-99"

        _key2, _kdf2, reloaded = storage.load_vault(vault_path, MASTER_PW.encode("utf-8"))
        assert reloaded["github"]["password"] == "new-password-99"


async def test_edit_refreshes_main_screen_table(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        screen = _main_screen(vault_path, entries, key=key, kdf_params=kdf_params)
        app.push_screen(screen)
        await pilot.pause()
        table = app.screen.query_one("#entries", VimDataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#username", Input).value = "alice-updated"
        await pilot.click("#save")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        table = app.screen.query_one("#entries", DataTable)
        rows = {table.get_row_at(i)[0]: table.get_row_at(i) for i in range(table.row_count)}
        assert rows["github"][1] == "alice-updated"


# --- MainScreen: entry actions (copy/delete) ---


async def test_c_key_copies_selected_password_and_shows_status(vault_path, fake_clipboard):
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, entries))
        await pilot.pause()

        await pilot.press("c")  # cursor starts on row 0 -> alphabetically "github"
        await pilot.pause()

        assert fake_clipboard.value == "hunter2"
        status = str(app.screen.query_one("#status", Static).render())
        assert "Copied password for 'github'" in status


async def test_copy_status_counts_down_then_clears(vault_path, monkeypatch, fake_clipboard):
    monkeypatch.setattr(MainScreen, "COPY_STATUS_DELAY", 2)
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, entries))
        await pilot.pause()

        await pilot.press("c")
        await pilot.pause()
        assert "2s" in str(app.screen.query_one("#status", Static).render())

        await pilot.pause(1.1)
        assert "1s" in str(app.screen.query_one("#status", Static).render())

        await pilot.pause(1.1)
        assert "cleared" in str(app.screen.query_one("#status", Static).render()).lower()


async def test_c_key_on_empty_vault_does_nothing(vault_path, fake_clipboard):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, {}))
        await pilot.pause()

        await pilot.press("c")
        await pilot.pause()

        assert fake_clipboard.value == ""
        assert str(app.screen.query_one("#status", Static).render()) == ""


async def test_d_key_opens_confirm_delete_modal_for_selected_row(vault_path):
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, entries))
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmDeleteScreen)
        assert app.screen.entry_name == "github"


async def test_d_key_on_empty_vault_does_nothing(vault_path):
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        app.push_screen(_main_screen(vault_path, {}))
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)


async def test_confirm_delete_removes_entry_and_persists_to_disk(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        screen = _main_screen(vault_path, entries, key=key, kdf_params=kdf_params)
        app.push_screen(screen)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeleteScreen)

        await pilot.click("#confirm")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        assert "github" not in entries
        table = app.screen.query_one("#entries", DataTable)
        assert table.row_count == 2
        status = str(app.screen.query_one("#status", Static).render())
        assert "Deleted 'github'" in status

        _key2, _kdf2, reloaded = storage.load_vault(vault_path, MASTER_PW.encode("utf-8"))
        assert "github" not in reloaded


async def test_cancel_button_in_confirm_delete_keeps_entry(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        screen = _main_screen(vault_path, entries, key=key, kdf_params=kdf_params)
        app.push_screen(screen)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.click("#cancel")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        assert "github" in entries


async def test_escape_in_confirm_delete_keeps_entry(real_vault):
    vault_path, key, kdf_params = real_vault
    entries = copy.deepcopy(SAMPLE_ENTRIES)
    app = PassShApp(vault_path)
    async with app.run_test() as pilot:
        screen = _main_screen(vault_path, entries, key=key, kdf_params=kdf_params)
        app.push_screen(screen)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)
        assert "github" in entries
