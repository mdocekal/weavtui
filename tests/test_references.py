from textual.widgets import Static

from tests.fake_backend import FakeBackend
from tests.test_app import SIZE, settle
from weavtui.app import WeavtuiApp
from weavtui.backend import CollectionInfo, ReferenceInfo
from weavtui.references import reference_graph


def test_reference_graph():
    infos = [
        CollectionInfo("A", 1, references=(ReferenceInfo("toB", ("B", "C")),)),
        CollectionInfo("B", 1, references=(ReferenceInfo("self", ("B",)),)),
        CollectionInfo("D", 1),
    ]
    g = reference_graph(infos)
    assert g["A"].outgoing == [("toB", "B"), ("toB", "C")]
    assert g["B"].incoming == [("A", "toB"), ("B", "self")]
    assert g["C"].incoming == [("A", "toB")]  # dangling target still shown
    assert g["D"].outgoing == g["D"].incoming == []


async def test_schema_summary_lists_references():
    app = WeavtuiApp(FakeBackend(), start="browse")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        summary = str(app.query_one("#schema", Static).content.renderables[0])
        assert "→ sequelOf  ▶ Book" in summary
        assert "→ shelvedIn  ▶ Empty" in summary
        assert "← Book.sequelOf" in summary
