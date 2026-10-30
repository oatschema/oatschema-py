# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import copyreg
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from oatschema._schemas import SchemaName, check_arguments, get_schema, resolve

_MASK = "…"
_PATH_PART_LIMIT = 100
_MESSAGE_LIMIT = 1000
_VALIDATOR_CALL = "{}(name, version=None, *, check_formats=True)"
_RECORD_CALL = "{}(record, name, version=None, *, check_formats=True)"


class ValidationError(ValueError):
    """A record that does not conform to its schema.

    ``path`` locates the failing value within the record, ``schema_path`` the schema location that rejected it,
    and ``keyword`` the JSON Schema keyword at the end of ``schema_path`` (``"maxLength"``, ``"required"``, …).

    ``message`` shows each value from the record as ``…``, quoting only property names, and is at most about 1 KB.
    ``str()`` also shortens long parts of ``path``. The underlying validator's error is not kept.
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
        location = _shorten("/".join(_shorten(str(part), _PATH_PART_LIMIT) for part in self.path), _MESSAGE_LIMIT)
        return f"{self.schema_name} {self.schema_version} at /{location}: {self.message}"

    def __reduce__(self) -> tuple[Any, ...]:
        return copyreg.__newobj__, (type(self), *self.args), vars(self)  # type: ignore[attr-defined]


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _check_formats(check_formats: object, call: str) -> None:
    if not isinstance(check_formats, bool):
        raise TypeError(f"check_formats must be True or False, not {check_formats!r}; expected {call}")


def _check_validator_call(function: str, name: object, version: object, check_formats: object) -> None:
    call = _VALIDATOR_CALL.format(function)
    check_arguments(call, name, version)
    _check_formats(check_formats, call)


def _check_record_call(function: str, record: object, name: object, version: object, check_formats: object) -> None:
    call = _RECORD_CALL.format(function)
    if isinstance(record, str) and not isinstance(name, str):
        raise TypeError(f"{function}() takes the record first, then the schema name; expected {call}")
    check_arguments(call, name, version)
    _check_formats(check_formats, call)


def _not_json(error: ValueError) -> TypeError:
    return TypeError(
        f"the record must be JSON data (dict, list, str, int, float, bool or None): {_shorten(str(error), 200)}"
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
            get_schema(name, self._version), validate_formats=check_formats, mask=_MASK
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

        Raises ``TypeError`` if ``record`` is not JSON data, such as a set or a dict with non-str keys.
        """
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
        as ``is_valid`` does.
        """
        try:
            errors = self._validator.iter_errors(record)
        except ValueError as error:
            failure = _not_json(error)
        else:
            return map(self._convert, errors)
        raise failure

    def _convert(self, error: Any) -> ValidationError:
        return ValidationError(
            _shorten(error.message, _MESSAGE_LIMIT),
            schema_name=self._name,
            schema_version=self._version,
            path=tuple(error.instance_path),
            schema_path=tuple(error.schema_path),
        )


@lru_cache(maxsize=None, typed=True)
def _validator(name: SchemaName, version: str, check_formats: bool) -> Validator:
    return Validator(name, version, check_formats=check_formats)


@lru_cache(maxsize=None, typed=True)
def _lookup(name: SchemaName, version: str | None, check_formats: bool) -> Validator:
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
    return _lookup(name, version, check_formats)


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
    _lookup(name, version, check_formats).validate(record)


def is_valid(record: Any, name: SchemaName, version: str | None = None, *, check_formats: bool = True) -> bool:
    """Return whether ``record`` conforms to the schema ``name`` in schema version ``version``.

    Arguments and errors are as for ``validate``, except that a non-conforming record returns ``False``.
    """
    _check_record_call("is_valid", record, name, version, check_formats)
    return _lookup(name, version, check_formats).is_valid(record)


def iter_errors(
    record: Any, name: SchemaName, version: str | None = None, *, check_formats: bool = True
) -> Iterator[ValidationError]:
    """Return an iterator over every error in ``record`` against the schema ``name`` in schema version ``version``.

    Arguments and errors are as for ``validate``, except that each error is yielded rather than raised.
    The order is as described for ``Validator.iter_errors``.
    """
    _check_record_call("iter_errors", record, name, version, check_formats)
    return _lookup(name, version, check_formats).iter_errors(record)
