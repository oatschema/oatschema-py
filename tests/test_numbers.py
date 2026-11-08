# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import math
import pickle
import warnings
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

import oatschema
from oatschema import (
    CheckedRecord,
    ConvertedNumber,
    DroppedNumber,
    DroppedNumberWarning,
    ValidationError,
    available_versions,
    check_numbers,
    get_schema,
    is_valid,
)

MAX = 9007199254740991
NAN = float("nan")
INF = float("inf")
CORPUS = json.loads((Path(__file__).parent / "data" / "number_corpus.json").read_text(encoding="utf-8"))

pytestmark = pytest.mark.filterwarnings("ignore::oatschema.DroppedNumberWarning")


def labelled(**fields: Any) -> dict[str, Any]:
    return {"labels": [{"label": "cat", **fields}]}


def ranked(rank: Any) -> dict[str, Any]:
    return {"results": [{"rank": rank, "id": "a"}]}


def seeded(seed: Any) -> dict[str, Any]:
    return {"model": "m", "response_data": "hi", "generation_params": {"seed": seed}}


def test_valid_record_is_copied_and_reports_nothing() -> None:
    record = labelled(score=0.5, attributes={"n": 3})
    before = copy.deepcopy(record)
    checked = check_numbers(record, "classification")
    assert checked == CheckedRecord(before, (), ())
    assert record == before
    assert checked.record is not record
    assert checked.record["labels"][0] is not record["labels"][0]


def test_input_is_never_modified() -> None:
    record = labelled(score=NAN, attributes={"n": -0.0, "m": 2**60})
    checked = check_numbers(record, "classification")
    assert math.isnan(record["labels"][0]["score"])
    assert math.copysign(1, record["labels"][0]["attributes"]["n"]) == -1
    assert record["labels"][0]["attributes"]["m"] == 2**60
    assert "score" not in checked.record["labels"][0]


@pytest.mark.parametrize("value", [MAX, -MAX, float(MAX), float(-MAX)])
def test_range_edge_is_kept_exactly(value: float) -> None:
    checked = check_numbers(labelled(attributes={"n": value}), "classification")
    assert checked.dropped == ()
    assert checked.record["labels"][0]["attributes"]["n"] == value
    assert type(checked.record["labels"][0]["attributes"]["n"]) is type(value)


@pytest.mark.parametrize("value", [MAX + 1, -MAX - 1, float(MAX + 1), 2**64, 2**1100, 1e308, -1e308])
@pytest.mark.parametrize("version", ["0.7.0", "0.8.0", "0.9.0"])
def test_one_past_the_range_is_dropped_in_every_version(value: float, version: str) -> None:
    checked = check_numbers(labelled(attributes={"n": value}), "classification", version)
    assert checked.record == labelled(attributes={})
    assert checked.dropped == (DroppedNumber(("labels", 0, "attributes", "n"), value, f"outside ±{MAX}"),)


@pytest.mark.parametrize("value", [NAN, INF, -INF, np.float32("nan"), np.float64("inf")])
def test_non_finite_is_dropped(value: float) -> None:
    checked = check_numbers(labelled(score=value), "classification")
    assert checked.record == labelled()
    assert len(checked.dropped) == 1
    assert checked.dropped[0].path == ("labels", 0, "score")
    assert checked.dropped[0].reason == "not a finite number"
    assert checked.dropped[0].value is value
    assert checked.converted == ()


@pytest.mark.parametrize(
    ("name", "record", "path"),
    [
        ("embedding", {"vector": [0.5, NAN], "model": "m"}, ("vector", 1)),
        ("embedding", {"vector": [0.5, 2**60], "model": "m"}, ("vector", 1)),
        ("classification", NAN, ()),
        ("classification", [NAN], (0,)),
    ],
)
def test_bad_list_element_or_top_level_rejects_the_record(name: Any, record: Any, path: tuple[Any, ...]) -> None:
    with pytest.raises(ValidationError) as caught:
        check_numbers(record, name)
    assert caught.value.path == path


def test_bad_required_value_rejects_the_record_and_names_it() -> None:
    with pytest.raises(ValidationError) as caught:
        check_numbers(ranked(2**60), "ranking")
    assert "required" in caught.value.message
    assert "/results/0/rank" in caught.value.message


def test_bad_number_in_strict_mode_raises_with_no_schema_keyword() -> None:
    with pytest.raises(ValidationError) as caught:
        check_numbers(labelled(score=NAN), "classification", on_bad_number="raise")
    assert caught.value.path == ("labels", 0, "score")
    assert caught.value.keyword == ""
    assert caught.value.message == "… is not a finite number"


