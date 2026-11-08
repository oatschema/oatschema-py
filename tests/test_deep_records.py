# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import oatschema
from oatschema import ValidationError, available_versions, get_schema, get_validator, is_valid, iter_errors, validate
from oatschema._validation import STAND_IN_DEPTH, stand_in_deep_values

DEPTHS = [20, 255, 256, 300, 5000, 100_000]


def lists(depth: int, leaf: Any = 1) -> Any:
    for _ in range(depth):
        leaf = [leaf]
    return leaf


def objects(depth: int, leaf: Any = 1) -> Any:
    for _ in range(depth):
        leaf = {"a": leaf}
    return leaf


SHAPES: dict[str, Callable[[int], Any]] = {
    "list-in-attributes": lambda depth: {"labels": [{"label": "a"}], "attributes": {"k": lists(depth)}},
    "object-in-attributes": lambda depth: {"labels": [{"label": "a"}], "attributes": {"k": objects(depth)}},
    "extra-property": lambda depth: {"labels": [{"label": "a"}], "extra": lists(depth)},
    "in-a-label": lambda depth: {"labels": [{"label": "a", "score": objects(depth)}]},
    "root": lambda depth: lists(depth),
    "beside-another-error": lambda depth: {"labels": [{"label": 5}], "attributes": {"k": lists(depth)}},
}


def summary(error: ValidationError) -> tuple[Any, ...]:
    return error.path, error.schema_path, error.keyword, error.message


def first_error(record: Any) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        validate(record, "classification")
    return caught.value


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("depth", DEPTHS)
def test_deep_record_gets_the_errors_a_shallow_one_gets(shape: str, depth: int) -> None:
    shallow, deep = SHAPES[shape](10), SHAPES[shape](depth)
    assert is_valid(deep, "classification") is False
    assert summary(first_error(deep)) == summary(first_error(shallow))
    assert [summary(error) for error in iter_errors(deep, "classification")] == [
        summary(error) for error in iter_errors(shallow, "classification")
    ]


@pytest.mark.parametrize("check_formats", [True, False])
def test_every_entry_point_agrees_on_a_deep_record(check_formats: bool) -> None:
    record = SHAPES["list-in-attributes"](300)
    validator = get_validator("classification", check_formats=check_formats)
    assert validator.is_valid(record) is False
    with pytest.raises(ValidationError):
        validator.validate(record)
    assert len(list(validator.iter_errors(record))) == 1
    with pytest.raises(ValidationError):
        oatschema.check_numbers(record, "classification", check_formats=check_formats)


@pytest.mark.parametrize("version", available_versions())
def test_deep_record_is_reported_invalid_in_every_version(version: str) -> None:
    record = {"extra": lists(300)}
    for name in ("classification", "regression"):
        assert not is_valid(record, cast(Any, name), version)
        assert list(iter_errors(record, cast(Any, name), version))
        with pytest.raises(ValidationError):
            validate(record, cast(Any, name), version)


def test_deep_error_stays_small() -> None:
    record = {"labels": [{"label": "x" * 1_000_000}], "attributes": {"k": lists(300, "y" * 1_000_000)}}
    for error in (first_error(record), *iter_errors(record, "classification")):
        assert len(str(error)) < 1500
        assert error.__cause__ is None and error.__context__ is None


def test_cyclic_record_is_not_json_data() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    record = {"labels": [], "attributes": {"k": cyclic}}
    with pytest.raises(TypeError, match="JSON data"):
        validate(record, "classification")
    with pytest.raises(TypeError, match="JSON data"):
        iter_errors(record, "classification")


@pytest.mark.parametrize("depth", [10, 300])
def test_value_that_is_not_json_is_refused_at_any_depth(depth: int) -> None:
    record = {"labels": [], "attributes": {"k": lists(depth, {1, 2})}}
    with pytest.raises(TypeError, match="JSON data"):
        validate(record, "classification")
    with pytest.raises(TypeError, match="JSON data"):
        iter_errors(record, "classification")


