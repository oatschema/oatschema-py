# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import math
import sys
import warnings
from functools import lru_cache
from typing import Any, Literal, NamedTuple

from oatschema._schemas import SchemaName, check_arguments, get_schema
from oatschema._validation import (
    ValidationError,
    Validator,
    check_formats_argument,
    format_path,
    lookup,
    shorten,
)

MAX_NUMBER = 9007199254740991

_CALL = "check_numbers(record, name, version=None, *, on_bad_number='drop', check_formats=True)"
_LARGE_INTEGER_PATTERN = "^(0|-?[1-9][0-9]*)$"
_BOUND_KEYWORDS = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"})
_NOT_FINITE = "not a finite number"
_OUT_OF_RANGE = f"outside ±{MAX_NUMBER}"
_OUT_OF_BOUNDS = "outside the bounds of this field"
_NOT_JSON = "check_numbers found a value with no exact JSON form"
_REPORTED_DROPS = 5
_REPORTED_VALUE_LIMIT = 100
_REPORTED_INTEGER_BITS = 64
_MAX_LARGE_INTEGER_BITS = 512

_Path = tuple[str | int, ...]


class DroppedNumber(NamedTuple):
    """A number ``check_numbers`` removed from a record, with its property."""

    path: _Path
    value: Any
    reason: str


class ConvertedNumber(NamedTuple):
    """A value ``check_numbers`` rewrote without changing what it means."""

    path: _Path
    before: Any
    after: Any


class CheckedRecord(NamedTuple):
    """The result of ``check_numbers``: a new record, and everything that differs from the original."""

    record: Any
    dropped: tuple[DroppedNumber, ...]
    converted: tuple[ConvertedNumber, ...]


class DroppedNumberWarning(UserWarning):
    """``check_numbers`` removed at least one number from a record."""

    __module__ = "oatschema"


DroppedNumber.__module__ = "oatschema"
ConvertedNumber.__module__ = "oatschema"
CheckedRecord.__module__ = "oatschema"


class _Bad(NamedTuple):
    path: _Path
    value: Any
    reason: str
    parent: dict[Any, Any] | None
    key: Any


class _Walk:
    def __init__(self, root: dict[str, Any]) -> None:
        self.root = root
        self.converted: list[ConvertedNumber] = []
        self.out_of_range: list[_Bad] = []
        self.out_of_bounds: list[_Bad] = []

    def expand(self, schemas: list[Any]) -> list[dict[str, Any]]:
        expanded: list[dict[str, Any]] = []
        seen: set[int] = set()
        stack = list(reversed(schemas))
        while stack:
            schema = stack.pop()
            if not isinstance(schema, dict) or id(schema) in seen:
                continue
            seen.add(id(schema))
            expanded.append(schema)
            reference = schema.get("$ref")
            if reference is not None:
                stack.append(self.resolve(reference))
            for keyword in ("allOf", "anyOf", "oneOf"):
                stack.extend(reversed(schema.get(keyword, ())))
        return expanded

    def resolve(self, reference: str) -> Any:
        target: Any = self.root
        for part in reference.removeprefix("#/").split("/"):
            target = target[part]
        return target

    def visit(
        self, value: Any, schemas: list[dict[str, Any]], path: _Path, parent: dict[Any, Any] | None, key: Any
    ) -> Any:
        plain = _plain(value)
        if isinstance(plain, dict):
            checked: Any = self.visit_object(plain, schemas, path)
        elif isinstance(plain, (list, tuple)):
            checked = self.visit_array(plain, schemas, path)
        elif isinstance(plain, (int, float)) and not isinstance(plain, bool):
            checked = self.visit_number(plain, value, schemas, path, parent, key)
        elif plain is None or isinstance(plain, (str, bool)):
            checked = plain
        else:
            raise TypeError(f"the record must be JSON data (dict, list, str, int, float, bool or None): {_NOT_JSON}")
        if plain is not value or (checked is not plain and isinstance(plain, (int, float))):
            self.converted.append(ConvertedNumber(path, value, checked))
        return checked

    def visit_object(self, value: dict[Any, Any], schemas: list[dict[str, Any]], path: _Path) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"the record must be JSON data: a property name is not a str: {_NOT_JSON}")
            children: list[Any] = []
            for schema in schemas:
                properties = schema.get("properties", {})
                if key in properties:
                    children.append(properties[key])
                elif "additionalProperties" in schema:
                    children.append(schema["additionalProperties"])
            result[key] = self.visit(item, self.expand(children), (*path, key), result, key)
        return result

    def visit_array(
        self, value: list[Any] | tuple[Any, ...], schemas: list[dict[str, Any]], path: _Path
    ) -> list[Any] | tuple[Any, ...]:
        positional = any("prefixItems" in schema for schema in schemas)
        items = self.expand([schema["items"] for schema in schemas if "items" in schema])
        result = []
        for index, item in enumerate(value):
            children = self.expand([_item_schema(schema, index) for schema in schemas]) if positional else items
            result.append(self.visit(item, children, (*path, index), None, None))
        return tuple(result) if isinstance(value, tuple) else result

    def visit_number(
        self,
        value: int | float,
        original: Any,
        schemas: list[dict[str, Any]],
        path: _Path,
        parent: dict[Any, Any] | None,
        key: Any,
    ) -> Any:
        if isinstance(value, int) and any(_is_large_integer(schema) for schema in schemas):
            if value.bit_length() <= _MAX_LARGE_INTEGER_BITS and len(text := str(int(value))) <= _max_length(schemas):
                return text
            self.out_of_range.append(_Bad(path, original, _OUT_OF_RANGE, parent, key))
            return value
        if isinstance(value, float) and not math.isfinite(value):
            self.out_of_range.append(_Bad(path, original, _NOT_FINITE, parent, key))
            return value
        if abs(value) > MAX_NUMBER:
            self.out_of_range.append(_Bad(path, original, _OUT_OF_RANGE, parent, key))
            return value
        numeric = [schema for schema in schemas if _is_numeric(schema)]
        if numeric and not any(_within_bounds(value, schema) for schema in numeric):
            self.out_of_bounds.append(_Bad(path, original, _OUT_OF_BOUNDS, parent, key))
        if isinstance(value, float):
            if value.is_integer() and any(_is_integer(schema) for schema in numeric):
                return int(value)
            if value == 0.0 and math.copysign(1.0, value) < 0:
                return 0.0
        return value


