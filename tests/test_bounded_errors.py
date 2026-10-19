# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import gc
import inspect
import traceback
import types
from typing import Any, get_type_hints

import jsonschema_rs
import pytest

import oatschema
from oatschema import SchemaName, ValidationError, get_validator, iter_errors, validate
from oatschema._validation import _DESCRIPTIONS

HUGE = "y" * 5_000_000
POINT = {"value": 1.0}

CASES: list[tuple[str, SchemaName, dict[str, Any]]] = [
    ("maxLength", "classification", {"labels": [{"label": HUGE}]}),
    ("type", "classification", {"labels": [{"label": "cat", "score": HUGE}]}),
    ("enum", "regression", {"points": [{**POINT, "statistic": HUGE}]}),
    ("const", "regression", {"points": [{**POINT, "statistic": HUGE, "quantile_level": 0.5}]}),
    ("pattern", "audio-transcription", {"text": "hi", "language": HUGE}),
    ("format", "classification", {"labels": [], "vocabulary_url": HUGE}),
    ("additionalProperties", "classification", {"labels": [], HUGE: 1}),
    ("maxLength", "classification", {"labels": [], "attributes": {HUGE: 1}}),
    ("anyOf", "classification", {"labels": [], "attributes": {"key": ["y" * 100] * 50_000}}),
    ("uniqueItems", "classification", {"labels": [], "vocabulary": [HUGE[:-1], HUGE[:-1]]}),
    ("oneOf", "embedding", {"vector": HUGE}),
]


def formatted_traceback(error: ValidationError) -> str:
    try:
        raise error
    except ValidationError as raised:
        return "".join(traceback.format_exception(raised))


def matching_error(keyword: str, name: SchemaName, record: dict[str, Any]) -> ValidationError:
    errors = [error for error in iter_errors(record, name) if error.keyword == keyword]
    assert errors, f"no {keyword} error"
    return errors[0]


def assert_bounded(error: ValidationError) -> None:
    assert len(error.message) <= 1000
    assert len(str(error)) <= 1000
    assert len(repr(error)) <= 1000
    assert len(formatted_traceback(error)) <= 2000
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "instance")
    assert all(len(repr(value)) <= 1000 for key, value in vars(error).items() if key != "path")


@pytest.mark.parametrize(("keyword", "name", "record"), CASES, ids=[case[0] for case in CASES])
def test_errors_are_bounded_for_huge_records(keyword: str, name: SchemaName, record: dict[str, Any]) -> None:
    assert_bounded(matching_error(keyword, name, record))
    with pytest.raises(ValidationError) as caught:
        validate(record, name)
    assert_bounded(caught.value)
    assert len("".join(traceback.format_exception(caught.value))) <= 2000


def test_long_strings_are_previewed_with_their_length() -> None:
    error = matching_error("maxLength", "classification", {"labels": [{"label": HUGE}]})
    assert error.message == f'"{"y" * 97}…" (5,000,000 characters) is longer than 128 characters'


def test_long_path_parts_are_shortened_in_str_only() -> None:
    record = {"labels": [], "attributes": {HUGE: [1]}}
    error = matching_error("anyOf", "classification", record)
    assert error.path == ("attributes", HUGE)
    assert_bounded(error)


def reachable(root: object) -> list[object]:
    seen: set[int] = set()
    stack, found = [root], []
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj, types.TracebackType | types.FrameType | type):
            continue
        seen.add(id(obj))
        found.append(obj)
        stack.extend(gc.get_referents(obj))
    return found


def test_errors_keep_neither_the_value_nor_the_jsonschema_rs_error() -> None:
    record = {"labels": [{"label": HUGE}]}
    with pytest.raises(ValidationError) as caught:
        validate(record, "classification")
    errors = [caught.value, *iter_errors(record, "classification")]
    del caught
    gc.collect()
    assert not [obj for obj in gc.get_objects() if isinstance(obj, jsonschema_rs.ValidationError)]
    for error in errors:
        objects = reachable(error)
        assert not [obj for obj in objects if isinstance(obj, jsonschema_rs.ValidationError)]
        assert not [obj for obj in objects if isinstance(obj, str) and len(obj) > 1000]


SYNTHETIC: dict[str, Any] = {
    "type": "object",
    "properties": {
        "short": {"minLength": 6_000_000},
        "long": {"maxLength": 1},
        "items": {"maxItems": 1, "minItems": 10, "uniqueItems": True, "contains": {"type": "integer"}},
        "object": {"maxProperties": 0, "minProperties": 5},
        "number": {"maximum": 0, "exclusiveMaximum": 0, "multipleOf": 7},
        "negative": {"minimum": 0, "exclusiveMinimum": 0},
        "not": {"not": {"type": "string"}},
        "false": False,
        "enum": {"enum": [HUGE[:-2], HUGE[:-3], 1, 2, 3, 4, 5]},
        "const": {"const": HUGE[:-4]},
        "unevaluated": {"type": "object", "unevaluatedProperties": False},
        "prefix": {"type": "array", "prefixItems": [True], "unevaluatedItems": False},
        "content": {"contentEncoding": "base64", "contentMediaType": "application/json"},
    },
    "required": [HUGE[:-5]],
}
SYNTHETIC_RECORD = {
    "short": HUGE,
    "long": HUGE,
    "items": [HUGE, HUGE],
    "object": {HUGE: HUGE},
    "number": 10**4000,
    "negative": -(10**4000),
    "not": HUGE,
    "false": HUGE,
    "enum": HUGE,
    "const": HUGE,
    "unevaluated": {HUGE: HUGE},
    "prefix": [HUGE, HUGE],
    "content": HUGE,
}


def test_every_kind_is_described_and_bounded() -> None:
    validator = jsonschema_rs.validator_for(SYNTHETIC)
    convert = get_validator("classification")._convert
    errors = [convert(error) for error in validator.iter_errors(SYNTHETIC_RECORD)]
    assert len(errors) >= 20
    for error in errors:
        assert_bounded(error)
        assert " is not valid under " not in error.message, error.message


def test_every_jsonschema_rs_kind_has_a_description() -> None:
    kinds = {name for name in dir(jsonschema_rs.ValidationErrorKind) if name[:1].isupper()}
    assert kinds <= set(_DESCRIPTIONS)


def test_public_api_does_not_mention_jsonschema_rs() -> None:
    targets = [getattr(oatschema, name) for name in oatschema.__all__]
    for name, member in vars(oatschema.Validator).items():
        if not name.startswith("_") or name == "__init__":
            targets.append(member.fget if isinstance(member, property) else member)
    for target in targets:
        if inspect.isfunction(target):
            assert "jsonschema" not in str(get_type_hints(target)), target
            assert "jsonschema" not in str(inspect.signature(target)), target
    assert {name for name in dir(get_validator("classification")) if not name.startswith("_")} == {
        "is_valid",
        "iter_errors",
        "schema",
        "schema_name",
        "schema_version",
        "validate",
    }
