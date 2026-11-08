# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import math
import sys
import warnings
from array import array
from functools import lru_cache
from operator import itemgetter
from collections.abc import Iterator
from typing import Any, Literal, NamedTuple

from oatschema._schemas import SchemaName, check_arguments, get_schema
from oatschema._validation import (
    ValidationError,
    Validator,
    check_formats_argument,
    compile_schema,
    format_path,
    has_surrogate,
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
_NOT_STR_KEY = f"the record must be JSON data: a property name is not a str: {_NOT_JSON}"
_EXCLUSIVE = frozenset({"exclusiveMinimum", "exclusiveMaximum"})
_TEXT_TYPES = frozenset({str, bool, type(None)})
_NUMBER_TYPES = frozenset({int, float})
_NEGATIVE_ZERO = -(2**63)
_WIDE = 16
_FOLLOWED_MAPS = frozenset({"properties", "$defs"})
_FOLLOWED = frozenset({"additionalProperties", "items", "prefixItems", "allOf", "anyOf", "oneOf"})
_CONDITIONAL = frozenset({"if", "then", "else", "not", "contains", "propertyNames"})
_REPORTED_VALUE_LIMIT = 100
_REPORTED_INTEGER_BITS = 64
_MAX_LARGE_INTEGER_BITS = 512

_Path = tuple[str | int, ...]
_OWN = object()


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


class _Where:
    __slots__ = ("frame", "slot")

    def __init__(self, frame: "_Frame", slot: Any = _OWN) -> None:
        self.frame = frame
        self.slot = slot

    def resolve(self) -> _Path:
        base = self.frame.path()
        return base if self.slot is _OWN else (*base, self.slot)


_Location = _Path | _Where


def _resolve(location: _Location) -> _Path:
    return location.resolve() if isinstance(location, _Where) else location


class _Change(NamedTuple):
    path: _Location
    before: Any
    after: Any


class _Bad(NamedTuple):
    path: _Location
    value: Any
    reason: str
    parent: dict[Any, Any] | None
    key: Any


class _Plan:
    __slots__ = (
        "empty",
        "extra",
        "fast_float",
        "fast_int",
        "high",
        "integer",
        "items",
        "known",
        "large",
        "low",
        "max_length",
        "numeric",
        "planner",
        "positional",
        "prefix",
        "prefix_length",
        "properties",
        "schemas",
    )

    def __init__(self, planner: "_Planner", schemas: list[dict[str, Any]]) -> None:
        self.planner = planner
        self.schemas = schemas
        self.empty = not schemas
        self.properties = frozenset(key for schema in schemas for key in schema.get("properties", {}))
        self.known: dict[str, _Plan] = {}
        self.extra: _Plan | None = None
        self.prefix_length = max((len(schema.get("prefixItems", ())) for schema in schemas), default=0)
        self.positional = any("prefixItems" in schema for schema in schemas)
        self.prefix: dict[int, _Plan] = {}
        self.items: _Plan | None = None
        self.large = any(_is_large_integer(schema) for schema in schemas)
        self.max_length = _max_length(schemas) if self.large else sys.maxsize
        self.numeric = [schema for schema in schemas if _is_numeric(schema)]
        self.integer = any(_is_integer(schema) for schema in self.numeric)
        self.low: float = max([-MAX_NUMBER, *(schema["minimum"] for schema in self.numeric if "minimum" in schema)])
        self.high: float = min([MAX_NUMBER, *(schema["maximum"] for schema in self.numeric if "maximum" in schema)])
        simple = (
            not self.large and len(self.numeric) <= 1 and not any(_EXCLUSIVE & schema.keys() for schema in self.numeric)
        )
        self.fast_int = simple
        self.fast_float = simple and not self.integer

    def child(self, key: str) -> "_Plan":
        plan = self.known.get(key)
        if plan is not None:
            return plan
        if key in self.properties:
            children: list[Any] = []
            for schema in self.schemas:
                properties = schema.get("properties", {})
                if key in properties:
                    children.append(properties[key])
                elif "additionalProperties" in schema:
                    children.append(schema["additionalProperties"])
            plan = self.known[key] = self.planner.plan(children)
            return plan
        return self.extra_plan()

    def extra_plan(self) -> "_Plan":
        if self.extra is None:
            extra = [schema["additionalProperties"] for schema in self.schemas if "additionalProperties" in schema]
            self.extra = self.planner.plan(extra)
        return self.extra

    def item(self, index: int) -> "_Plan":
        if index < self.prefix_length:
            plan = self.prefix.get(index)
            if plan is None:
                plan = self.prefix[index] = self.planner.plan([_item_schema(schema, index) for schema in self.schemas])
            return plan
        if self.items is None:
            self.items = self.planner.plan([schema["items"] for schema in self.schemas if "items" in schema])
        return self.items

    def keeps(self, value: Any) -> bool:
        kind = type(value)
        if kind is str or kind is bool or value is None:
            return True
        if kind is float:
            return self.fast_float and self.low <= value <= self.high and value != 0
        return kind is int and self.fast_int and self.low <= value <= self.high

    def keeps_all(self, values: list[Any] | tuple[Any, ...]) -> bool:
        kinds = set(map(type, values))
        if kinds <= _TEXT_TYPES:
            return True
        if not kinds <= _NUMBER_TYPES or (int in kinds and not self.fast_int):
            return False
        if float in kinds:
            if not self.fast_float:
                return False
            try:
                if not math.isfinite(sum(values)):
                    return False
                if 0 in values and _NEGATIVE_ZERO in memoryview(array("d", values)).cast("B").cast("q"):
                    return False
            except OverflowError:
                return False
        return bool(self.low <= min(values) and max(values) <= self.high)

    def keeps_objects(self, values: list[Any] | tuple[Any, ...]) -> bool:
        if self.empty or set(map(type, values)) != {dict}:
            return False
        shapes = set(map(tuple, values))
        if len(shapes) != 1:
            return False
        keys = shapes.pop()
        if not all(type(key) is str for key in keys):
            return False
        return all(self.child(key).keeps_all(list(map(itemgetter(key), values))) for key in keys)


class _Planner:
    def __init__(self, root: dict[str, Any]) -> None:
        self.root = root
        self.plans: dict[tuple[int, ...], _Plan] = {}
        self.top = self.plan([root])
        self.hidden_bounds = any(self.hidden_bounds_in(root, hidden=False))

    def plan(self, schemas: list[Any]) -> _Plan:
        expanded = self.expand(schemas)
        key = tuple(map(id, expanded))
        plan = self.plans.get(key)
        if plan is None:
            plan = self.plans[key] = _Plan(self, expanded)
        return plan

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

    def hidden_bounds_in(self, schema: Any, *, hidden: bool) -> Iterator[bool]:
        if isinstance(schema, list):
            for item in schema:
                yield from self.hidden_bounds_in(item, hidden=hidden)
        elif isinstance(schema, dict):
            if _BOUND_KEYWORDS & schema.keys() and (hidden or not _is_numeric(schema)):
                yield True
            if hidden and "$ref" in schema:
                yield from self.hidden_bounds_in(self.resolve(schema["$ref"]), hidden=True)
            for keyword, value in schema.items():
                if keyword in _FOLLOWED_MAPS:
                    for item in value.values():
                        yield from self.hidden_bounds_in(item, hidden=hidden)
                elif keyword in _FOLLOWED:
                    yield from self.hidden_bounds_in(value, hidden=hidden)
                elif keyword in _CONDITIONAL:
                    yield from self.hidden_bounds_in(value, hidden=True)

    def resolve(self, reference: str) -> Any:
        target: Any = self.root
        for part in reference.removeprefix("#/").split("/"):
            target = target[part]
        return target


class _Frame:
    __slots__ = ("base", "entries", "holder", "original", "parent", "plain", "result", "slot")

    def __init__(self, original: Any, plain: Any, parent: "_Frame | None", slot: Any, base: _Path = ()) -> None:
        self.original = original
        self.plain = plain
        self.parent = parent
        self.slot = slot
        self.base = base
        self.holder: Any = None if parent is None else parent.result
        self.result: Any = {} if isinstance(plain, dict) else []
        self.entries = iter(plain.items()) if isinstance(plain, dict) else enumerate(plain)

    def path(self) -> _Path:
        slots = []
        frame: _Frame | None = self
        while frame is not None and frame.parent is not None:
            slots.append(frame.slot)
            frame = frame.parent
        return (*(frame.base if frame is not None else ()), *reversed(slots))

    def place(self, slot: Any, value: Any) -> None:
        if isinstance(self.result, dict):
            self.result[slot] = value
        else:
            self.result.append(value)

    def finish(self) -> Any:
        return tuple(self.result) if isinstance(self.plain, tuple) else self.result


class _Walk:
    def __init__(self) -> None:
        self.converted: list[_Change] = []
        self.out_of_range: list[_Bad] = []
        self.out_of_bounds: list[_Bad] = []
        self.merged_bounds = False

    def visit(self, value: Any, plan: _Plan, path: _Location, parent: dict[Any, Any] | None, key: Any) -> Any:
        plain = _plain(value)
        if isinstance(plain, (dict, list, tuple)) and plan.empty:
            checked: Any = self.visit_free(plain, plan, _resolve(path))
        elif isinstance(plain, dict):
            checked = self.visit_object(plain, plan, _resolve(path))
        elif isinstance(plain, (list, tuple)):
            checked = self.visit_array(plain, plan, _resolve(path))
        elif isinstance(plain, (int, float)) and not isinstance(plain, bool):
            checked = self.visit_number(plain, value, plan, path, parent, key)
        elif plain is None or isinstance(plain, (str, bool)):
            checked = plain
        else:
            raise TypeError(f"the record must be JSON data (dict, list, str, int, float, bool or None): {_NOT_JSON}")
        if plain is not value or (checked is not plain and isinstance(plain, (int, float))):
            self.converted.append(_Change(path, value, checked))
        return checked

    def visit_object(self, value: dict[Any, Any], plan: _Plan, path: _Path) -> dict[Any, Any]:
        if (
            len(value) > _WIDE
            and plan.properties.isdisjoint(value)
            and set(map(type, value)) == {str}
            and plan.extra_plan().keeps_all(list(value.values()))
        ):
            return dict(value)
        result: dict[Any, Any] = {}
        known = plan.known
        for key, item in value.items():
            if type(key) is not str and not isinstance(key, str):
                raise TypeError(_NOT_STR_KEY)
            child = known.get(key) or plan.child(key)
            kind = type(item)
            if kind is float:
                if child.fast_float and child.low <= item <= child.high and item:
                    result[key] = item
                    continue
            elif kind is str or kind is bool or item is None:
                result[key] = item
                continue
            elif kind is int:
                if child.fast_int and child.low <= item <= child.high:
                    result[key] = item
                    continue
            elif kind is dict and not child.empty:
                result[key] = self.visit_object(item, child, (*path, key))
                continue
            result[key] = self.visit(item, child, (*path, key), result, key)
        return result

    def visit_array(self, value: list[Any] | tuple[Any, ...], plan: _Plan, path: _Path) -> list[Any] | tuple[Any, ...]:
        child = plan.item(0)
        if not plan.positional:
            if child.keeps_all(value):
                return tuple(value) if isinstance(value, tuple) else list(value)
            if child.keeps_objects(value):
                copied = list(map(dict, value))
                return tuple(copied) if isinstance(value, tuple) else copied
        result: list[Any] = []
        append = result.append
        for index, item in enumerate(value):
            if index <= plan.prefix_length:
                child = plan.item(index)
            if type(item) is dict and not child.empty:
                append(self.visit_object(item, child, (*path, index)))
            elif child.keeps(item):
                append(item)
            else:
                append(self.visit(item, child, (*path, index), None, None))
        return tuple(result) if isinstance(value, tuple) else result

    def visit_free(self, value: dict[Any, Any] | list[Any] | tuple[Any, ...], plan: _Plan, path: _Path) -> Any:
        frames = [_Frame(value, value, None, None, path)]
        open_containers = {id(value)}
        while True:
            frame = frames[-1]
            for slot, item in frame.entries:
                if isinstance(frame.result, dict) and not isinstance(slot, str):
                    raise TypeError(_NOT_STR_KEY)
                if plan.keeps(item):
                    frame.place(slot, item)
                    continue
                plain = _plain(item)
                if not isinstance(plain, (dict, list, tuple)):
                    parent = frame.result if isinstance(frame.result, dict) else None
                    frame.place(slot, self.visit(item, plan, _Where(frame, slot), parent, slot))
                    continue
                if not isinstance(plain, dict) and plan.keeps_all(plain):
                    copied = tuple(plain) if isinstance(plain, tuple) else list(plain)
                    if plain is not item:
                        self.converted.append(_Change(_Where(frame, slot), item, copied))
                    frame.place(slot, copied)
                    continue
                if id(plain) in open_containers:
                    raise TypeError("the record must be JSON data: it contains itself")
                child = _Frame(item, plain, frame, slot)
                frame.place(slot, child.result)
                frames.append(child)
                open_containers.add(id(plain))
                break
            else:
                frames.pop()
                open_containers.discard(id(frame.plain))
                finished = frame.finish()
                if frame.holder is not None and finished is not frame.result:
                    frame.holder[frame.slot] = finished
                if frame.plain is not frame.original:
                    self.converted.append(_Change(_Where(frame), frame.original, finished))
                if not frames:
                    return finished

    def visit_number(
        self,
        value: int | float,
        original: Any,
        plan: _Plan,
        path: _Location,
        parent: dict[Any, Any] | None,
        key: Any,
    ) -> Any:
        if isinstance(value, int) and plan.large:
            if value.bit_length() <= _MAX_LARGE_INTEGER_BITS and len(text := str(int(value))) <= plan.max_length:
                return text
            self.out_of_range.append(_Bad(path, original, _OUT_OF_RANGE, parent, key))
            return value
        if isinstance(value, float) and not math.isfinite(value):
            self.out_of_range.append(_Bad(path, original, _NOT_FINITE, parent, key))
            return value
        if abs(value) > MAX_NUMBER:
            self.out_of_range.append(_Bad(path, original, _OUT_OF_RANGE, parent, key))
            return value
        if len(plan.numeric) > 1:
            self.merged_bounds = True
        if plan.numeric and not any(_within_bounds(value, schema) for schema in plan.numeric):
            self.out_of_bounds.append(_Bad(path, original, _OUT_OF_BOUNDS, parent, key))
        if isinstance(value, float):
            if value.is_integer() and plan.integer:
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


def _warn(dropped: tuple[DroppedNumber, ...], name: str, version: str) -> None:
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
def _planner(name: SchemaName, version: str) -> _Planner:
    return _Planner(get_schema(name, version))


def _drop(bad: _Bad, on_bad_number: str, validator: Validator, dropped: list[_Bad]) -> None:
    if on_bad_number == "raise" or bad.parent is None:
        raise _rejected(validator, _resolve(bad.path), bad.reason)
    del bad.parent[bad.key]
    dropped.append(bad)


def _bounds_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return True
    kept: dict[str, Any] = {}
    for keyword, value in schema.items():
        if keyword in _BOUND_KEYWORDS or keyword in ("$schema", "$id", "$ref", "if"):
            kept[keyword] = value
        elif keyword in _FOLLOWED_MAPS:
            kept[keyword] = {name: _bounds_schema(item) for name, item in value.items()}
        elif keyword in ("prefixItems", "allOf"):
            kept[keyword] = [_bounds_schema(item) for item in value]
        elif keyword in ("additionalProperties", "items", "then", "else"):
            kept[keyword] = _bounds_schema(value)
    return kept


def _refers(schema: Any) -> bool:
    if isinstance(schema, dict):
        return "$ref" in schema or any(map(_refers, schema.values()))
    return isinstance(schema, list) and any(map(_refers, schema))


def _conditions_refer(schema: Any) -> bool:
    if isinstance(schema, dict):
        return _refers(schema.get("if")) or any(map(_conditions_refer, schema.values()))
    return isinstance(schema, list) and any(map(_conditions_refer, schema))


@lru_cache(maxsize=None)
def _bounds_validator(name: SchemaName, version: str, check_formats: bool) -> Any:
    schema = get_schema(name, version)
    return None if _conditions_refer(schema) else compile_schema(_bounds_schema(schema), check_formats=check_formats)


def _keyword(schema_path: Any) -> Any:
    return next((part for part in reversed(schema_path) if isinstance(part, str)), "")


def _bound_paths(record: Any, validator: Validator) -> set[_Path]:
    bounds = _bounds_validator(validator.schema_name, validator.schema_version, validator.check_formats)
    if bounds is not None and not has_surrogate(record):
        try:
            errors = bounds.iter_errors(record)
        except ValueError:
            pass
        else:
            return {tuple(error.instance_path) for error in errors if _keyword(error.schema_path) in _BOUND_KEYWORDS}
    return {error.path for error in validator.iter_errors(record) if error.keyword in _BOUND_KEYWORDS}


def _drop_overlay_bounds(record: Any, validator: Validator, on_bad_number: str, dropped: list[_Bad]) -> None:
    for path in sorted(_bound_paths(record, validator), key=len, reverse=True):
        parent, value = _value_at(record, path)
        holder = parent if isinstance(parent, dict) else None
        _drop(_Bad(path, value, _OUT_OF_BOUNDS, holder, path[-1] if path else None), on_bad_number, validator, dropped)


def _failure(checked: Any, validator: Validator, dropped: list[_Bad]) -> ValidationError | None:
    try:
        validator.validate(checked)
    except ValidationError as error:
        if not dropped:
            return error
        names = ", ".join(f"/{format_path(_resolve(item.path))}" for item in dropped[:_REPORTED_DROPS])
        return ValidationError(
            shorten(f"{error.message}, after dropping bad numbers at {names}", 1000),
            schema_name=error.schema_name,
            schema_version=error.schema_version,
            path=error.path,
            schema_path=error.schema_path,
        )
    return None


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
    planner = _planner(name, validator.schema_version)
    walk = _Walk()
    checked = walk.visit(record, planner.top, (), None, None)
    dropped: list[_Bad] = []
    for bad in walk.out_of_range:
        _drop(bad, on_bad_number, validator, dropped)
    if not validator.is_valid(checked):
        for bad in walk.out_of_bounds:
            _drop(bad, on_bad_number, validator, dropped)
        if planner.hidden_bounds or walk.merged_bounds:
            _drop_overlay_bounds(checked, validator, on_bad_number, dropped)
        failure = _failure(checked, validator, dropped)
        if failure is not None:
            raise failure
    reported = tuple(DroppedNumber(_resolve(bad.path), bad.value, bad.reason) for bad in dropped)
    if reported:
        _warn(reported, name, validator.schema_version)
    gone = {item.path for item in reported}
    changes = (ConvertedNumber(_resolve(change.path), change.before, change.after) for change in walk.converted)
    return CheckedRecord(checked, reported, tuple(item for item in changes if item.path not in gone))