def _item_schema(schema: dict[str, Any], index: int) -> Any:
    prefix = schema.get("prefixItems", ())
    return prefix[index] if index < len(prefix) else schema.get("items")


def _types(schema: dict[str, Any]) -> tuple[Any, ...]:
    kinds = schema.get("type", ())
    return tuple(kinds) if isinstance(kinds, list) else (kinds,)


def _is_numeric(schema: dict[str, Any]) -> bool:
    return "number" in _types(schema) or "integer" in _types(schema)


def _is_integer(schema: dict[str, Any]) -> bool:
    return "integer" in _types(schema)


def _is_large_integer(schema: dict[str, Any]) -> bool:
    return "string" in _types(schema) and schema.get("pattern") == _LARGE_INTEGER_PATTERN


def _max_length(schemas: list[dict[str, Any]]) -> int:
    return min(int(schema.get("maxLength", sys.maxsize)) for schema in schemas if _is_large_integer(schema))


def _within_bounds(value: int | float, schema: dict[str, Any]) -> bool:
    return not (
        ("minimum" in schema and value < schema["minimum"])
        or ("maximum" in schema and value > schema["maximum"])
        or ("exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"])
        or ("exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"])
    )


def _plain(value: Any) -> Any:
    numpy = sys.modules.get("numpy")
    if numpy is None or not isinstance(value, (numpy.generic, numpy.ndarray)):
        return value
    if isinstance(value, numpy.ndarray):
        if isinstance(value, numpy.ma.MaskedArray) or value.dtype.kind not in "biufO":
            return value
        return value.tolist()
    if isinstance(value, (numpy.datetime64, numpy.timedelta64)):
        return value
    if isinstance(value, (numpy.bool_, numpy.integer, numpy.floating, numpy.str_)):
        item = value.item()
        if type(item) in (bool, int, float, str):
            return item
    return value


def _describe(value: Any) -> str:
    if isinstance(value, int) and value.bit_length() > _REPORTED_INTEGER_BITS:
        return f"an integer of {value.bit_length()} bits"
    try:
        return shorten(repr(value), _REPORTED_VALUE_LIMIT)
    except ValueError:
        return f"a value of type {type(value).__name__}"


def _warn(dropped: list[DroppedNumber], name: str, version: str) -> None:
    shown = ", ".join(f"/{format_path(item.path)} ({_describe(item.value)})" for item in dropped[:_REPORTED_DROPS])
    more = f" and {len(dropped) - _REPORTED_DROPS} more" if len(dropped) > _REPORTED_DROPS else ""
    warnings.warn(
        f"check_numbers dropped {len(dropped)} from a {name} {version} record: {shown}{more}",
        DroppedNumberWarning,
        stacklevel=3,
    )


def _rejected(validator: Validator, path: _Path, reason: str) -> ValidationError:
    return ValidationError(
        f"… is {reason}", schema_name=validator.schema_name, schema_version=validator.schema_version, path=path
    )


