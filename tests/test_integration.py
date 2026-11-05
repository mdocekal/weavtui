"""Tests against a real Weaviate (see docker-compose.yml). Skipped when none is reachable.

Point them elsewhere with WEAVTUI_TEST_PORT / WEAVTUI_TEST_GRPC_PORT.
"""

import os
import uuid as uuidlib

import pytest
from textual.widgets import DataTable, Label

from weavtui.app import WeavtuiApp
from weavtui.backend import BackendError, ConnectionSettings, WeaviateBackend

SETTINGS = ConnectionSettings(
    port=int(os.environ.get("WEAVTUI_TEST_PORT", 8080)),
    grpc_port=int(os.environ.get("WEAVTUI_TEST_GRPC_PORT", 50051)),
)
NAME = f"WeavtuiTest{uuidlib.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def backend():
    b = WeaviateBackend(SETTINGS)
    try:
        b.connect()
    except BackendError as exc:
        pytest.skip(f"no test Weaviate: {exc}")
    from weaviate.classes.config import Configure, DataType, Property, ReferenceProperty

    coll = b.client.collections.create(
        NAME,
        vector_config=Configure.Vectors.self_provided(),
        properties=[
            Property(name="title", data_type=DataType.TEXT),
            Property(name="year", data_type=DataType.INT),
            Property(name="published_at", data_type=DataType.DATE),
            Property(name="tags", data_type=DataType.TEXT_ARRAY),
        ],
        references=[ReferenceProperty(name="related", target_collection=NAME)],
    )
    coll.data.insert_many(
        [
            {
                "title": f"doc {i} {'alpha' if i % 2 else 'beta'}",
                "year": 2000 + i,
                "published_at": f"20{10 + i % 10:02d}-01-01T00:00:00Z",
                "tags": ["even" if i % 2 == 0 else "odd"],
            }
            for i in range(30)
        ]
    )
    yield b
    if b.client.collections.exists(NAME):
        b.client.collections.delete(NAME)
    b.close()


def test_list_and_count(backend):
    backend.client.alias.create(alias_name=f"{NAME}Alias", target_collection=NAME)
    try:
        info = {c.name: c for c in backend.list_collections()}[NAME]
    finally:
        backend.client.alias.delete(alias_name=f"{NAME}Alias")
    assert info.aliases == (f"{NAME}Alias",)
    assert [(r.name, r.targets) for r in info.references] == [("related", (NAME,))]
    assert info.count == 30
    assert info.property_types()["tags"] == "text[]"


def test_cursor_paging_covers_everything(backend):
    seen, after = [], None
    while True:
        page = backend.fetch_page(NAME, limit=7, after=after)
        assert page.total == 30
        if not page.objects:
            break
        seen += [o.uuid for o in page.objects]
        after = page.objects[-1].uuid
    assert len(set(seen)) == 30


def test_filters_and_search(backend):
    page = backend.fetch_page(NAME, limit=50, filter_text="year >= 2020 and tags contains odd")
    assert page.total == 5
    assert {o.properties["year"] % 2 for o in page.objects} == {1}
    page = backend.fetch_page(NAME, limit=50, filter_text="published_at >= 2019-01-01")
    assert page.total == 3
    page = backend.fetch_page(NAME, limit=5, offset=5, query="alpha")
    assert len(page.objects) == 5
    assert all("alpha" in o.properties["title"] for o in page.objects)
    assert page.objects[0].score is not None


def test_crud(backend):
    uuid = backend.insert_object(NAME, {"title": "new", "published_at": "2024-05-01T10:00:00Z"})
    backend.update_object(NAME, uuid, {"title": "changed", "year": None})
    obj = backend.get_object(NAME, uuid)
    assert obj.properties["title"] == "changed"
    assert obj.properties["published_at"].year == 2024
    assert obj.created is not None
    backend.delete_object(NAME, uuid)
    assert backend.get_object(NAME, uuid) is None


def test_bad_filter_is_reported(backend):
    with pytest.raises(BackendError, match="unknown property"):
        backend.fetch_page(NAME, limit=5, filter_text="nope = 1")


async def test_app_end_to_end(backend):
    app = WeavtuiApp(WeaviateBackend(SETTINGS), start="browse")
    async with app.run_test(size=(160, 50)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert NAME in app.collections
        app.select_collection(NAME)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 30  # page holds 50 at this size
        assert "30 objects" in str(app.query_one("#pager", Label).content)


def test_delete_collection(backend):
    backend.delete_collection(NAME)
    assert NAME not in {c.name for c in backend.list_collections()}
