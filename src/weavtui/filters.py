"""Parse a small filter language into Weaviate filters.

Grammar (case-insensitive keywords):
    filter     := condition ("and" condition)*
    condition  := property operator value
    operator   := = | != | < | <= | > | >= | like | contains
    value      := "quoted string" | bare-word

Examples:
    year >= 2020 and published = true
    title like "*vector*"
    tags contains nlp, ml          (any of the comma-separated values)
    id = 3f2a...                   (the object's UUID; = != contains)

Values are converted to the property's data type taken from the schema. ``id`` is the
object's UUID; Weaviate reserves that name, so it cannot clash with a property.
"""

from __future__ import annotations

import re
import shlex
import uuid
from datetime import datetime
from typing import Any

from weaviate.classes.query import Filter


class FilterError(ValueError):
    pass


ID = "id"
_ID_OPERATORS = ("=", "!=", "contains")


def _uuid(raw: str) -> str:
    value = _unquote(raw)
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise FilterError(f"{value!r} is not a valid UUID") from exc


def _id_condition(op: str, raw: str, part: str) -> Any:
    f = Filter.by_id()
    if op == "contains":
        values = [_uuid(v) for v in raw.split(",") if v.strip()]
        if not values:
            raise FilterError(f"no values after 'contains' in {part!r}")
        return f.contains_any(values)
    if op not in _ID_OPERATORS:
        raise FilterError(f"id supports only {', '.join(_ID_OPERATORS)}, not {op!r}")
    return f.equal(_uuid(raw)) if op == "=" else f.not_equal(_uuid(raw))


_COND = re.compile(
    r"^\s*(?P<prop>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<op>!=|<=|>=|=|<|>|\blike\b|\bcontains\b)\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_AND = re.compile(r"\s+and(?:\s+|$)", re.IGNORECASE)


def _split_and(text: str) -> list[str]:
    """Split on ' and ' that is not inside double quotes."""
    parts, start, in_quotes = [], 0, False
    for i, ch in enumerate(text):
        if ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes:
            m = _AND.match(text, i)
            if m and m.start() == i:
                parts.append(text[start:i])
                start = m.end()
    parts.append(text[start:])
    return [p for p in (p.strip() for p in parts)]


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            (value,) = shlex.split(raw)
        except ValueError as exc:
            raise FilterError(f"bad quoting in {raw!r}") from exc
        return value
    return raw


def convert(raw: str, data_type: str | None) -> Any:
    """Convert a textual value to the Python type matching a Weaviate data type."""
    value = _unquote(raw)
    base = (data_type or "text").removesuffix("[]")
    try:
        if base == "int":
            return int(value)
        if base == "number":
            return float(value)
        if base == "boolean":
            low = value.lower()
            if low not in ("true", "false"):
                raise ValueError("expected true or false")
            return low == "true"
        if base == "date":
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.astimezone()
    except ValueError as exc:
        raise FilterError(f"{value!r} is not a valid {base}: {exc}") from exc
    return value


def build_filter(text: str, types: dict[str, str]) -> Any | None:
    """Build a Weaviate filter from ``text``; ``types`` maps property name -> data type."""
    if not text.strip():
        return None
    conditions = []
    for part in _split_and(text):
        if not part:
            raise FilterError("empty condition next to 'and'")
        m = _COND.match(part)
        if not m:
            raise FilterError(f"cannot parse {part!r}; expected e.g. year >= 2020")
        prop, op, raw = m["prop"], m["op"].lower(), m["value"]
        if prop.lower() == ID:
            conditions.append(_id_condition(op, raw, part))
            continue
        if types and prop not in types:
            raise FilterError(f"unknown property {prop!r}")
        data_type = types.get(prop)
        f = Filter.by_property(prop)
        if op == "contains":
            values = [convert(v, data_type) for v in raw.split(",") if v.strip()]
            if not values:
                raise FilterError(f"no values after 'contains' in {part!r}")
            conditions.append(f.contains_any(values))
            continue
        value = convert(raw, data_type)
        conditions.append(
            {
                "=": f.equal,
                "!=": f.not_equal,
                "<": f.less_than,
                "<=": f.less_or_equal,
                ">": f.greater_than,
                ">=": f.greater_or_equal,
                "like": f.like,
            }[op](value)
        )
    return conditions[0] if len(conditions) == 1 else Filter.all_of(conditions)
