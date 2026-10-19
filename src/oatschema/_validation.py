# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import json
from collections.abc import Callable, Iterable, Iterator
from functools import lru_cache
from typing import Any

from oatschema._schemas import SchemaName, get_schema, resolve

_PREVIEW_LIMIT = 100
_MESSAGE_LIMIT = 1000


class ValidationError(ValueError):
    """A record that does not conform to its schema.

    ``path`` locates the failing value within the record, ``schema_path`` the schema location that rejected it,
    and ``keyword`` the JSON Schema keyword at the end of ``schema_path`` (``"maxLength"``, ``"required"``, …).

    Messages never quote more than a short preview of the record: a long string is cut to about 100 characters,
    and an array or object is described by its size. ``str()`` also shortens long parts of ``path``.
    The underlying validator's error is not kept.
    """

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
        location = _shorten("/".join(_shorten(str(part), _PREVIEW_LIMIT) for part in self.path), _MESSAGE_LIMIT)
        return f"{self.schema_name} {self.schema_version} at /{location}: {self.message}"


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _preview(value: Any) -> str:
    if isinstance(value, str):
        quoted = json.dumps(value[:_PREVIEW_LIMIT], ensure_ascii=False)
        if len(value) <= _PREVIEW_LIMIT and len(quoted) <= _PREVIEW_LIMIT:
            return quoted
        return f'{quoted[: _PREVIEW_LIMIT - 2]}…" ({len(value):,} characters)'
    if isinstance(value, list):
        return f"an array of {_count(len(value), 'item')}"
    if isinstance(value, dict):
        return f"an object with {_count(len(value), 'property', 'properties')}"
    if value is None or isinstance(value, bool | int | float):
        try:
            return _shorten(json.dumps(value), _PREVIEW_LIMIT)
        except ValueError:
            return "a number too large to show"
    return f"a {type(value).__name__}"


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number:,} {singular if number == 1 else plural or f'{singular}s'}"


def _join(values: Iterable[Any], conjunction: str = "and", shown: int = 5) -> str:
    items = list(values)
    previews = [_preview(value) for value in items[:shown]]
    if len(items) > shown:
        previews.append(f"{len(items) - shown:,} more")
    if len(previews) < 2:
        return "".join(previews)
    return f"{', '.join(previews[:-1])} {conjunction} {previews[-1]}"


def _unexpected(what: str, unexpected: list[Any]) -> str:
    return f"{what} are not allowed ({_join(unexpected)} {'was' if len(unexpected) == 1 else 'were'} unexpected)"


def _reason(error: Any) -> str:
    return _shorten(str(error), _PREVIEW_LIMIT)