@pytest.mark.parametrize("record", [labelled(attributes={"n": 2**60}), labelled(score=1.5)])
def test_strict_mode_rejects_every_kind_of_bad_number(record: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        check_numbers(record, "classification", on_bad_number="raise")


def test_strict_mode_still_makes_lossless_conversions() -> None:
    checked = check_numbers(ranked(1.0), "ranking", on_bad_number="raise")
    assert checked.record == ranked(1)
    assert type(checked.record["results"][0]["rank"]) is int
    assert checked.converted == (ConvertedNumber(("results", 0, "rank"), 1.0, 1),)


@pytest.mark.parametrize("score", [1.0000001, 1.5, -1.5])
def test_field_bound_is_a_bad_number(score: float) -> None:
    checked = check_numbers(labelled(score=score), "classification")
    assert checked.record == labelled()
    assert checked.dropped == (DroppedNumber(("labels", 0, "score"), score, "outside the bounds of this field"),)


def test_field_bound_inside_one_of_is_dropped() -> None:
    record = {"blocks": [], "page_width": -5}
    checked = check_numbers(record, "document-extraction")
    assert checked.record == {"blocks": []}
    assert [item.path for item in checked.dropped] == [("page_width",)]


def test_required_value_outside_field_bound_rejects() -> None:
    with pytest.raises(ValidationError):
        check_numbers(ranked(0), "ranking")


def test_conditional_bound_is_applied() -> None:
    box = {"x": 0.5, "y": 0.5, "width": 0.5, "height": 1.5}
    record = {"objects": [{"label": "a", "box": box}], "unit": "normalized"}
    assert not is_valid(record, "object-detection")
    with pytest.raises(ValidationError) as caught:
        check_numbers(record, "object-detection")
    assert "/objects/0/box/height" in caught.value.message


def test_value_in_bounds_of_a_later_branch_is_not_dropped() -> None:
    record = {"type": "Feature", "id": 7, "geometry": None, "properties": None}
    assert is_valid(record, "geolocation")
    assert check_numbers(record, "geolocation").dropped == ()


@pytest.mark.parametrize("version", ["0.8.0", "0.9.0"])
def test_negative_zero_becomes_zero(version: str) -> None:
    checked = check_numbers(labelled(score=-0.0), "classification", version)
    value = checked.record["labels"][0]["score"]
    assert value == 0.0 and math.copysign(1, value) == 1
    assert len(checked.converted) == 1
    assert math.copysign(1, checked.converted[0].before) == -1


def test_positive_zero_and_integer_zero_are_not_reported() -> None:
    assert check_numbers(labelled(score=0.0), "classification").converted == ()
    assert check_numbers(labelled(score=0), "classification").converted == ()


def test_whole_float_becomes_int_only_in_integer_field() -> None:
    assert check_numbers(ranked(2.0), "ranking").record == ranked(2)
    kept = check_numbers(labelled(score=1.0, attributes={"n": 2.0}), "classification")
    assert type(kept.record["labels"][0]["score"]) is float
    assert type(kept.record["labels"][0]["attributes"]["n"]) is float
    assert kept.converted == ()


def test_fractional_float_in_integer_field_is_never_rounded() -> None:
    with pytest.raises(ValidationError) as caught:
        check_numbers(ranked(1.5), "ranking")
    assert caught.value.keyword == "type"


def test_whole_float_above_range_is_not_made_an_int() -> None:
    with pytest.raises(ValidationError):
        check_numbers(ranked(1e20), "ranking")


@pytest.mark.parametrize("value", [True, False])
def test_bool_is_never_a_number(value: bool) -> None:
    checked = check_numbers(labelled(attributes={"n": value}), "classification")
    assert checked.record["labels"][0]["attributes"]["n"] is value
    assert checked.converted == ()
    with pytest.raises(ValidationError):
        check_numbers(ranked(value), "ranking")
    with pytest.raises(ValidationError):
        check_numbers(seeded(value), "llm-output")


@pytest.mark.parametrize(
    ("seed", "text"),
    [
        (42, "42"),
        (-7, "-7"),
        (0, "0"),
        (2**64 - 1, "18446744073709551615"),
        (10**127, "1" + "0" * 127),
        (np.uint64(2**64 - 1), "18446744073709551615"),
    ],
)
def test_int_seed_becomes_a_string_from_0_9_0(seed: Any, text: str) -> None:
    checked = check_numbers(seeded(seed), "llm-output")
    assert checked.record["generation_params"]["seed"] == text
    assert checked.converted == (ConvertedNumber(("generation_params", "seed"), seed, text),)
    assert checked.dropped == ()


@pytest.mark.parametrize("seed", [10**128, -(10**127), 10**5000], ids=["129-digits", "negative-128", "5001-digits"])
def test_seed_too_long_for_the_string_form_is_dropped(seed: int) -> None:
    checked = check_numbers(seeded(seed), "llm-output")
    assert "seed" not in checked.record["generation_params"]
    assert checked.dropped[0].value == seed


def test_string_seed_is_untouched() -> None:
    checked = check_numbers(seeded("42"), "llm-output")
    assert checked.converted == () and checked.dropped == ()


@pytest.mark.parametrize("seed", [42.0, -0.0, 1.5])
def test_float_seed_is_not_converted(seed: float) -> None:
    with pytest.raises(ValidationError):
        check_numbers(seeded(seed), "llm-output")


def test_seed_before_0_9_0_stays_an_int_and_out_of_range_is_dropped() -> None:
    kept = check_numbers(seeded(42), "llm-output", "0.8.0")
    assert kept.record["generation_params"]["seed"] == 42
    assert kept.converted == ()
    dropped = check_numbers(seeded(2**64), "llm-output", "0.8.0")
    assert "seed" not in dropped.record["generation_params"]
    assert dropped.converted == ()
    assert [item.value for item in dropped.dropped] == [2**64]


@pytest.mark.parametrize(
    ("value", "after", "kind"),
    [
        (np.float64(0.5), 0.5, float),
        (np.float32(0.5), 0.5, float),
        (np.float16(0.5), 0.5, float),
        (np.int64(3), 3, int),
        (np.uint8(3), 3, int),
    ],
)
def test_numpy_scalar_becomes_python_number(value: Any, after: float, kind: type) -> None:
    checked = check_numbers(labelled(attributes={"n": value}), "classification")
    result = checked.record["labels"][0]["attributes"]["n"]
    assert result == after and type(result) is kind
    assert checked.converted == (ConvertedNumber(("labels", 0, "attributes", "n"), value, after),)


def test_numpy_float32_keeps_its_exact_value() -> None:
    value = np.float32(0.1)
    checked = check_numbers(labelled(score=value), "classification")
    assert checked.record["labels"][0]["score"] == float(value) == 0.10000000149011612


def test_numpy_bool_and_str_become_python_values() -> None:
    checked = check_numbers(labelled(attributes={"a": np.True_, "b": np.str_("x")}), "classification")
    attributes = checked.record["labels"][0]["attributes"]
    assert attributes["a"] is True
    assert type(attributes["b"]) is str
    assert len(checked.converted) == 2


def test_numpy_array_becomes_a_list() -> None:
    array = np.array([0.5, 0.25])
    checked = check_numbers({"vector": array, "model": "m"}, "embedding")
    assert checked.record["vector"] == [0.5, 0.25]
    assert type(checked.record["vector"][0]) is float
    assert len(checked.converted) == 1
    assert checked.converted[0].path == ("vector",)
    assert checked.converted[0].before is array


def test_numpy_integer_array_in_integer_field() -> None:
    record = {"vector": {"indices": np.array([1, 2]), "values": np.array([0.5, 0.25]), "dimensions": np.int64(8)}}
    checked = check_numbers({**record, "model": "m"}, "embedding")
    assert checked.record["vector"] == {"indices": [1, 2], "values": [0.5, 0.25], "dimensions": 8}


def test_numpy_whole_float_in_integer_field_is_one_conversion() -> None:
    value = np.float32(2.0)
    checked = check_numbers(ranked(value), "ranking")
    assert type(checked.record["results"][0]["rank"]) is int
    assert checked.converted == (ConvertedNumber(("results", 0, "rank"), value, 2),)


def test_numpy_array_with_nan_rejects_the_record() -> None:
    with pytest.raises(ValidationError) as caught:
        check_numbers({"vector": np.array([0.5, np.nan]), "model": "m"}, "embedding")
    assert caught.value.path == ("vector", 1)


@pytest.mark.parametrize(
    "value",
    [
        np.datetime64("2026-11-06T10:00:00", "ns"),
        np.timedelta64(5, "ns"),
        np.longdouble(1.5),
        np.complex128(1 + 2j),
        np.array([1 + 2j]),
        np.array(["2026-11-06"], dtype="datetime64[ns]"),
        np.array([np.timedelta64(5, "ns")]),
        np.array([np.longdouble(1.5)]),
        np.ma.masked_array([1.0, 2.0], mask=[True, False]),
    ],
    ids=str,
)
def test_numpy_value_with_no_lossless_json_form_is_refused(value: Any) -> None:
    with pytest.raises(TypeError, match="JSON data"):
        check_numbers(labelled(attributes={"n": value}), "classification")


@pytest.mark.parametrize("record", [{1, 2}, labelled(attributes={"n": {1}}), {1: "a"}])
def test_non_json_record_raises_type_error(record: Any) -> None:
    with pytest.raises(TypeError, match="JSON data"):
        check_numbers(record, "classification")


class Plain(Enum):
    BIG = 2**60
    TEXT = "a"


@pytest.mark.parametrize(
    "value", [Decimal("1E+400"), Decimal(2**60), Decimal("-0"), Decimal("1"), Plain.BIG, Plain.TEXT]
)
@pytest.mark.parametrize("version", ["0.8.0", "0.9.0"])
def test_value_the_engine_would_unwrap_is_refused(value: Any, version: str) -> None:
    with pytest.raises(TypeError, match="JSON data"):
        check_numbers(labelled(attributes={"n": value}), "classification", version)


@pytest.mark.parametrize("value", [NAN, 2**60, 1])
def test_non_str_key_is_refused_whatever_its_value(value: float) -> None:
    with pytest.raises(TypeError, match="JSON data"):
        check_numbers({"labels": [], "attributes": {1: value}}, "classification")


def test_warning_survives_a_value_too_large_to_show() -> None:
    value = np.array(10**5000, dtype=object)
    with pytest.warns(DroppedNumberWarning, match="value of type ndarray"):
        checked = check_numbers({"labels": [], "attributes": {"n": value}}, "classification")
    assert checked.record == {"labels": [], "attributes": {}}


def test_cyclic_record_raises_type_error() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    looped: dict[str, Any] = {"labels": []}
    looped["labels"].append(looped)
    for record in (cyclic, looped, {"labels": [], "attributes": {"k": cyclic}}):
        with pytest.raises(TypeError, match="JSON data"):
            check_numbers(record, "classification")


def test_deep_record_is_invalid_not_a_type_error() -> None:
    deep: list[Any] = []
    for _ in range(100_000):
        deep = [deep]
    for record in (deep, {"labels": [], "attributes": {"k": deep}}, {"labels": [{"label": "a", "x": deep}]}):
        with pytest.raises(ValidationError):
            check_numbers(record, "classification")


@pytest.mark.parametrize("record", [[], None, "cat", 3, {}])
def test_record_that_is_not_valid_json_data_for_the_schema_is_invalid(record: Any) -> None:
    with pytest.raises(ValidationError):
        check_numbers(record, "classification")


def test_unpaired_surrogate_rejects_the_record() -> None:
    with pytest.raises(ValidationError) as caught:
        check_numbers(labelled(attributes={"a": "\ud800"}), "classification")
    assert "surrogate" in caught.value.message


def test_tuple_stays_a_tuple_and_dict_subclass_becomes_dict() -> None:
    from collections import OrderedDict

    checked = check_numbers({"labels": [], "vocabulary": ("a", "b")}, "classification")
    assert checked.record["vocabulary"] == ("a", "b")
    ordered: OrderedDict[str, list[Any]] = OrderedDict(labels=[])
    assert type(check_numbers(ordered, "classification").record) is dict


def test_dropping_reports_a_warning_with_paths_and_values() -> None:
    with pytest.warns(DroppedNumberWarning, match=r"dropped 2 .*classification 0\.9\.0.*labels/0/score \(nan\)"):
        check_numbers(labelled(score=NAN, attributes={"n": 2**60}), "classification")


def test_warning_points_at_the_caller() -> None:
    with pytest.warns(DroppedNumberWarning) as caught:
        check_numbers(labelled(score=NAN), "classification")
    assert caught[0].filename == __file__


def test_no_warning_without_a_drop_or_on_rejection() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        check_numbers(labelled(score=-0.0), "classification")
        with pytest.raises(ValidationError):
            check_numbers(ranked(NAN), "ranking")


def test_warning_and_errors_stay_small_whatever_the_record() -> None:
    long_key = "k" * 2_000_000
    huge = 2**4_000_000
    record = labelled(attributes={f"{index}{long_key}": huge for index in range(16)})
    with pytest.warns(DroppedNumberWarning) as caught:
        checked = check_numbers(record, "classification")
    assert len(checked.dropped) == 16
    assert len(str(caught[0].message)) < 2000
    assert "16" in str(caught[0].message) and "11 more" in str(caught[0].message)
    required = {"results": [{"rank": huge, "id": "a", long_key: huge}]}
    with pytest.raises(ValidationError) as rejected:
        check_numbers(required, "ranking")
    assert len(rejected.value.message) <= 1000
    assert len(str(rejected.value)) < 3000


def test_error_keeps_no_reference_to_the_value() -> None:
    with pytest.raises(ValidationError) as caught:
        check_numbers(labelled(score=2**4_000_000), "classification", on_bad_number="raise")
    assert caught.value.__cause__ is None
    assert len(pickle.dumps(caught.value)) < 1000


def test_result_types_pickle_and_name_the_package() -> None:
    checked = check_numbers(labelled(score=NAN, attributes={"n": -0.0}), "classification")
    assert pickle.loads(pickle.dumps(checked.converted)) == checked.converted
    assert type(pickle.loads(pickle.dumps(checked))) is CheckedRecord
    for kind in (CheckedRecord, DroppedNumber, ConvertedNumber, DroppedNumberWarning):
        assert kind.__module__ == "oatschema"
        assert kind.__name__ in oatschema.__all__


@pytest.mark.parametrize("version", available_versions())
def test_every_bundled_version_is_checked(version: str) -> None:
    record = labelled(score=NAN)
    checked = check_numbers(record, "classification", version)
    assert checked.record == labelled()
    assert [item.path for item in checked.dropped] == [("labels", 0, "score")]


def test_check_formats_is_passed_on() -> None:
    record = labelled(timestamp="not a time")
    with pytest.raises(ValidationError):
        check_numbers(record, "classification")
    assert check_numbers(record, "classification", check_formats=False).record == record


def test_arguments_are_checked() -> None:
    record = labelled()
    with pytest.raises(TypeError, match="takes the record first"):
        check_numbers("classification", cast(Any, record))
    with pytest.raises(TypeError, match="schema name must be a str"):
        check_numbers(record, cast(Any, 8))
    with pytest.raises(TypeError, match="version must be"):
        check_numbers(record, "classification", cast(Any, 8))
    with pytest.raises(TypeError, match="check_formats"):
        check_numbers(record, "classification", check_formats=cast(Any, 1))
    with pytest.raises(TypeError, match="on_bad_number"):
        check_numbers(record, "classification", on_bad_number=cast(Any, 1))
    with pytest.raises(ValueError, match="on_bad_number"):
        check_numbers(record, "classification", on_bad_number=cast(Any, "round"))
    with pytest.raises(ValueError, match="unknown schema"):
        check_numbers(record, cast(Any, "nope"))
    with pytest.raises(LookupError):
        check_numbers(record, "classification", "0.9")


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["id"])
def test_corpus_verdict_matches_the_javascript_build(case: dict[str, Any]) -> None:
    assert is_valid(json.loads(case["text"]), case["schema"]) is case["valid"]


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["id"])
def test_corpus_valid_record_keeps_every_number(case: dict[str, Any]) -> None:
    record = json.loads(case["text"])
    if case["valid"]:
        checked = check_numbers(record, case["schema"])
        assert checked.dropped == ()
        assert json.loads(json.dumps(checked.record)) == record


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["id"])
def test_corpus_result_is_valid_and_nothing_else_changed(case: dict[str, Any]) -> None:
    record = json.loads(case["text"])
    try:
        checked = check_numbers(record, case["schema"])
    except ValidationError:
        return
    assert is_valid(checked.record, case["schema"])
    assert case["valid"] or checked.dropped or checked.converted
    changed = {item.path for item in checked.dropped} | {item.path for item in checked.converted}
    for path, value in leaves(record):
        if path in changed:
            continue
        assert value_at(checked.record, path) == value


