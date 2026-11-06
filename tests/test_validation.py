# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import os
import pickle
import struct
import subprocess
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, cast, get_args

import pytest

import oatschema
from oatschema import (
    SCHEMA_VERSION,
    SchemaName,
    ValidationError,
    Validator,
    available_versions,
    get_validator,
    is_valid,
    iter_errors,
    validate,
)

OATS_REPO = Path(os.environ.get("OATS_REPO", Path.home() / "projects" / "oats"))
SEGMENT = {"label": "scene", "start_time": 0, "end_time": 1.5}


def test_valid_record_passes() -> None:
    record = {"segments": [SEGMENT], "vocabulary_url": "https://example.com/vocabulary"}
    validate(record, "temporal-segmentation")
    assert is_valid(record, "temporal-segmentation")


def test_invalid_record_raises_package_error() -> None:
    record = {"segments": [{**SEGMENT, "start_time": -1}]}
    assert not is_valid(record, "temporal-segmentation")
    with pytest.raises(ValidationError) as caught:
        validate(record, "temporal-segmentation")
    error = caught.value
    assert isinstance(error, ValueError)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.schema_name == "temporal-segmentation"
    assert error.schema_version == SCHEMA_VERSION
    assert error.path == ("segments", 0, "start_time")
    assert error.schema_path[-1] == "minimum"
    assert error.keyword == "minimum"
    assert error.message == "… is less than the minimum of 0"
    assert str(error).startswith(f"temporal-segmentation {SCHEMA_VERSION} at /segments/0/start_time: ")


def test_formats_are_asserted() -> None:
    assert not is_valid({"segments": [SEGMENT], "vocabulary_url": "not a uri"}, "temporal-segmentation")


BAD_FORMATS = {"labels": [{"label": "cat", "score": 0.5, "timestamp": "not a date"}], "vocabulary_url": "not a uri"}


@pytest.mark.parametrize("version", ["0.5.0", "0.8.0"])
def test_format_checking_can_be_turned_off(version: str) -> None:
    errors = list(iter_errors(BAD_FORMATS, "classification", version))
    assert [(error.keyword, error.path) for error in errors] == [
        ("format", ("labels", 0, "timestamp")),
        ("format", ("vocabulary_url",)),
    ]
    assert not is_valid(BAD_FORMATS, "classification", version)
    assert not get_validator("classification", version).is_valid(BAD_FORMATS)
    with pytest.raises(ValidationError):
        validate(BAD_FORMATS, "classification", version)

    validate(BAD_FORMATS, "classification", version, check_formats=False)
    assert is_valid(BAD_FORMATS, "classification", version, check_formats=False)
    assert list(iter_errors(BAD_FORMATS, "classification", version, check_formats=False)) == []
    unchecked = get_validator("classification", version, check_formats=False)
    assert unchecked.is_valid(BAD_FORMATS)
    assert Validator("classification", version, check_formats=False).is_valid(BAD_FORMATS)
    assert not Validator("classification", version).is_valid(BAD_FORMATS)


def test_check_formats_is_part_of_the_cache_key() -> None:
    checked = get_validator("classification", "0.5.0")
    unchecked = get_validator("classification", "0.5.0", check_formats=False)
    assert checked is not unchecked
    assert checked is get_validator("classification", "0.5.0", check_formats=True)
    assert unchecked is get_validator("classification", "0.5.0", check_formats=False)
    assert (checked.check_formats, unchecked.check_formats) == (True, False)
    assert repr(checked) == "<oatschema.Validator classification 0.5.0>"
    assert repr(unchecked) == "<oatschema.Validator classification 0.5.0 check_formats=False>"


@pytest.mark.parametrize("value", [None, 0, 1, "no"])
def test_check_formats_must_be_a_bool(value: Any) -> None:
    get_validator("classification", "0.5.0")
    get_validator("classification", "0.5.0", check_formats=False)
    calls: list[Callable[[], object]] = [
        lambda: get_validator("classification", "0.5.0", check_formats=value),
        lambda: Validator("classification", "0.5.0", check_formats=value),
        lambda: validate({}, "classification", "0.5.0", check_formats=value),
        lambda: is_valid({}, "classification", "0.5.0", check_formats=value),
        lambda: iter_errors({}, "classification", "0.5.0", check_formats=value),
    ]
    for call in calls:
        with pytest.raises(TypeError, match="check_formats must be True or False"):
            call()