def _value_at(record: Any, path: _Path) -> tuple[Any, Any]:
    parent: Any = None
    target = record
    for part in path:
        parent = target
        target = target[part]
    return parent, target


@lru_cache(maxsize=None)
def _schema(name: SchemaName, version: str) -> dict[str, Any]:
    return get_schema(name, version)


def _drop(bad: _Bad, on_bad_number: str, validator: Validator, dropped: list[DroppedNumber]) -> None:
    if on_bad_number == "raise" or bad.parent is None:
        raise _rejected(validator, bad.path, bad.reason)
    del bad.parent[bad.key]
    dropped.append(DroppedNumber(bad.path, bad.value, bad.reason))


def _drop_overlay_bounds(record: Any, validator: Validator, on_bad_number: str, dropped: list[DroppedNumber]) -> None:
    paths = {error.path for error in validator.iter_errors(record) if error.keyword in _BOUND_KEYWORDS}
    for path in sorted(paths, key=len, reverse=True):
        parent, value = _value_at(record, path)
        holder = parent if isinstance(parent, dict) else None
        _drop(_Bad(path, value, _OUT_OF_BOUNDS, holder, path[-1] if path else None), on_bad_number, validator, dropped)


def check_numbers(
    record: Any,
    name: SchemaName,
    version: str | None = None,
    *,
    on_bad_number: Literal["drop", "raise"] = "drop",
    check_formats: bool = True,
) -> CheckedRecord:
    """Return a copy of ``record`` whose numbers are safe to write, validated against the schema ``name``.

    Values are never rounded, and ``record`` is not modified. Lossless conversions are made and listed in
    ``converted``: numpy values to Python ones, ``-0.0`` to ``0.0``, a whole float in an ``integer`` field to an
    int, and, from schema version 0.9.0, an int ``seed`` to its string form.

    A bad number is NaN, ±infinity, beyond ±9007199254740991 in any schema version, or outside its field's bounds.
    With ``on_bad_number="drop"``, a bad number under a property is removed, listed in ``dropped``, and reported
    with a ``DroppedNumberWarning``. In a list, or at the top level, it rejects the record, because removing it
    would change what the list says. With ``on_bad_number="raise"``, any bad number rejects the record.

    Requires the ``validate`` extra.

    Raises:
        ValidationError: the record is rejected, or isn't valid after the changes above.
        TypeError: an argument has the wrong type, or ``record`` is not JSON data, such as a ``Decimal``, an ``Enum``
            that is not also an int or str, or a numpy ``datetime64``.
        ValueError: ``on_bad_number`` is not ``"drop"`` or ``"raise"``, or ``name`` is not a schema.
        LookupError: ``version`` is not bundled, or does not contain ``name``.
    """
    if isinstance(record, str) and not isinstance(name, str):
        raise TypeError(f"check_numbers() takes the record first, then the schema name; expected {_CALL}")
    check_arguments(_CALL, name, version)
    check_formats_argument(check_formats, _CALL)
    if not isinstance(on_bad_number, str):
        raise TypeError(
            f"on_bad_number must be 'drop' or 'raise', not {type(on_bad_number).__name__}; expected {_CALL}"
        )
    if on_bad_number not in ("drop", "raise"):
        raise ValueError(f"on_bad_number must be 'drop' or 'raise', not {shorten(repr(on_bad_number), 100)}")
    validator = lookup(name, version, check_formats)
    schema = _schema(name, validator.schema_version)
    walk = _Walk(schema)
    try:
        checked = walk.visit(record, walk.expand([schema]), (), None, None)
    except RecursionError:
        raise TypeError("the record must be JSON data: it is nested too deeply") from None
    dropped: list[DroppedNumber] = []
    for bad in walk.out_of_range:
        _drop(bad, on_bad_number, validator, dropped)
    if not validator.is_valid(checked):
        for bad in walk.out_of_bounds:
            _drop(bad, on_bad_number, validator, dropped)
        _drop_overlay_bounds(checked, validator, on_bad_number, dropped)
    try:
        validator.validate(checked)
    except ValidationError as error:
        if not dropped:
            raise
        names = ", ".join(f"/{format_path(item.path)}" for item in dropped[:_REPORTED_DROPS])
        failure = ValidationError(
            shorten(f"{error.message}, after dropping bad numbers at {names}", 1000),
            schema_name=error.schema_name,
            schema_version=error.schema_version,
            path=error.path,
            schema_path=error.schema_path,
        )
    else:
        if dropped:
            _warn(dropped, name, validator.schema_version)
        gone = {item.path for item in dropped}
        converted = tuple(item for item in walk.converted if item.path not in gone)
        return CheckedRecord(checked, tuple(dropped), converted)
    raise failure