def leaves(node: Any, path: tuple[Any, ...] = ()) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, (*path, index))
    else:
        yield path, node


def value_at(node: Any, path: tuple[Any, ...]) -> Any:
    for part in path:
        node = node[part]
    return node


def test_walker_understands_every_applicator_in_the_bundled_schemas() -> None:
    understood = {"properties", "additionalProperties", "items", "prefixItems", "$ref", "allOf", "anyOf", "oneOf"}
    conditional = {"if", "then", "else", "not", "contains", "propertyNames"}
    unsupported = {"patternProperties", "dependentSchemas", "unevaluatedProperties", "unevaluatedItems", "$dynamicRef"}
    assert not (understood | conditional) & unsupported

    def walk(node: Any, at_properties: bool = False) -> None:
        if isinstance(node, dict):
            if not at_properties:
                assert not unsupported & set(node)
                assert str(node.get("$ref", "#/")).startswith("#/")
            for key, value in node.items():
                if key not in ("enum", "const", "examples"):
                    walk(value, key in ("properties", "$defs"))
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for version in available_versions():
        for path in (Path(oatschema.__file__).parent / "schemas" / version).glob("*.json"):
            if path.name != "MANIFEST.json":
                walk(get_schema(cast(Any, path.stem), version))


def vector(values: Any, version: str = "0.9.0") -> CheckedRecord:
    return check_numbers({"vector": values}, "embedding", version)


