# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import copyreg
import marshal
import re
from collections.abc import Iterator
from enum import Enum
from functools import lru_cache
from itertools import chain
from typing import Any

from oatschema._schemas import SchemaName, check_arguments, get_schema, resolve

_MASK = "…"
_PATH_PART_LIMIT = 100
_MESSAGE_LIMIT = 1000
_VALIDATOR_CALL = "{}(name, version=None, *, check_formats=True)"
_RECORD_CALL = "{}(record, name, version=None, *, check_formats=True)"
_DATE_TIME = re.compile(
    r"([0-9]{4})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])[Tt]([01][0-9]|2[0-3]):([0-5][0-9]):([0-5][0-9]|60)"
    r"(?:\.[0-9]+)?(?:[Zz]|([+-])([01][0-9]|2[0-3]):([0-5][0-9]))"
)
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_ENCODED_SURROGATE = re.compile(rb"\xed[\xa0-\xbf][\x80-\xbf]")
_SCALAR_TYPES = frozenset({float, int, bool, type(None)})
_MAX_SCAN_DEPTH = 2048
_SURROGATE_MESSAGE = "… contains an unpaired surrogate, which RFC 7493 does not allow in JSON text"


class ValidationError(ValueError):
    """A record that does not conform to its schema.

    ``path`` locates the failing value within the record, ``schema_path`` the schema location that rejected it,
    and ``keyword`` the JSON Schema keyword at the end of ``schema_path`` (``"maxLength"``, ``"required"``, …).

    ``message`` shows each value from the record as ``…``, quoting only property names, and is at most about 1 KB.
    ``str()`` also shortens long parts of ``path``. The underlying validator's error is not kept.
    ``schema_path`` and ``keyword`` are empty for a check no schema keyword expresses, such as an unpaired surrogate.
    """

    __module__ = "oatschema"

    def __init__(
        self,
        message: str,
        *,
        schema_name: str,
        schema_version: str,
        path: tuple[str | int, ...] = (),
        schema_path: tuple[str | int, ...] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.schema_name = schema_name
        self.schema_version = schema_version
        self.path = path
        self.schema_path = schema_path
        self.keyword = next((part for part in reversed(schema_path) if isinstance(part, str)), "")

    def __str__(self) -> str:
        return f"{self.schema_name} {self.schema_version} at /{format_path(self.path)}: {self.message}"

    def __reduce__(self) -> tuple[Any, ...]:
        return copyreg.__newobj__, (type(self), *self.args), vars(self)  # type: ignore[attr-defined]


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def format_path(path: tuple[str | int, ...]) -> str:
    """Return ``path`` as short, printable text."""
    location = shorten("/".join(shorten(str(part), _PATH_PART_LIMIT) for part in path), _MESSAGE_LIMIT)
    return location.encode("utf-8", "backslashreplace").decode("utf-8")


def _is_date_time(value: str) -> bool:
    match = _DATE_TIME.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second, sign, offset_hour, offset_minute = match.groups()
    if day > "28":
        days = _DAYS_IN_MONTH[int(month) - 1]
        number = int(year)
        if month == "02" and number % 4 == 0 and (number % 100 != 0 or number % 400 == 0):
            days += 1
        if int(day) > days:
            return False
    if second == "60":
        direction = -1 if sign == "-" else 1
        offset = int(offset_hour or 0) * 60 + int(offset_minute or 0)
        return (int(hour) * 60 + int(minute) - direction * offset) % 1440 == 1439
    return True


def _well_formed(text: str) -> bool:
    return text.isascii() or not any("\ud800" <= character <= "\udfff" for character in text)


def _surrogates(record: Any) -> Iterator[tuple[str | int, ...]]:
    stack: list[tuple[Any, tuple[str | int, ...]]] = [(record, ())]
    while stack:
        value, path = stack.pop()
        if len(path) > _MAX_SCAN_DEPTH:
            continue
        if isinstance(value, Enum) and not isinstance(value, str):
            value = value.value
        if isinstance(value, str):
            if not _well_formed(value):
                yield path
        elif isinstance(value, dict):
            for key, item in reversed(value.items()):
                if isinstance(key, str) and not _well_formed(key):
                    yield (*path, key)
                stack.append((item, (*path, key)))
        elif isinstance(value, (list, tuple)) and not _SCALAR_TYPES.issuperset(map(type, value)):
            stack.extend(((value[index], (*path, index)) for index in range(len(value) - 1, -1, -1)))


def _surrogate_paths(record: Any) -> Iterator[tuple[str | int, ...]]:
    try:
        if _ENCODED_SURROGATE.search(marshal.dumps(record)) is None:
            return iter(())
    except ValueError:
        pass
    return _surrogates(record)


def check_formats_argument(check_formats: object, call: str) -> None:
    """Raise ``TypeError``, naming ``call``, unless ``check_formats`` is a bool."""
    if not isinstance(check_formats, bool):
        raise TypeError(f"check_formats must be True or False, not {check_formats!r}; expected {call}")


def _check_validator_call(function: str, name: object, version: object, check_formats: object) -> None:
    call = _VALIDATOR_CALL.format(function)
    check_arguments(call, name, version)
    check_formats_argument(check_formats, call)


def _check_record_call(function: str, record: object, name: object, version: object, check_formats: object) -> None:
    call = _RECORD_CALL.format(function)
    if isinstance(record, str) and not isinstance(name, str):
        raise TypeError(f"{function}() takes the record first, then the schema name; expected {call}")
    check_arguments(call, name, version)
    check_formats_argument(check_formats, call)


def _not_json(error: ValueError) -> TypeError:
    return TypeError(
        f"the record must be JSON data (dict, list, str, int, float, bool or None): {shorten(str(error), 200)}"
    )


class Validator:
    """A compiled validator for one schema version.

    Formats (``date-time``, ``uri``, …) are checked unless ``check_formats`` is ``False``.
    Get one with ``get_validator``, which caches it. Requires the ``validate`` extra.
    """

    __module__ = "oatschema"
    __slots__ = ("_check_formats", "_error_type", "_name", "_validator", "_version")

    def __init__(self, name: SchemaName, version: str | None = None, *, check_formats: bool = True) -> None:
        _check_validator_call("Validator", name, version, check_formats)
        try:
            import jsonschema_rs
        except ImportError as error:
            raise ImportError("validation needs jsonschema-rs: pip install 'oatschema[validate]'") from error
        self._version = resolve(name, version)
        self._name = name
        self._check_formats = check_formats
        self._validator = jsonschema_rs.validator_for(
            get_schema(name, self._version),
            validate_formats=check_formats,
            mask=_MASK,
            formats={"date-time": _is_date_time},
        )
        self._error_type = jsonschema_rs.ValidationError

    def __repr__(self) -> str:
        formats = "" if self._check_formats else " check_formats=False"
        return f"<oatschema.Validator {self._name} {self._version}{formats}>"

    @property
    def schema_name(self) -> SchemaName:
        """The schema's name."""
        return self._name

    @property
    def schema_version(self) -> str:
        """The exact schema version."""
        return self._version

    @property
    def check_formats(self) -> bool:
        """Whether formats (``date-time``, ``uri``, …) are checked."""
        return self._check_formats

    @property
    def schema(self) -> dict[str, Any]:
        """The schema, as a new dict on every access."""
        return get_schema(self._name, self._version)

    def is_valid(self, record: Any) -> bool:
        """Return whether ``record`` conforms.

        A string or property name with an unpaired surrogate does not conform: RFC 7493 forbids them.
        Raises ``TypeError`` if ``record`` is not JSON data, such as a set or a dict with non-str keys.
        """
        if next(_surrogate_paths(record), None) is not None:
            return False
        try:
            return self._validator.is_valid(record)
        except ValueError as error:
            failure = _not_json(error)
        raise failure

    def validate(self, record: Any) -> None:
        """Raise ``ValidationError`` for an error in ``record``, if there is one.

        It stops at the first error jsonschema-rs finds, which is not always the first ``iter_errors`` yields.
        Raises ``TypeError`` if ``record`` is not JSON data, as ``is_valid`` does.
        """
        failure: Exception
        path = next(_surrogate_paths(record), None)
        if path is not None:
            raise self._surrogate_error(path)
        try:
            self._validator.validate(record)
        except self._error_type as error:
            failure = self._convert(error)
        except ValueError as error:
            failure = _not_json(error)
        else:
            return
        raise failure

    def iter_errors(self, record: Any) -> Iterator[ValidationError]:
        """Return an iterator over every error in ``record``, as ``ValidationError``s.

        jsonschema-rs finds every error before the first is yielded, so use ``validate`` when one error is enough.
        The order is jsonschema-rs's: the same record gives the same order with the same jsonschema-rs version,
        but it may change between jsonschema-rs versions. Raises ``TypeError`` if ``record`` is not JSON data,
        as ``is_valid`` does. A record with an unpaired surrogate reports only those: the rest can't be checked.
        """
        paths = _surrogate_paths(record)
        first = next(paths, None)
        if first is not None:
            return map(self._surrogate_error, chain((first,), paths))
        try:
            errors = self._validator.iter_errors(record)
        except ValueError as error:
            failure = _not_json(error)
        else:
            return map(self._convert, errors)
        raise failure

    def _surrogate_error(self, path: tuple[str | int, ...]) -> ValidationError:
        return ValidationError(_SURROGATE_MESSAGE, schema_name=self._name, schema_version=self._version, path=path)

    def _convert(self, error: Any) -> ValidationError:
        return ValidationError(
            shorten(error.message, _MESSAGE_LIMIT),
            schema_name=self._name,
            schema_version=self._version,
            path=tuple(error.instance_path),
            schema_path=tuple(error.schema_path),
        )


@lru_cache(maxsize=None, typed=True)
def _validator(name: SchemaName, version: str, check_formats: bool) -> Validator:
    return Validator(name, version, check_formats=check_formats)


@lru_cache(maxsize=None, typed=True)
def lookup(name: SchemaName, version: str | None, check_formats: bool) -> Validator:
    return _validator(name, resolve(name, version), check_formats)


def get_validator(name: SchemaName, version: str | None = None, *, check_formats: bool = True) -> Validator:
    """Return a cached ``Validator`` for the schema ``name`` in schema version ``version``.

    ``version`` follows the same rules as in ``get_schema``. Formats (``date-time``, ``uri``, …) are checked
    unless ``check_formats`` is ``False``. Requires the ``validate`` extra.

    Raises:
        ImportError: ``jsonschema-rs`` is not installed.
        ValueError: ``name`` is not a schema in any bundled version.
        LookupError: ``version`` is not bundled, or does not contain ``name``.
        TypeError: ``name`` is not a str, ``version`` is neither a str nor ``None``,
            or ``check_formats`` is not ``True`` or ``False``.
    """
    _check_validator_call("get_validator", name, version, check_formats)
    return lookup(name, version, check_formats)


def validate(record: Any, name: SchemaName, version: str | None = None, *, check_formats: bool = True) -> None:
    """Check ``record`` against the schema ``name`` in schema version ``version``, defaulting to ``SCHEMA_VERSION``.

    Raises ``ValidationError`` for the first error found; ``iter_errors`` returns every error.
    Before 1.0.0, a schema version may reject records that an older one accepted,
    so pass the version a record was written for. Formats (``date-time``, ``uri``, …) are checked
    unless ``check_formats`` is ``False``. Requires the ``validate`` extra.

    Raises:
        ValidationError: ``record`` does not conform. Other errors are as for ``get_validator``,
            and a ``TypeError`` for a str record with a non-str ``name`` says the arguments are swapped.
    """
    _check_record_call("validate", record, name, version, check_formats)
    lookup(name, version, check_formats).validate(record)


def is_valid(record: Any, name: SchemaName, version: str | None = None, *, check_formats: bool = True) -> bool:
    """Return whether ``record`` conforms to the schema ``name`` in schema version ``version``.

    Arguments and errors are as for ``validate``, except that a non-conforming record returns ``False``.
    """
    _check_record_call("is_valid", record, name, version, check_formats)
    return lookup(name, version, check_formats).is_valid(record)


def iter_errors(
    record: Any, name: SchemaName, version: str | None = None, *, check_formats: bool = True
) -> Iterator[ValidationError]:
    """Return an iterator over every error in ``record`` against the schema ``name`` in schema version ``version``.

    Arguments and errors are as for ``validate``, except that each error is yielded rather than raised.
    The order is as described for ``Validator.iter_errors``.
    """
    _check_record_call("iter_errors", record, name, version, check_formats)
    return lookup(name, version, check_formats).iter_errors(record)