def test_validation_error_can_be_pickled_and_copied() -> None:
    record = {"segments": [{**SEGMENT, "start_time": -1}]}
    with pytest.raises(ValidationError) as caught:
        validate(record, "temporal-segmentation")
    error = caught.value
    error.add_note("a note")
    copies = [pickle.loads(pickle.dumps(error, protocol)) for protocol in range(pickle.HIGHEST_PROTOCOL + 1)]
    for clone in [*copies, copy.copy(error), copy.deepcopy(error)]:
        assert type(clone) is ValidationError
        assert clone.args == error.args
        assert vars(clone) == vars(error)
        assert str(clone) == str(error)


def test_version_selects_schema() -> None:
    record = {"segments": [SEGMENT]}
    with pytest.raises(LookupError):
        validate(record, "temporal-segmentation", "0.6.0")
    assert not is_valid({}, "classification", "0.4.0")
    with pytest.raises(ValidationError) as caught:
        validate({}, "classification", "0.4.0")
    assert caught.value.schema_version == "0.4.0"


def test_get_validator_is_cached_per_resolved_version() -> None:
    assert get_validator("classification") is get_validator("classification", SCHEMA_VERSION)
    assert get_validator("classification", "0.6.0") is not get_validator("classification", "0.5.0")


def test_validator_is_package_owned() -> None:
    validator = get_validator("classification", "0.6.0")
    assert isinstance(validator, Validator)
    assert validator.schema_name == "classification"
    assert validator.schema_version == "0.6.0"
    assert repr(validator) == "<oatschema.Validator classification 0.6.0>"
    schema = validator.schema
    assert schema == oatschema.get_schema("classification", "0.6.0")
    schema["mutated"] = True
    assert "mutated" not in validator.schema
    for attribute in ("schema_name", "schema_version", "schema", "check_formats"):
        with pytest.raises(AttributeError):
            setattr(validator, attribute, None)
    with pytest.raises(AttributeError):
        validator.extra = None  # type: ignore[attr-defined]


def test_validator_methods_match_module_functions() -> None:
    validator = get_validator("temporal-segmentation")
    record = {"segments": [{**SEGMENT, "start_time": -1, "end_time": "x"}, {"label": 3}], "extra": 1}
    assert validator.is_valid(record) is is_valid(record, "temporal-segmentation") is False
    errors = list(validator.iter_errors(record))
    assert [str(error) for error in errors] == [str(error) for error in iter_errors(record, "temporal-segmentation")]
    assert len(errors) > 3
    assert all(isinstance(error, ValidationError) and error.__cause__ is None for error in errors)
    for call in (validator.validate, lambda record: validate(record, "temporal-segmentation")):
        with pytest.raises(ValidationError) as caught:
            call(record)
        assert str(caught.value) in {str(error) for error in errors}
    assert list(validator.iter_errors({"segments": [SEGMENT]})) == []
    validator.validate({"segments": [SEGMENT]})


def test_iter_errors_checks_arguments_eagerly() -> None:
    with pytest.raises(ValueError, match="unknown schema name"):
        iter_errors({}, "nope")  # type: ignore[arg-type]


@pytest.mark.parametrize("call", [validate, is_valid, iter_errors])
def test_validation_rejects_unknown_name_and_version(call: Any) -> None:
    with pytest.raises(ValueError, match="unknown schema name"):
        call({}, "nope")
    with pytest.raises(LookupError):
        call({}, "classification", "0.7")