def test_number_list_with_no_change_is_copied() -> None:
    values = [0.5, -0.25, 1, 0.0, -1]
    checked = vector(values)
    assert checked == CheckedRecord({"vector": values}, (), ())
    assert checked.record["vector"] is not values
    assert [type(value) for value in checked.record["vector"]] == [float, float, int, float, int]


def test_negative_zero_in_a_number_list_is_converted_where_it_is() -> None:
    checked = vector([0.5, 0.0, -0.0, 1.0, -0.0])
    assert checked.converted == (
        ConvertedNumber(("vector", 2), -0.0, 0.0),
        ConvertedNumber(("vector", 4), -0.0, 0.0),
    )
    assert all(math.copysign(1, value) == 1 for value in checked.record["vector"])


@pytest.mark.parametrize("bad", [NAN, INF, -INF, 1e300, float(2**53), 2**53, 10**400])
def test_bad_number_in_a_number_list_rejects_at_its_index(bad: float) -> None:
    with pytest.raises(ValidationError) as caught:
        vector([0.5, 0.25, bad, 0.75])
    assert caught.value.path == ("vector", 2)


def test_infinities_that_cancel_in_a_sum_still_reject() -> None:
    with pytest.raises(ValidationError) as caught:
        vector([0.5, INF, -INF])
    assert caught.value.path == ("vector", 1)


