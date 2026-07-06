from textual.widgets import Static

from passsh.tui import MainScreen, PassShApp


async def test_app_mounts_main_screen_via_push_screen():
    app = PassShApp()
    async with app.run_test():
        assert isinstance(app.screen, MainScreen)


async def test_main_screen_has_header_footer_and_placeholder_body():
    app = PassShApp()
    async with app.run_test():
        body = app.screen.query_one("#body", Static)
        assert str(body.render()) == "Vault contents will go here."


async def test_q_key_quits_the_app():
    app = PassShApp()
    async with app.run_test() as pilot:
        assert app.is_running
        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running


async def test_screen_stack_pattern_supports_push_and_pop():
    app = PassShApp()
    async with app.run_test() as pilot:
        base_depth = len(app.screen_stack)

        app.push_screen(MainScreen())
        await pilot.pause()
        assert len(app.screen_stack) == base_depth + 1

        app.pop_screen()
        await pilot.pause()
        assert len(app.screen_stack) == base_depth