@pytest.mark.parametrize(
    ("first", "second", "duplicate"),
    [
        (1, 1, True),
        (1, 1.0, True),
        (1, 2, False),
        (1, True, False),
        ("1", 1, False),
        ({"x": 1, "y": 2}, {"y": 2, "x": 1}, True),
        ({"x": 1}, {"x": 1, "y": 2}, False),
        ([1, 2], [2, 1], False),
        (None, None, True),
    ],
)
@pytest.mark.parametrize("depth", [10, 300])
def test_deep_values_are_compared_as_json(first: Any, second: Any, duplicate: bool, depth: int) -> None:
    record = {"labels": [], "vocabulary": [lists(depth, first), objects(depth, second)]}
    same_shape = {"labels": [], "vocabulary": [lists(depth, first), lists(depth, second)]}
    assert "uniqueItems" not in {error.keyword for error in iter_errors(record, "classification")}
    keywords = {error.keyword for error in iter_errors(same_shape, "classification")}
    assert ("uniqueItems" in keywords) is duplicate
    assert is_valid(same_shape, "classification") is False
    with pytest.raises(ValidationError):
        validate(same_shape, "classification")


@pytest.mark.parametrize("depth", [254, 255, 256, 300])
def test_unique_items_with_deep_items_gets_a_verdict(depth: int) -> None:
    def record(depth: int) -> dict[str, Any]:
        return {"vector": {"dimensions": 4, "indices": [lists(depth), lists(depth)], "values": [0.5, 0.5]}}

    validator = get_validator("embedding")
    assert validator.is_valid(record(depth)) is False
    assert list(map(summary, validator.iter_errors(record(depth)))) == list(
        map(summary, validator.iter_errors(record(10)))
    )
    with pytest.raises(ValidationError):
        validator.validate(record(depth))


def test_stand_ins_keep_shallow_values_and_share_unchanged_ones() -> None:
    record: dict[str, Any] = {"labels": [{"label": "a"}], "attributes": {"k": lists(300)}}
    stood_in = stand_in_deep_values(record)
    assert stood_in["labels"] is record["labels"]
    assert stood_in["attributes"] is not record["attributes"]
    assert record["attributes"]["k"] == lists(300)
    shallow = SHAPES["list-in-attributes"](STAND_IN_DEPTH - 3)
    assert stand_in_deep_values(shallow) is shallow


def test_no_bundled_schema_reaches_the_stand_in_depth() -> None:
    def depth(schema: Any, root: dict[str, Any], seen: tuple[int, ...]) -> int:
        if not isinstance(schema, dict):
            return 0
        assert id(schema) not in seen, "a recursive $ref"
        seen = (*seen, id(schema))
        same = [*schema.get("allOf", ()), *schema.get("anyOf", ()), *schema.get("oneOf", ())]
        same += [schema[key] for key in ("not", "if", "then", "else") if key in schema]
        if "$ref" in schema:
            target: Any = root
            for part in schema["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            same.append(target)
        below = [*schema.get("properties", {}).values(), *schema.get("prefixItems", ())]
        below += [
            schema[key] for key in ("additionalProperties", "items", "contains", "propertyNames") if key in schema
        ]
        return max([0, *(depth(item, root, seen) for item in same), *(1 + depth(item, root, seen) for item in below)])

    deepest = 0
    for version in available_versions():
        for path in (Path(oatschema.__file__).parent / "schemas" / version).glob("*.json"):
            if path.name != "MANIFEST.json":
                schema = get_schema(cast(Any, path.stem), version)
                deepest = max(deepest, depth(schema, schema, ()))
    assert deepest + 2 <= STAND_IN_DEPTH


def test_a_str_cannot_pass_for_a_stand_in() -> None:
    from oatschema._validation import _stand_in

    crafted = _stand_in(lists(300 - STAND_IN_DEPTH + 2))
    record = {"labels": [], "vocabulary": [lists(300), lists(STAND_IN_DEPTH - 2, crafted)]}
    assert "uniqueItems" not in {error.keyword for error in iter_errors(record, "classification")}


def test_is_valid_says_false_for_a_record_that_contains_itself() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    assert is_valid({"labels": [], "attributes": {"k": cyclic}}, "classification") is False