def test_finite_numbers_whose_sum_overflows_are_checked_one_by_one() -> None:
    checked = vector([float(MAX), float(MAX)] * 2)
    assert checked.converted == ()
    with pytest.raises(ValidationError) as caught:
        vector([1e308, 1e308])
    assert caught.value.path == ("vector", 0)


def test_whole_floats_in_an_integer_list_become_ints_in_order() -> None:
    checked = vector({"dimensions": 8, "indices": [1.0, 2, 3.0], "values": [0.5, 0.5, 0.5]})
    assert checked.record["vector"]["indices"] == [1, 2, 3]
    assert [type(index) for index in checked.record["vector"]["indices"]] == [int, int, int]
    assert checked.converted == (
        ConvertedNumber(("vector", "indices", 0), 1.0, 1),
        ConvertedNumber(("vector", "indices", 2), 3.0, 3),
    )


def test_bool_in_a_number_list_is_not_a_number() -> None:
    with pytest.raises(ValidationError):
        vector([0.5, True])


def test_number_tuple_stays_a_tuple_and_list_subclass_becomes_a_list() -> None:
    class Numbers(list[float]):
        pass

    assert vector((0.5, 0.25)).record["vector"] == (0.5, 0.25)
    copied = vector(Numbers([0.5, 0.25])).record["vector"]
    assert type(copied) is list and copied == [0.5, 0.25]