_DESCRIPTIONS: dict[str, Callable[[Any, Any], str]] = {
    "AdditionalItems": lambda kind, error: f"no items are allowed after the first {_count(kind.limit, 'item')}",
    "AdditionalProperties": lambda kind, error: _unexpected("additional properties", kind.unexpected),
    "AnyOf": lambda kind, error: f"{_preview(error.instance)} is not valid under any of the schemas in anyOf",
    "BacktrackLimitExceeded": lambda kind, error: (
        f"regular expression backtracking limit exceeded: {_reason(kind.error)}"
    ),
    "Constant": lambda kind, error: f"{_preview(kind.expected_value)} was expected, not {_preview(error.instance)}",
    "Contains": lambda kind, error: f"{_preview(error.instance)} has no item valid under the contains schema",
    "ContentEncoding": lambda kind, error: (
        f"{_preview(error.instance)} is not compliant with content encoding {_preview(kind.content_encoding)}"
    ),
    "ContentMediaType": lambda kind, error: (
        f"{_preview(error.instance)} is not compliant with content media type {_preview(kind.content_media_type)}"
    ),
    "Custom": lambda kind, error: _reason(kind.message),
    "Enum": lambda kind, error: f"{_preview(error.instance)} is not one of {_join(kind.options, 'or')}",
    "ExclusiveMaximum": lambda kind, error: (
        f"{_preview(error.instance)} is greater than or equal to the exclusive maximum of {_preview(kind.limit)}"
    ),
    "ExclusiveMinimum": lambda kind, error: (
        f"{_preview(error.instance)} is less than or equal to the exclusive minimum of {_preview(kind.limit)}"
    ),
    "FalseSchema": lambda kind, error: f"{_preview(error.instance)} is not allowed here",
    "Format": lambda kind, error: f"{_preview(error.instance)} is not a valid {_preview(kind.format)}",
    "FromUtf8": lambda kind, error: f"content is not valid UTF-8: {_reason(kind.error)}",
    "MaxItems": lambda kind, error: f"{_preview(error.instance)} has more than {_count(kind.limit, 'item')}",
    "MaxLength": lambda kind, error: f"{_preview(error.instance)} is longer than {_count(kind.limit, 'character')}",
    "MaxProperties": lambda kind, error: (
        f"{_preview(error.instance)} has more than {_count(kind.limit, 'property', 'properties')}"
    ),
    "Maximum": lambda kind, error: f"{_preview(error.instance)} is greater than the maximum of {_preview(kind.limit)}",
    "MinItems": lambda kind, error: f"{_preview(error.instance)} has fewer than {_count(kind.limit, 'item')}",
    "MinLength": lambda kind, error: f"{_preview(error.instance)} is shorter than {_count(kind.limit, 'character')}",
    "MinProperties": lambda kind, error: (
        f"{_preview(error.instance)} has fewer than {_count(kind.limit, 'property', 'properties')}"
    ),
    "Minimum": lambda kind, error: f"{_preview(error.instance)} is less than the minimum of {_preview(kind.limit)}",
    "MultipleOf": lambda kind, error: f"{_preview(error.instance)} is not a multiple of {_preview(kind.multiple_of)}",
    "Not": lambda kind, error: f"{_preview(error.instance)} must not be valid under the schema in not",
    "OneOfMultipleValid": lambda kind, error: (
        f"{_preview(error.instance)} is valid under more than one of the schemas in oneOf"
    ),
    "OneOfNotValid": lambda kind, error: f"{_preview(error.instance)} is not valid under any of the schemas in oneOf",
    "Pattern": lambda kind, error: f"{_preview(error.instance)} does not match {_preview(kind.pattern)}",
    "PropertyNames": lambda kind, error: f"property name {_describe(kind.error)}",
    "Referencing": lambda kind, error: f"a schema reference could not be resolved: {_reason(kind.error)}",
    "RegexEngineFailure": lambda kind, error: f"regular expression engine failure: {_reason(kind.message)}",
    "Required": lambda kind, error: f"{_preview(kind.property)} is a required property",
    "Type": lambda kind, error: f"{_preview(error.instance)} is not of type {_join(kind.types, 'or')}",
    "UnevaluatedItems": lambda kind, error: _unexpected("unevaluated items", kind.unexpected),
    "UnevaluatedProperties": lambda kind, error: _unexpected("unevaluated properties", kind.unexpected),
    "UniqueItems": lambda kind, error: f"{_preview(error.instance)} has non-unique items",
}


def _describe(error: Any) -> str:
    kind = error.kind
    describe = _DESCRIPTIONS.get(type(kind).__name__.removeprefix("ValidationErrorKind_"))
    if describe is None:
        keyword = next((part for part in reversed(error.schema_path) if isinstance(part, str)), "schema")
        return f"{_preview(error.instance)} is not valid under {_preview(keyword)}"
    return _shorten(describe(kind, error), _MESSAGE_LIMIT)


