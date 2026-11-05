"""Thin, synchronous layer between the UI and the Weaviate client.

The UI never touches the Weaviate client directly; it calls these methods from worker
threads. Keeping this surface small lets the UI tests run against a fake backend.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

# authlib (pulled in by weaviate-client) forces its deprecation warnings to "always"
# and emits one on import; record them instead of letting them hit the terminal.
with warnings.catch_warnings(record=True):
    import weaviate
    from weaviate.classes.init import Auth
    from weaviate.classes.query import MetadataQuery

from weavtui.filters import FilterError, build_filter


@dataclass(frozen=True)
class ConnectionSettings:
    host: str = "localhost"
    port: int = 8080
    grpc_port: int = 50051
    api_key: str | None = None

    def label(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class PropertyInfo:
    name: str
    data_type: str  # Weaviate data type name, e.g. "text", "int", "text[]"


@dataclass(frozen=True)
class ReferenceInfo:
    name: str  # reference property name
    targets: tuple[str, ...]  # target collection names


@dataclass(frozen=True)
class CollectionInfo:
    name: str
    count: int | None
    description: str | None = None
    properties: tuple[PropertyInfo, ...] = ()
    aliases: tuple[str, ...] = ()
    references: tuple[ReferenceInfo, ...] = ()

    def property_types(self) -> dict[str, str]:
        return {p.name: p.data_type for p in self.properties}


@dataclass
class ObjectRecord:
    uuid: str
    properties: dict[str, Any]
    vectors: dict[str, Any] = field(default_factory=dict)
    created: datetime | None = None
    updated: datetime | None = None
    score: float | None = None


@dataclass
class Page:
    objects: list[ObjectRecord]
    total: int | None  # None when unknown (keyword search)


class BackendError(Exception):
    """Any failure talking to Weaviate, with a message fit for the user."""


class Backend(Protocol):
    settings: ConnectionSettings

    def connect(self) -> str: ...
    def close(self) -> None: ...
    def list_collections(self) -> list[CollectionInfo]: ...
    def collection_config(self, name: str) -> dict[str, Any]: ...
    def fetch_page(
        self,
        name: str,
        *,
        limit: int,
        offset: int = 0,
        after: str | None = None,
        query: str = "",
        filter_text: str = "",
    ) -> Page: ...
    def get_object(self, name: str, uuid: str) -> ObjectRecord | None: ...
    def insert_object(self, name: str, properties: dict[str, Any]) -> str: ...
    def update_object(self, name: str, uuid: str, properties: dict[str, Any]) -> None: ...
    def delete_object(self, name: str, uuid: str) -> None: ...
    def delete_collection(self, name: str) -> None: ...


def to_jsonable(value: Any) -> Any:
    """Convert values returned by the client into plain JSON types."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):  # GeoCoordinate, PhoneNumber (pydantic)
        return to_jsonable(value.model_dump(exclude_none=True))
    if hasattr(value, "__dict__"):
        return to_jsonable({k: v for k, v in vars(value).items() if not k.startswith("_")})
    return value


def default_value(data_type: str) -> Any:
    """Placeholder value for a new object's property, by Weaviate data type."""
    if data_type.endswith("[]"):
        return []
    return {
        "text": "",
        "int": 0,
        "number": 0.0,
        "boolean": False,
        "date": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "uuid": "00000000-0000-0000-0000-000000000000",
    }.get(data_type)


def _record(obj: Any) -> ObjectRecord:
    meta = obj.metadata
    return ObjectRecord(
        uuid=str(obj.uuid),
        properties=dict(obj.properties),
        vectors=dict(obj.vector or {}),
        created=getattr(meta, "creation_time", None),
        updated=getattr(meta, "last_update_time", None),
        score=getattr(meta, "score", None),
    )


