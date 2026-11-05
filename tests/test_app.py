from textual.widgets import DataTable, Input, Label, OptionList, Static, TextArea

from tests.fake_backend import FakeBackend
from weavtui.app import WeavtuiApp
from weavtui.screens import ConfirmScreen, ConnectErrorScreen, EditObjectScreen, ObjectScreen

# 24 lines leave 15 visible table rows -> pages of 20 (60 fake objects = 3 pages)
SIZE = (160, 24)


async def settle(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def pager(app) -> str:
    return str(app.query_one("#pager", Label).content)


async def test_lists_collections_and_first_page():
    app = WeavtuiApp(FakeBackend(), start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        options = app.query_one("#collections", OptionList)
        assert options.option_count == 2
        assert app.current == "Book"
        table = app.query_one(DataTable)
        assert app.page_size == 20
        assert table.row_count == 20
        assert "Page 1/3 · 60 objects" in pager(app)
        assert "1.40.3" in app.sub_title


async def test_paging_uses_cursor():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("right_square_bracket")
        await settle(pilot)
        assert "Page 2/3" in pager(app)
        assert backend.calls[-1][3] == "00000000-0000-0000-0000-000000000019"
        await pilot.press("right_square_bracket", "right_square_bracket")
        await settle(pilot)
        assert "Page 3/3" in pager(app)
        assert app.query_one(DataTable).row_count == 20
        assert not app.has_next
        await pilot.press("left_square_bracket")
        await settle(pilot)
        assert "Page 2/3" in pager(app)


async def test_search_resets_paging_and_typing_q_does_not_quit():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("slash")
        await pilot.press(*"book 1", "enter")
        await settle(pilot)
        assert app.is_running
        assert backend.calls[-1][4] == "book 1"
        assert app.query_one(DataTable).row_count == 10


async def test_switching_collection():
    app = WeavtuiApp(FakeBackend(), start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one("#collections", OptionList).focus()
        await pilot.press("down")
        await settle(pilot)
        assert app.current == "Empty"
        assert "no objects" in pager(app)


async def test_open_edit_object():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ObjectScreen)
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, EditObjectScreen)
        app.screen.query_one(TextArea).text = '{"title": "Changed", "year": 2000}'
        await pilot.press("ctrl+s")
        await settle(pilot)
        uuid = "00000000-0000-0000-0000-000000000000"
        assert backend.calls[-2] == ("update_object", "Book", uuid, {"title": "Changed"})
        assert backend.data["Book"][uuid]["title"] == "Changed"


async def test_edit_rejects_invalid_json():
    app = WeavtuiApp(FakeBackend(), start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, EditObjectScreen)
        app.screen.query_one(TextArea).text = "[1, 2"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, EditObjectScreen)
        assert "Invalid JSON" in str(app.screen.query_one("#edit-error", Label).content)


async def test_create_object():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("n")
        await pilot.pause()
        assert '"title": ""' in app.screen.query_one(TextArea).text
        app.screen.query_one(TextArea).text = '{"title": "New", "year": 1999}'
        await pilot.press("ctrl+s")
        await settle(pilot)
        assert len(backend.data["Book"]) == 61
        assert "61 objects" in pager(app)


async def test_delete_object_needs_confirmation():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await settle(pilot)
        assert len(backend.data["Book"]) == 60
        await pilot.press("delete")
        await pilot.pause()
        await pilot.click("#yes")
        await settle(pilot)
        assert len(backend.data["Book"]) == 59


async def test_delete_collection_requires_typing_name():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("D")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConfirmScreen)
        assert screen.query_one("#yes").disabled
        await pilot.press(*"Boo", "enter")
        await pilot.pause()
        assert app.screen is screen  # wrong name, still open
        await pilot.press("k", "enter")
        await settle(pilot)
        assert "Book" not in backend.data
        assert app.current == "Empty"


async def test_connection_error_offers_retry():
    backend = FakeBackend(fail_connect=True)
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ConnectErrorScreen)
        backend.fail_connect = False
        await pilot.press("r")
        await settle(pilot)
        assert app.current == "Book"


async def test_filter_input_is_passed_to_backend():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one("#filter", Input).focus()
        await pilot.press(*"year > 3", "enter")
        await settle(pilot)
        assert backend.calls[-1][5] == "year > 3"
        assert "filtered" in pager(app)


async def test_main_shortcuts_disabled_on_object_screen():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ObjectScreen)
        await pilot.press("n", "D", "right_square_bracket")
        await pilot.pause()
        assert app.screen is screen
        assert app.page_index == 0


async def test_page_size_follows_window_height():
    backend = FakeBackend()
    app = WeavtuiApp(backend, start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.query_one(DataTable).focus()
        await pilot.press("right_square_bracket", "right_square_bracket")
        await settle(pilot)
        assert "showing 41–60" in pager(app)

        await pilot.resize_terminal(160, 50)  # 41 visible rows -> pages of 50
        await pilot.pause(0.4)
        await settle(pilot)
        assert app.page_size == 50
        assert app.page_index == 0  # object 41 is still on the shown page
        assert app.query_one(DataTable).row_count == 50
        assert "Page 1/2" in pager(app)

        await pilot.press("right_square_bracket")
        await settle(pilot)
        assert "showing 51–60" in pager(app)
        await pilot.resize_terminal(160, 24)
        await pilot.pause(0.4)
        await settle(pilot)
        assert app.page_size == 20
        assert "showing 41–60" in pager(app)  # jumped by offset, no cursor known
        assert backend.calls[-1][2:4] == (40, None)


def test_page_size_rounding():
    from weavtui.app import page_size_for

    assert [page_size_for(n) for n in (0, 5, 10, 11, 27, 41, 70)] == [10, 10, 10, 20, 30, 50, 70]


async def test_main_area_fits_next_to_sidebar():
    app = WeavtuiApp(FakeBackend(), start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        table = app.query_one(DataTable)
        assert table.region.right <= app.size.width
        assert app.query_one("#filter", Input).region.right <= app.size.width


async def test_aliases_shown_in_sidebar_and_schema():
    app = WeavtuiApp(FakeBackend(), start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        options = app.query_one("#collections", OptionList)
        assert "↳ Books, Library" in str(options.get_option("Book").prompt)
        assert "↳" not in str(options.get_option("Empty").prompt)
        summary = app.query_one("#schema", Static).content.renderables[0]
        assert "Books, Library" in str(summary)