def test_numpy_array_with_negative_zero_reports_the_value_then_the_array() -> None:
    array = np.array([-0.0, 0.5])
    checked = vector(array)
    assert checked.record["vector"] == [0.0, 0.5]
    assert [item.path for item in checked.converted] == [("vector", 0), ("vector",)]
    assert checked.converted[1].before is array


def test_list_of_like_objects_is_copied_object_by_object() -> None:
    labels = [{"label": f"l{index}", "score": index / 10} for index in range(10)]
    checked = check_numbers({"labels": labels}, "classification")
    assert checked == CheckedRecord({"labels": labels}, (), ())
    assert all(new is not old for new, old in zip(checked.record["labels"], labels, strict=True))


@pytest.mark.parametrize("bad", [NAN, -0.0, 1.5, 2**60])
def test_one_changed_value_in_a_list_of_like_objects_is_found(bad: float) -> None:
    labels = [{"label": "a", "score": 0.5} for _ in range(5)]
    labels[3] = {"label": "a", "score": bad}
    checked = check_numbers({"labels": labels}, "classification")
    changes = [*(item.path for item in checked.dropped), *(item.path for item in checked.converted)]
    assert changes == [("labels", 3, "score")]
    assert checked.record["labels"][:3] == labels[:3]


def test_list_of_objects_with_different_keys_is_checked() -> None:
    labels = [{"label": "a"}, {"label": "b", "score": -0.0}, {"score": 0.5, "label": "c"}]
    checked = check_numbers({"labels": labels}, "classification")
    assert checked.converted == (ConvertedNumber(("labels", 1, "score"), -0.0, 0.0),)
    assert list(checked.record["labels"][2]) == ["score", "label"]