def test_works_without_validate_extra() -> None:
    script = """
import sys
sys.modules["jsonschema_rs"] = None
import oatschema
assert oatschema.get_schema("classification")["x-version"] == oatschema.SCHEMA_VERSION
for call in (oatschema.get_validator, oatschema.Validator, oatschema.validate, oatschema.is_valid, oatschema.iter_errors):
    args = ("classification",) if call in (oatschema.get_validator, oatschema.Validator) else ({}, "classification")
    try:
        call(*args)
    except ImportError as error:
        assert "pip install 'oatschema[validate]'" in str(error), error
    else:
        raise AssertionError(f"{call.__name__} did not raise ImportError")
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def schema_for_example(stem: str, names: frozenset[str]) -> SchemaName:
    matches = [name for name in names if stem.startswith(f"{name}-")]
    assert matches, f"no schema for example {stem}"
    return cast(SchemaName, max(matches, key=len))


def tag_examples(version: str, kind: str) -> list[tuple[str, SchemaName, Any]]:
    tag = f"v{version}"
    listing = subprocess.run(
        ["git", "-C", str(OATS_REPO), "ls-tree", "-r", "--name-only", tag],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    paths = [path for path in listing if f"examples/{kind}/" in path and path.endswith(".json")]
    names = frozenset(get_args(SchemaName))
    examples = []
    for path in paths:
        content = subprocess.run(
            ["git", "-C", str(OATS_REPO), "show", f"{tag}:{path}"], capture_output=True, check=True
        ).stdout
        stem = Path(path).stem
        examples.append((stem, schema_for_example(stem, names), json.loads(content)))
    assert examples, f"{tag} has no {kind} examples"
    return examples


needs_oats = pytest.mark.skipif(not (OATS_REPO / ".git").exists(), reason="set OATS_REPO to a checkout of oats")


@needs_oats
@pytest.mark.parametrize("version", available_versions())
def test_release_examples(version: str) -> None:
    for _stem, name, record in tag_examples(version, "positive"):
        validate(record, name, version)
    for stem, name, record in tag_examples(version, "negative"):
        assert not is_valid(record, name, version), stem
        errors = list(iter_errors(record, name, version))
        assert errors, stem
        assert all(len(str(error)) < 1500 for error in errors), stem
        with pytest.raises(ValidationError) as caught:
            validate(record, name, version)
        assert str(caught.value) in {str(error) for error in errors}, stem


def test_validation_api_is_exported() -> None:
    assert {"ValidationError", "Validator", "get_validator", "is_valid", "iter_errors", "validate"} <= set(
        oatschema.__all__
    )


RECORD = {"labels": [{"label": "cat", "score": 0.5}]}
RECORD_CALLS: list[Callable[..., Any]] = [validate, is_valid, iter_errors]
VALIDATOR_CALLS: list[Callable[..., Any]] = [get_validator, Validator]


@pytest.mark.parametrize("call", RECORD_CALLS)
@pytest.mark.parametrize("record", [RECORD, [RECORD], b"{}"])
def test_swapped_arguments_name_the_expected_order(call: Callable[..., Any], record: Any) -> None:
    with pytest.raises(TypeError, match=rf"{call.__name__}\(\) takes the record first, then the schema name"):
        call("classification", record)
    with pytest.raises(TypeError, match=rf"{call.__name__}\(\) takes the record first"):
        call("classification", record, "0.8.0", check_formats=False)


@pytest.mark.parametrize("call", RECORD_CALLS)
@pytest.mark.parametrize("name", [None, 1, b"classification", ["classification"], {"name": "classification"}])
def test_record_calls_reject_a_non_str_name(call: Callable[..., Any], name: Any) -> None:
    expected = rf"expected {call.__name__}\(record, name, version=None, \*, check_formats=True\)"
    with pytest.raises(TypeError, match=rf"the schema name must be a str, not {type(name).__name__}; {expected}"):
        call(RECORD, name)


@pytest.mark.parametrize("call", RECORD_CALLS)
@pytest.mark.parametrize("version", [8, 0.8, b"0.8.0", ("0", "8", "0"), {"version": "0.8.0"}])
def test_record_calls_reject_a_non_str_version(call: Callable[..., Any], version: Any) -> None:
    with pytest.raises(TypeError, match=rf"version must be a str .*; expected {call.__name__}\(record, name"):
        call(RECORD, "classification", version)


@pytest.mark.parametrize("call", VALIDATOR_CALLS)
@pytest.mark.parametrize(
    ("name", "version"), [(RECORD, None), (1, None), ("classification", 8), ("classification", [])]
)
def test_validator_calls_reject_wrong_types(call: Callable[..., Any], name: Any, version: Any) -> None:
    with pytest.raises(TypeError, match=rf"expected {call.__name__}\(name, version=None, \*, check_formats=True\)"):
        call(name, version)


@pytest.mark.parametrize("value", [[], {}, {"x": 1}])
def test_unhashable_check_formats_is_a_type_error(value: Any) -> None:
    calls: list[Callable[[], object]] = [
        lambda: get_validator("classification", check_formats=value),
        lambda: Validator("classification", check_formats=value),
        lambda: validate({}, "classification", check_formats=value),
        lambda: is_valid({}, "classification", check_formats=value),
        lambda: iter_errors({}, "classification", check_formats=value),
    ]
    for call in calls:
        with pytest.raises(TypeError, match="check_formats must be True or False"):
            call()


def test_argument_types_are_checked_before_the_caches() -> None:
    from oatschema import _validation

    _validation.lookup.cache_clear()
    _validation._validator.cache_clear()
    record: Any = RECORD
    calls: list[Callable[[], object]] = [
        lambda: validate("classification", record),
        lambda: is_valid(record, "classification", cast(Any, 8)),
        lambda: iter_errors(record, "classification", check_formats=cast(Any, None)),
        lambda: get_validator(record),
    ]
    for call in calls:
        with pytest.raises(TypeError):
            call()
    assert _validation.lookup.cache_info().currsize == 0
    assert _validation._validator.cache_info().currsize == 0


@pytest.mark.parametrize("record", [[], None, "classification", 1, 1.5, True, [RECORD]])
def test_non_object_records_are_still_reported_as_invalid(record: Any) -> None:
    assert not is_valid(record, "classification")
    with pytest.raises(ValidationError, match="is not of type"):
        validate(record, "classification")
    assert [error.keyword for error in iter_errors(record, "classification")] == ["type"]
    assert not get_validator("classification").is_valid(record)


def test_wrong_types_are_reported_without_the_validate_extra() -> None:
    script = """
