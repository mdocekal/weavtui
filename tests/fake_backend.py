"""In-memory Backend used by the UI tests."""

from __future__ import annotations

import uuid as uuidlib
from typing import Any

from weavtui.backend import (
    BackendError,
    CollectionInfo,
    ConnectionSettings,
    ObjectRecord,
    Page,
    PropertyInfo,
    ReferenceInfo,
)


class FakeBackend:
    def __init__(self, fail_connect: bool = False) -> None:
        self.settings = ConnectionSettings()
        self.fail_connect = fail_connect
        self.calls: list[tuple] = []
        self.schema = {
            "Book": (PropertyInfo("title", "text"), PropertyInfo("year", "int")),
            "Empty": (PropertyInfo("name", "text"),),
        }
        self.data: dict[str, dict[str, dict[str, Any]]] = {
            "Book": {
                f"00000000-0000-0000-0000-{i:012d}": {"title": f"Book {i:02d}", "year": 2000 + i}
                for i in range(60)
            },
            "Empty": {},
        }
        self.aliases = {"Book": ("Books", "Library")}
        self.references = {
            "Book": (ReferenceInfo("sequelOf", ("Book",)), ReferenceInfo("shelvedIn", ("Empty",))),
            "Empty": (ReferenceInfo("lostIn", ("Gone",)),),
        }

    def connect(self) -> str:
        if self.fail_connect:
            raise BackendError("Cannot connect to localhost:8080: refused")
        return "1.40.3"

    def close(self) -> None:
        pass

    def list_collections(self) -> list[CollectionInfo]:
        return [
            CollectionInfo(
                n,
                len(self.data[n]),
                None,
                self.schema[n],
                self.aliases.get(n, ()),
                self.references.get(n, ()),
            )
            for n in sorted(self.data)
        ]

    def collection_config(self, name: str) -> dict[str, Any]:
        return {"class": name, "properties": [p.name for p in self.schema[name]]}

    def fetch_page(self, name, *, limit, offset=0, after=None, query="", filter_text=""):
        self.calls.append(("fetch_page", name, offset, after, query, filter_text))
        items = sorted(self.data[name].items())
        if query:
            items = [(u, p) for u, p in items if query.lower() in str(p.get("title", "")).lower()]
        items = [(u, p) for u, p in items if u > after] if after is not None else items[offset:]
        objs = [ObjectRecord(u, dict(p)) for u, p in items[:limit]]
        return Page(objs, None if query else len(self.data[name]))

    def get_object(self, name: str, uuid: str) -> ObjectRecord | None:
        props = self.data[name].get(uuid)
        return None if props is None else ObjectRecord(uuid, dict(props), {"default": [0.1, 0.2]})

    def insert_object(self, name: str, properties: dict[str, Any]) -> str:
        uuid = str(uuidlib.uuid4())
        self.data[name][uuid] = properties
        return uuid

    def update_object(self, name: str, uuid: str, properties: dict[str, Any]) -> None:
        self.calls.append(("update_object", name, uuid, properties))
        self.data[name][uuid].update(properties)

    def delete_object(self, name: str, uuid: str) -> None:
        del self.data[name][uuid]

    def delete_collection(self, name: str) -> None:
        del self.data[name]
        del self.schema[name]