class Validator:
    """A compiled validator for one schema version, with format assertions (``date-time``, ``uri``, …) enabled.

    Get one with ``get_validator``, which caches it. Requires the ``validate`` extra.
    """

    __slots__ = ("_error_type", "_name", "_validator", "_version")

    def __init__(self, name: SchemaName, version: str | None = None) -> None:
        try:
            import jsonschema_rs
        except ImportError as error:
            raise ImportError("validation needs jsonschema-rs: pip install 'oatschema[validate]'") from error
        self._version = resolve(name, version)
        self._name = name
        self._validator = jsonschema_rs.validator_for(get_schema(name, self._version), validate_formats=True, mask="…")
        self._error_type = jsonschema_rs.ValidationError

    def __repr__(self) -> str:
        return f"<oatschema.Validator {self._name} {self._version}>"

    @property
    def schema_name(self) -> SchemaName:
        """The schema's name."""
        return self._name

    @property
    def schema_version(self) -> str:
        """The exact schema version."""
        return self._version

    @property
    def schema(self) -> dict[str, Any]:
        """The schema, as a new dict on every access."""
        return get_schema(self._name, self._version)

    def is_valid(self, record: Any) -> bool:
        """Return whether ``record`` conforms."""
        return self._validator.is_valid(record)

    def validate(self, record: Any) -> None:
        """Raise ``ValidationError`` for an error in ``record``, if there is one.

        It stops at the first error jsonschema-rs finds, which is not always the first ``iter_errors`` yields.
        """
        try:
            self._validator.validate(record)
        except self._error_type as error:
            failure = self._convert(error)
        else:
            return
        raise failure

    def iter_errors(self, record: Any) -> Iterator[ValidationError]:
        """Return an iterator over every error in ``record``, as ``ValidationError``s.

        jsonschema-rs finds every error before the first is yielded, so use ``validate`` when one error is enough.
        The order is jsonschema-rs's: the same record gives the same order with the same jsonschema-rs version,
        but it may change between jsonschema-rs versions.
        """
        return map(self._convert, self._validator.iter_errors(record))

    def _convert(self, error: Any) -> ValidationError:
        return ValidationError(
            _describe(error),
            schema_name=self._name,
            schema_version=self._version,
            path=tuple(error.instance_path),
            schema_path=tuple(error.schema_path),
        )


@lru_cache(maxsize=None)
def _validator(name: SchemaName, version: str) -> Validator:
    return Validator(name, version)


@lru_cache(maxsize=None)
def _lookup(name: SchemaName, version: str | None) -> Validator:
    return _validator(name, resolve(name, version))


def get_validator(name: SchemaName, version: str | None = None) -> Validator:
    """Return a cached ``Validator`` for the schema ``name`` in schema version ``version``.

    ``version`` follows the same rules as in ``get_schema``. Requires the ``validate`` extra.

    Raises:
        ImportError: ``jsonschema-rs`` is not installed.
        ValueError: ``name`` is not a schema in any bundled version.
        LookupError: ``version`` is not bundled, or does not contain ``name``.
    """
    return _lookup(name, version)


def validate(record: Any, name: SchemaName, version: str | None = None) -> None:
    """Check ``record`` against the schema ``name`` in schema version ``version``, defaulting to ``SCHEMA_VERSION``.

    Raises ``ValidationError`` for the first error found; ``iter_errors`` returns every error.
    Before 1.0.0, a schema version may reject records that an older one accepted,
    so pass the version a record was written for. Requires the ``validate`` extra.

    Raises:
        ValidationError: ``record`` does not conform. Other errors are as for ``get_validator``.
    """
    _lookup(name, version).validate(record)


def is_valid(record: Any, name: SchemaName, version: str | None = None) -> bool:
    """Return whether ``record`` conforms to the schema ``name`` in schema version ``version``.

    Arguments and errors are as for ``validate``, except that a non-conforming record returns ``False``.
    """
    return _lookup(name, version).is_valid(record)


def iter_errors(record: Any, name: SchemaName, version: str | None = None) -> Iterator[ValidationError]:
    """Return an iterator over every error in ``record`` against the schema ``name`` in schema version ``version``.

    Arguments and errors are as for ``validate``, except that each error is yielded rather than raised.
    The order is as described for ``Validator.iter_errors``.
    """
    return _lookup(name, version).iter_errors(record)
