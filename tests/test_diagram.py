import pytest
from textual.widgets import DataTable, Input, OptionList, TabbedContent

import weavtui.diagram as diagram_mod
from tests.fake_backend import FakeBackend
from weavtui.app import WeavtuiApp
from weavtui.backend import CollectionInfo, PropertyInfo, ReferenceInfo
from weavtui.diagram import DiagramView, layout, to_mermaid
from weavtui.screens import ConfirmScreen, SavePathScreen


def props(*names):
    return tuple(PropertyInfo(n, "text") for n in names)


SCHEMA = [
    CollectionInfo(
        "Article",
        120,
        properties=props("title", "body"),
        aliases=("Articles",),
        references=(
            ReferenceInfo("writtenBy", ("Author",)),
            ReferenceInfo("cites", ("Article",)),
            ReferenceInfo("inJournal", ("Journal",)),
        ),
    ),
    CollectionInfo(
        "Author",
        15,
        properties=props("name"),
        references=(
            ReferenceInfo("favourite", ("Article",)),
            ReferenceInfo("worksAt", ("Institution",)),
        ),
    ),
    CollectionInfo("Institution", 3, properties=props("name", "country")),
    CollectionInfo(
        "Journal",
        4,
        properties=props("name"),
        references=(ReferenceInfo("publisher", ("Institution", "Ghost")),),
    ),
    CollectionInfo("Lonely", 1, properties=props("x")),
    CollectionInfo("Alone", 0),
]


def test_boxes_do_not_overlap_and_list_everything():
    d = layout(SCHEMA)
    assert set(d.boxes) == {
        "Article",
        "Author",
        "Institution",
        "Journal",
        "Ghost",
        "Lonely",
        "Alone",
    }
    boxes = list(d.boxes.values())
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            separate = (
                a.x + a.width <= b.x
                or b.x + b.width <= a.x
                or a.y + a.height <= b.y
                or b.y + b.height <= a.y
            )
            assert separate, (a.name, b.name)
    text = "\n".join("".join(row) for row in d.cells)
    for line in ("title  text", "aka Articles", "writtenBy  → Author", "(missing collection)"):
        assert line in text
    assert "Collections without references" in text


def test_lines_never_run_under_boxes(monkeypatch):
    d_full = layout(SCHEMA)
    monkeypatch.setattr(diagram_mod, "_draw_box", lambda d, b: None)
    d_lines = layout(SCHEMA)
    for b in d_full.boxes.values():
        for y in range(b.y, b.y + b.height):
            for x in range(b.x, b.x + b.width):
                assert d_lines.cells[y][x] == " ", (b.name, x, y)


def test_every_reference_has_an_arrow_and_a_start():
    d = layout(SCHEMA)
    edges = [
        (c.name, r.name, t) for c in SCHEMA for r in c.references for t in r.targets if t != c.name
    ]
    for source, prop, target in edges:
        t = d.boxes[target]
        row = t.y + 1  # name row
        assert d.cells[row][t.x - 1] == "▶" or d.cells[row][t.x + t.width] == "◀", target
        s = d.boxes[source]
        ref_row = s.y + s.ref_rows[prop]
        left, right = d.cells[ref_row][s.x - 1], d.cells[ref_row][s.x + s.width]
        assert left != " " or right != " ", (source, prop)


def test_empty_and_unlinked_schemas():
    assert layout([]).boxes == {}
    d = layout([CollectionInfo("A", 1), CollectionInfo("B", 2)])
    assert "Collections" in "".join(d.cells[0])
    assert "▶" not in "\n".join("".join(r) for r in d.cells)


def test_mermaid():
    src = to_mermaid(SCHEMA)
    assert src.startswith("classDiagram\n")
    assert "  class Article {\n    <<Articles>>\n    text title\n" in src
    assert "  class Alone\n" in src
    assert "  class Ghost {\n    <<missing>>\n  }" in src
    assert "  Article --> Author : writtenBy\n" in src
    assert "  Article --> Article : cites\n" in src
    assert "  Journal --> Ghost : publisher\n" in src


async def settle(pilot):
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


@pytest.fixture
def app():
    return WeavtuiApp(FakeBackend())  # starts on the diagram tab


def active_tab(app) -> str:
    return app.query_one(TabbedContent).active


async def test_starts_on_diagram_tab_with_sidebar(app):
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot)
        assert active_tab(app) == "tab-diagram"
        assert app.query_one("#collections", OptionList).option_count == 2  # sidebar visible
        view = app.query_one(DiagramView)
        assert app.focused is view
        assert view.selected == "Book"


async def test_diagram_and_sidebar_stay_in_sync(app):
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot)
        view = app.query_one(DiagramView)
        await pilot.press("right")
        await settle(pilot)
        assert view.selected != "Book"
        assert app.current == view.selected  # sidebar followed the diagram
        options = app.query_one("#collections", OptionList)
        options.focus()
        options.highlighted = options.get_option_index("Book")
        await settle(pilot)
        assert view.selected == "Book"  # diagram followed the sidebar


async def test_enter_opens_objects_and_g_returns(app):
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert active_tab(app) == "tab-objects"
        assert isinstance(app.focused, DataTable)
        assert app.query_one(DataTable).row_count > 0
        await pilot.press("g")
        await settle(pilot)
        assert active_tab(app) == "tab-diagram"
        assert isinstance(app.focused, DiagramView)


async def test_export_asks_for_path(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot)
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, SavePathScreen)
        path_input = app.screen.query_one(Input)
        assert path_input.value == "weaviate-schema.mmd"
        (tmp_path / "out").mkdir()
        path_input.value = "out"  # a directory: default file name is appended
        await pilot.press("enter")
        await settle(pilot)
    content = (tmp_path / "out" / "weaviate-schema.mmd").read_text()
    assert "Book --> Empty : shelvedIn" in content


async def test_export_rejects_missing_dir_and_confirms_overwrite(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "schema.mmd"
    target.write_text("old")
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot)
        await pilot.press("e")
        await pilot.pause()
        path_input = app.screen.query_one(Input)
        path_input.value = str(tmp_path / "nope" / "x.mmd")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SavePathScreen)
        assert "does not exist" in str(app.screen.query_one("#save-error").content)
        path_input.value = "schema.mmd"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")  # keep the old file
        await settle(pilot)
        assert target.read_text() == "old"
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one(Input).value = "schema.mmd"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.click("#yes")
        await settle(pilot)
    assert target.read_text().startswith("classDiagram")


async def test_click_selects_then_opens(app):
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot)
        view = app.query_one(DiagramView)
        canvas = view.query_one("#diagram-canvas")
        empty = view.diagram.boxes["Empty"]
        target = (empty.x + 2, empty.y + 1)  # inside the box, on its name row

        await pilot.click(canvas, offset=target)
        await settle(pilot)
        assert view.selected == "Empty"
        assert app.current == "Empty"  # sidebar followed
        assert active_tab(app) == "tab-diagram"  # first click only selects

        await pilot.click(canvas, offset=target)
        await settle(pilot)
        assert active_tab(app) == "tab-objects"  # second click opens


async def test_click_on_empty_space_does_nothing(app):
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot)
        view = app.query_one(DiagramView)
        canvas = view.query_one("#diagram-canvas")
        d = view.diagram
        spot = next(
            (x, y)
            for y in range(d.height)
            for x in range(d.width)
            if d.owner[y][x] is None and d.cells[y][x] == " "
        )
        await pilot.click(canvas, offset=spot)
        await settle(pilot)
        assert view.selected == "Book"
        assert active_tab(app) == "tab-diagram"