import sys
sys.modules["jsonschema_rs"] = None
import oatschema
for call, args in [
    (oatschema.validate, ("classification", {})),
    (oatschema.is_valid, ({}, 1)),
    (oatschema.iter_errors, ({}, "classification", 8)),
    (oatschema.get_validator, ({},)),
    (oatschema.Validator, ("classification", 8)),
]:
    try:
        call(*args)
    except TypeError:
        pass
    else:
        raise AssertionError(f"{call.__name__} did not raise TypeError")
"""
    subprocess.run([sys.executable, "-c", script], check=True)


@pytest.mark.parametrize("cls", [ValidationError, Validator])
def test_public_classes_belong_to_the_package(cls: type) -> None:
    assert cls.__module__ == "oatschema"
    assert getattr(oatschema, cls.__qualname__) is cls


def test_validation_error_reads_as_package_class() -> None:
    with pytest.raises(ValidationError) as caught:
        validate({"labels": [{"label": "cat", "score": 1.5}]}, "classification")
    error = caught.value
    assert repr(error).startswith("ValidationError(")
    assert caught.exconly().startswith("oatschema.ValidationError: classification 0.9.0 at /labels/0/score: ")
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        data = pickle.dumps(error, protocol)
        assert b"oatschema._validation" not in data
        clone = pickle.loads(data)
        assert type(clone) is ValidationError
        assert clone.args == error.args
        assert vars(clone) == vars(error)
        assert (clone.message, clone.schema_name, clone.schema_version) == (
            error.message,
            error.schema_name,
            error.schema_version,
        )
        assert (clone.path, clone.schema_path, clone.keyword) == (error.path, error.schema_path, error.keyword)


def test_uncaught_validation_error_traceback_names_the_package() -> None:
    script = "import oatschema; oatschema.validate({'labels': [{'label': 'cat', 'score': 1.5}]}, 'classification')"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 1
    last = result.stderr.strip().splitlines()[-1]
    assert last.startswith("oatschema.ValidationError: classification 0.9.0 at /labels/0/score: "), last


def test_swapped_arguments_traceback_is_a_clear_type_error() -> None:
    script = "import oatschema; oatschema.is_valid('classification', {'labels': []})"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    last = result.stderr.strip().splitlines()[-1]
    assert last == (
        "TypeError: is_valid() takes the record first, then the schema name; "
        "expected is_valid(record, name, version=None, *, check_formats=True)"
    ), last


PICKLED_BY_0_8_0_1 = (
    b"\x80\x04\x95\x17\x01\x00\x00\x00\x00\x00\x00\x8c\x15oatschema._validation\x94\x8c\x0fVal"
    b"idationError\x94\x93\x94\x8c$\xe2\x80\xa6 is greater than the"
    b" maximum of 1\x94\x85\x94\x81\x94}\x94(\x8c\x07message\x94h\x03\x8c\x0bschem"
    b"a_name\x94\x8c\x0eclassification\x94\x8c\x0eschema_version"
    b"\x94\x8c\x050.8.0\x94\x8c\x04path\x94\x8c\x06labels\x94K\x00\x8c\x05score\x94\x87\x94\x8c\x0bs"
    b"chema_path\x94(\x8c\nproperties\x94\x8c\x06labels\x94\x8c\x05item"
    b"s\x94\x8c\nproperties\x94\x8c\x05score\x94\x8c\x07maximum\x94t\x94\x8c\x07key"
    b"word\x94h\x16ub."
)


def test_errors_pickled_by_0_8_0_1_still_load() -> None:
    assert b"oatschema._validation" in PICKLED_BY_0_8_0_1
    error = pickle.loads(PICKLED_BY_0_8_0_1)
    assert type(error) is ValidationError
    assert vars(error) == {
        "message": "… is greater than the maximum of 1",
        "schema_name": "classification",
        "schema_version": "0.8.0",
        "path": ("labels", 0, "score"),
        "schema_path": ("properties", "labels", "items", "properties", "score", "maximum"),
        "keyword": "maximum",
    }


NOT_JSON = [{1, 2}, object(), {1: "a"}, {"labels": [{"label": b"cat"}]}, {"labels": [{"label": "cat", "score": 1j}]}]


@pytest.mark.parametrize("record", NOT_JSON)
def test_records_that_are_not_json_raise_type_error(record: Any) -> None:
    validator = get_validator("classification")
    calls: list[Callable[[], object]] = [
        lambda: validate(record, "classification"),
        lambda: is_valid(record, "classification"),
        lambda: iter_errors(record, "classification"),
        lambda: validator.validate(record),
        lambda: validator.is_valid(record),
        lambda: validator.iter_errors(record),
    ]
    for call in calls:
        with pytest.raises(TypeError, match="the record must be JSON data") as caught:
            call()
        assert caught.value.__cause__ is None
        assert not isinstance(caught.value, ValueError)


@pytest.mark.parametrize("call", RECORD_CALLS)
def test_check_formats_error_names_the_expected_call(call: Callable[..., Any]) -> None:
    with pytest.raises(TypeError, match=rf"; expected {call.__name__}\(record, name, version=None"):
        call(RECORD, "classification", check_formats=1)


FORMAT_VERDICTS = json.loads((Path(__file__).parent / "data" / "format_verdicts.json").read_text(encoding="utf-8"))


def format_record(kind: str, value: str) -> dict[str, Any]:
    if kind == "date-time":
        return {"labels": [{"label": "x", "timestamp": value}]}
    return {"labels": [{"label": "x"}], "vocabulary_url": value}


def test_format_verdicts_match_the_javascript_build() -> None:
    wrong = [
        (case["format"], case["value"])
        for case in FORMAT_VERDICTS
        if is_valid(format_record(case["format"], case["value"]), "classification") is not case["valid"]
    ]
    assert wrong == []


@pytest.mark.parametrize("version", ["0.7.0", "0.8.0", "0.9.0"])
@pytest.mark.parametrize(
    "value",
    [
        "-----11-06T10:00:00Z",
        "+,-./11-06T10:00:00Z",
        "2026-11-0/T10:00:00Z",
        "2026-11-06T1,:00:00Z",
        "2026-11-06T10:0.:00Z",
        "2026-11-06T10:00:0/Z",
        "2026-11-06T10:00:00+0.:00",
        "2026-11-06T10:00:00+00:0-",
        "٢٠٢٦-11-06T10:00:00Z",
        "2026-11-06T10:00:00Z\n",
    ],
)
def test_date_time_with_a_non_digit_is_invalid(value: str, version: str) -> None:
    record = format_record("date-time", value)
    assert not is_valid(record, "classification", version)
    assert is_valid(record, "classification", version, check_formats=False)
    errors = list(iter_errors(record, "classification", version))
    assert [error.keyword for error in errors] == ["format"]
    assert value not in str(errors[0])


@pytest.mark.parametrize(
    "value",
    ["2026-11-06T10:00:00Z", "2026-11-06t10:00:00z", "2024-02-29T00:00:00+05:30", "2026-12-31T23:59:60Z"],
)
def test_date_time_still_accepts_real_times(value: str) -> None:
    assert is_valid(format_record("date-time", value), "classification")


SURROGATE_RECORDS: list[tuple[Any, tuple[str | int, ...]]] = [
    ({"labels": [], "vocabulary": ["a", "\ud800"]}, ("vocabulary", 1)),
    ({"labels": [], "producer": "\ud800"}, ("producer",)),
    ({"labels": [], "producer": "x" * 5000 + "\udfff"}, ("producer",)),
    ({"labels": [{"label": "\udc00"}]}, ("labels", 0, "label")),
    ({"labels": [{"label": "x", "attributes": {"\ud800": 1}}]}, ("labels", 0, "attributes", "\ud800")),
    ({"labels": [], "\ud800": 1}, ("\ud800",)),
    ({"labels": [], "vocabulary": ("a", "\ud800")}, ("vocabulary", 1)),
    ({"labels": [], "producer": "\ud83d" + "\ude00"}, ("producer",)),
    ("\ud800", ()),
    (["\ud800"], (0,)),
]


class StrSubclass(str):
    pass


@pytest.mark.parametrize("version", ["0.5.0", "0.8.0", "0.9.0"])
@pytest.mark.parametrize(("record", "path"), SURROGATE_RECORDS)
def test_unpaired_surrogate_is_invalid(record: Any, path: tuple[str | int, ...], version: str) -> None:
    assert is_valid(record, "classification", version) is False
    assert is_valid(record, "classification", version, check_formats=False) is False
    with pytest.raises(ValidationError) as caught:
        validate(record, "classification", version)
    assert caught.value.path == path
    assert caught.value.keyword == ""
    assert "surrogate" in caught.value.message
    assert [error.path for error in iter_errors(record, "classification", version)] == [path]
    str(caught.value).encode("utf-8")
    assert pickle.loads(pickle.dumps(caught.value)).path == path


def test_every_unpaired_surrogate_is_reported() -> None:
    record = {"labels": [{"label": "\ud800"}, {"label": "\udc00"}], "producer": "\udfff"}
    assert {error.path for error in iter_errors(record, "classification")} == {
        ("labels", 0, "label"),
        ("labels", 1, "label"),
        ("producer",),
    }


def test_surrogate_in_a_type_the_fast_scan_cannot_read_is_found() -> None:
    assert is_valid({"labels": [], "producer": StrSubclass("\ud800")}, "classification") is False
    with pytest.raises(TypeError, match="JSON data"):
        is_valid({"labels": [], "producer": StrSubclass("fine")}, "classification")


def test_surrogate_in_a_plain_enum_is_found() -> None:
    class Text(Enum):
        LONE = "\ud800"

    record = {"labels": [{"label": "x", "attributes": {"n": Text.LONE}}]}
    assert is_valid(record, "classification") is False
    assert [error.path for error in iter_errors(record, "classification")] == [("labels", 0, "attributes", "n")]


def test_surrogate_scan_does_not_flag_bytes_that_look_like_one() -> None:
    lookalike = struct.unpack("<d", b"\xed\xa0\x80\x00\x00\x00\xe0\x3f")[0]
    record = {"labels": [{"label": "໐퟿😀", "score": lookalike, "attributes": {"n": lookalike}}]}
    assert is_valid(record, "classification") is True


def test_paired_escapes_from_json_text_are_one_valid_character() -> None:
    assert is_valid(json.loads('{"labels": [{"label": "\\ud83d\\ude00"}]}'), "classification") is True


def test_surrogate_scan_ends_on_cyclic_and_deep_records() -> None:
    cyclic: list[Any] = ["\xed"]
    cyclic.append(cyclic)
    deep: list[Any] = []
    for _ in range(100_000):
        deep = [deep]
    for record in (cyclic, deep):
        assert not is_valid(record, "classification")