def test_non_str_key_in_a_list_of_like_objects_is_refused() -> None:
    with pytest.raises(TypeError, match="JSON data"):
        check_numbers({"labels": [{"label": "a", 1: 0.5}, {"label": "b", 1: 0.5}]}, "classification")


def test_positional_items_use_each_position_s_bounds() -> None:
    def point(coordinates: Any) -> dict[str, Any]:
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coordinates}, "properties": {}}

    checked = check_numbers(point([-0.0, 51.5, 1e15]), "geolocation")
    assert checked.converted == (ConvertedNumber(("geometry", "coordinates", 0), -0.0, 0.0),)
    with pytest.raises(ValidationError) as caught:
        check_numbers(point([10.0, 91.0]), "geolocation", on_bad_number="raise")
    assert caught.value.path == ("geometry", "coordinates", 1)


def test_exclusive_bound_is_checked_exactly() -> None:
    record = {"blocks": [], "unit": "px", "page_width": 0}
    with pytest.raises(ValidationError):
        check_numbers(record, "document-extraction", on_bad_number="raise")
    assert check_numbers({**record, "page_width": 5e-324}, "document-extraction").dropped == ()


def test_bad_number_no_schema_describes_is_found_at_any_depth() -> None:
    nested: Any = [0.5, NAN]
    for _ in range(3000):
        nested = {"k": nested}
    with pytest.raises(ValidationError) as caught:
        check_numbers({"labels": [], "attributes": {"x": nested}}, "classification")
    assert caught.value.path == ("attributes", "x", *["k"] * 3000, 1)
    assert len(str(caught.value)) < 1500


def test_values_no_schema_describes_are_copied_and_converted_in_order() -> None:
    from oatschema._numbers import _planner, _resolve, _Walk

    shared = {"z": -0.0}
    value = {"n": 2.0, "t": (1.0, -0.0, (-0.0,)), "a": np.array([0.5, -0.0]), "s": [shared, shared], "e": ()}
    walk = _Walk()
    checked = walk.visit(value, _planner("classification", "0.9.0").top.child("extra"), ("x",), None, None)
    assert checked == {"n": 2.0, "t": (1.0, 0.0, (0.0,)), "a": [0.5, 0.0], "s": [{"z": 0.0}, {"z": 0.0}], "e": ()}
    assert type(checked["t"]) is tuple and type(checked["t"][2]) is tuple
    assert checked["s"][0] is not checked["s"][1] and checked["s"][0] is not shared
    assert [_resolve(item.path) for item in walk.converted] == [
        ("x", "t", 1),
        ("x", "t", 2, 0),
        ("x", "a", 1),
        ("x", "a"),
        ("x", "s", 0, "z"),
        ("x", "s", 1, "z"),
    ]


def test_repeated_object_in_a_list_is_copied_each_time() -> None:
    label = {"label": "a", "score": 0.5}
    checked = check_numbers({"labels": [label, label]}, "classification")
    first, second = checked.record["labels"]
    assert first is not second and first is not label


def wide(**changes: Any) -> dict[str, Any]:
    return {"data": {**{f"k{index}": index / 8 for index in range(100)}, **changes}}


def test_wide_object_with_no_change_is_copied() -> None:
    record = wide(text="a", flag=True, empty=None)
    checked = check_numbers(record, "generic")
    assert checked == CheckedRecord(record, (), ())
    assert checked.record["data"] is not record["data"]