class WeaviateBackend:
    """Backend implementation using the official weaviate-client (v4)."""

    def __init__(self, settings: ConnectionSettings) -> None:
        self.settings = settings
        self._client: weaviate.WeaviateClient | None = None
        self._types: dict[str, dict[str, str]] = {}

    # -- connection ---------------------------------------------------------------------

    @property
    def client(self) -> weaviate.WeaviateClient:
        if self._client is None:
            raise BackendError("Not connected")
        return self._client

    def connect(self) -> str:
        """Connect and return the server version."""
        s = self.settings
        try:
            self._client = weaviate.connect_to_local(
                host=s.host,
                port=s.port,
                grpc_port=s.grpc_port,
                auth_credentials=Auth.api_key(s.api_key) if s.api_key else None,
            )
            return str(self._client.get_meta().get("version", "?"))
        except Exception as exc:
            self.close()
            msg = f"Cannot connect to {s.label()} (gRPC {s.grpc_port}): {exc}"
            raise BackendError(msg) from exc

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    # -- reading ------------------------------------------------------------------------

    def list_collections(self) -> list[CollectionInfo]:
        try:
            configs = self.client.collections.list_all(simple=True)
            aliases = self._aliases()
            result = []
            for name in sorted(configs, key=str.lower):
                cfg = configs[name]
                props = tuple(PropertyInfo(p.name, p.data_type.value) for p in cfg.properties)
                self._types[name] = {p.name: p.data_type for p in props}
                result.append(
                    CollectionInfo(
                        name,
                        self._count(name),
                        cfg.description,
                        props,
                        tuple(sorted(aliases.get(name, ()), key=str.lower)),
                        tuple(
                            ReferenceInfo(r.name, tuple(r.target_collections))
                            for r in cfg.references
                        ),
                    )
                )
            return result
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Listing collections failed: {exc}") from exc

    def _aliases(self) -> dict[str, list[str]]:
        """Map collection name -> its aliases. Empty on servers without aliases (< 1.32)."""
        try:
            listing = self.client.alias.list_all()
        except Exception:
            return {}
        result: dict[str, list[str]] = {}
        for alias in listing.values():
            result.setdefault(alias.collection, []).append(alias.alias)
        return result

    def _count(self, name: str) -> int | None:
        try:
            agg = self.client.collections.get(name).aggregate.over_all(total_count=True)
            return agg.total_count
        except Exception:
            return None  # e.g. multi-tenant collections need a tenant

    def collection_config(self, name: str) -> dict[str, Any]:
        try:
            cfg = self.client.collections.get(name).config.get().to_dict()
            return json.loads(json.dumps(cfg, default=str))
        except Exception as exc:
            raise BackendError(f"Reading schema of {name} failed: {exc}") from exc

    def fetch_page(
        self,
        name: str,
        *,
        limit: int,
        offset: int = 0,
        after: str | None = None,
        query: str = "",
        filter_text: str = "",
    ) -> Page:
        try:
            filters = build_filter(filter_text, self._types.get(name, {}))
        except FilterError as exc:
            raise BackendError(f"Invalid filter: {exc}") from exc
        coll = self.client.collections.get(name)
        meta = MetadataQuery(creation_time=True, last_update_time=True, score=bool(query))
        try:
            if query.strip():
                res = coll.query.bm25(
                    query.strip(), limit=limit, offset=offset, filters=filters, return_metadata=meta
                )
                return Page([_record(o) for o in res.objects], None)
            if after is not None or (filters is None and offset == 0):
                # Cursor paging has no depth limit but cannot be combined with filters.
                res = coll.query.fetch_objects(limit=limit, after=after, return_metadata=meta)
            else:
                res = coll.query.fetch_objects(
                    limit=limit, offset=offset, filters=filters, return_metadata=meta
                )
            total = coll.aggregate.over_all(total_count=True, filters=filters).total_count
            return Page([_record(o) for o in res.objects], total)
        except Exception as exc:
            raise BackendError(f"Querying {name} failed: {exc}") from exc

    def get_object(self, name: str, uuid: str) -> ObjectRecord | None:
        try:
            obj = self.client.collections.get(name).query.fetch_object_by_id(
                uuid, include_vector=True
            )
        except Exception as exc:
            raise BackendError(f"Fetching object {uuid} failed: {exc}") from exc
        return None if obj is None else _record(obj)

    # -- writing ------------------------------------------------------------------------

    def insert_object(self, name: str, properties: dict[str, Any]) -> str:
        try:
            return str(self.client.collections.get(name).data.insert(properties))
        except Exception as exc:
            raise BackendError(f"Creating object failed: {exc}") from exc

    def update_object(self, name: str, uuid: str, properties: dict[str, Any]) -> None:
        try:
            self.client.collections.get(name).data.update(uuid, properties=properties)
        except Exception as exc:
            raise BackendError(f"Updating object {uuid} failed: {exc}") from exc

    def delete_object(self, name: str, uuid: str) -> None:
        try:
            self.client.collections.get(name).data.delete_by_id(uuid)
        except Exception as exc:
            raise BackendError(f"Deleting object {uuid} failed: {exc}") from exc

    def delete_collection(self, name: str) -> None:
        try:
            self.client.collections.delete(name)
            self._types.pop(name, None)
        except Exception as exc:
            raise BackendError(f"Deleting collection {name} failed: {exc}") from exc
