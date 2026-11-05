from datetime import datetime

import pytest

from weavtui.filters import FilterError, build_filter, convert

TYPES = {
    "title": "text",
    "year": "int",
    "rating": "number",
    "published": "boolean",
    "published_at": "date",
    "tags": "text[]",
}


def test_empty_filter_is_none():
    assert build_filter("  ", TYPES) is None


def test_single_condition_converts_type():
    f = build_filter("year >= 2020", TYPES)
    assert f.target == "year"
    assert f.value == 2020
    assert f.operator.name == "GREATER_THAN_EQUAL"


def test_and_combines_conditions():
    f = build_filter('year > 2020 AND published = true and title like "*a and b*"', TYPES)
    assert [c.value for c in f.filters] == [2020, True, "*a and b*"]


def test_contains_any_splits_values():
    f = build_filter("tags contains nlp, ml", TYPES)
    assert f.value == ["nlp", "ml"]
    assert f.operator.name == "CONTAINS_ANY"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("nope = 1", "unknown property"),
        ("year = abc", "not a valid int"),
        ("published = yes", "not a valid boolean"),
        ("year", "cannot parse"),
        ("year = 1 and", "empty condition"),
    ],
)
def test_errors(text, message):
    with pytest.raises(FilterError, match=message):
        build_filter(text, TYPES)


def test_convert_date_gets_timezone():
    value = convert("2024-01-02", "date")
    assert isinstance(value, datetime)
    assert value.tzinfo is not None


def test_unknown_types_pass_through_without_schema():
    assert build_filter("anything = x", {}).value == "x"


UUID = "3f2a9c1e-8b7d-4e6f-9a01-23456789abcd"


def test_id_equal_filters_by_uuid():
    f = build_filter(f"id = {UUID.upper()}", TYPES)
    assert f.target == "_id"
    assert f.value == UUID
    assert f.operator.name == "EQUAL"


def test_id_contains_and_combination():
    other = "00000000-0000-0000-0000-000000000001"
    f = build_filter(f'id contains {UUID}, "{other}" and year > 2000', TYPES)
    assert f.filters[0].value == [UUID, other]
    assert f.filters[1].value == 2000


@pytest.mark.parametrize("text", ["id = nope", "id > " + UUID, "id contains ,"])
def test_id_errors(text):
    with pytest.raises(FilterError):
        build_filter(text, TYPES)