@pytest.mark.parametrize(
    ("value", "dropped", "converted"), [(NAN, True, False), (-0.0, False, True), (2**60, True, False)]
)
def test_one_changed_value_in_a_wide_object_is_found(value: float, dropped: bool, converted: bool) -> None:
    checked = check_numbers(wide(k50=value), "generic")
    assert [item.path for item in checked.dropped] == ([("data", "k50")] if dropped else [])
    assert [item.path for item in checked.converted] == ([("data", "k50")] if converted else [])
    assert list(checked.record["data"])[:3] == ["k0", "k1", "k2"]


def test_non_str_key_in_a_wide_object_is_refused() -> None:
    record = wide()
    record["data"][7] = 0.5
    with pytest.raises(TypeError, match="JSON data"):
        check_numbers(record, "generic")


def test_bounds_the_walk_does_not_follow_are_found() -> None:
    from oatschema._numbers import _Planner

    def hidden(schema: dict[str, Any]) -> bool:
        return _Planner(schema).hidden_bounds

    number = {"type": "number", "maximum": 1}
    assert not hidden({"properties": {"x": number}, "$defs": {"n": number}})
    assert not hidden({"properties": {"x": {"anyOf": [number, {"type": "string"}]}}})
    assert hidden({"properties": {"x": {"maximum": 1}}})
    assert hidden({"properties": {"x": {"type": "number"}}, "if": {}, "then": {"properties": {"x": number}}})
    assert hidden({"$defs": {"n": number}, "if": {}, "then": {"properties": {"x": {"$ref": "#/$defs/n"}}}})
    assert hidden({"properties": {"x": {"not": number}}})
    names = {"document-extraction", "entity-extraction", "object-detection"}
    for version in available_versions():
        for path in (Path(oatschema.__file__).parent / "schemas" / version).glob("*.json"):
            if path.name != "MANIFEST.json":
                assert hidden(get_schema(cast(Any, path.stem), version)) is (path.stem in names)


@pytest.mark.parametrize("version", ["0.6.0", "0.8.0", "0.9.0"])
def test_bounds_only_pass_finds_what_every_error_finds(version: str) -> None:
    from oatschema._numbers import _BOUND_KEYWORDS, _bound_paths

    record: dict[str, Any] = {
        "unit": "normalized",
        "page_width": 2,
        "blocks": [
            {"block_type": "box", "box": {"x": 1.5, "y": 0.5, "width": 0.2, "height": 3}},
            {"block_type": "polygon", "polygon": [{"x": 2, "y": 0.5}, {"x": 0.1, "y": 0.1}, {"x": 0.2, "y": -9}]},
            {"block_type": "text", "text": "a", "score": 2, "page_number": 0},
            {"block_type": "text"},
        ],
    }
    validator = oatschema.get_validator("document-extraction", version)
    every = {error.path for error in validator.iter_errors(record) if error.keyword in _BOUND_KEYWORDS}
    assert len(every) >= 4
    assert _bound_paths(record, validator) == every
    assert _bound_paths({**record, "blocks": [*record["blocks"], {"text": "\ud800"}]}, validator) == set()


def test_numbers_with_several_numeric_schemas_are_rechecked() -> None:
    from oatschema._numbers import _Planner, _Walk

    planner = _Planner({"properties": {"x": {"allOf": [{"type": "number", "maximum": 5}, {"type": "integer"}]}}})
    walk = _Walk()
    walk.visit({"x": 3}, planner.top, (), None, None)
    assert walk.merged_bounds
    walk = _Walk()
    walk.visit({"x": 3}, _Planner({"properties": {"x": {"type": "number"}}}).top, (), None, None)
    assert not walk.merged_bounds


def test_conditions_that_refer_elsewhere_use_every_error() -> None:
    from oatschema._numbers import _bounds_schema, _conditions_refer

    assert _conditions_refer({"allOf": [{"if": {"properties": {"u": {"$ref": "#/$defs/u"}}}, "then": {}}]})
    assert not _conditions_refer({"$ref": "#/$defs/u", "if": {"properties": {"u": {"const": 1}}}})
    assert _bounds_schema(
        {
            "type": "object",
            "required": ["x"],
            "additionalProperties": False,
            "properties": {"x": {"type": "number", "maximum": 5, "anyOf": [{"minimum": 0}]}},
        }
    ) == {"additionalProperties": True, "properties": {"x": {"maximum": 5}}}
