# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from oatschema._schemas import SchemaName, get_schema, resolve

_MASK = "…"
_PATH_PART_LIMIT = 100
_MESSAGE_LIMIT = 1000


class ValidationError(ValueError):
    """A record that does not conform to its schema.

    ``path`` locates the failing value within the record, ``schema_path`` the schema location that rejected it,
    and ``keyword`` the JSON Schema keyword at the end of ``schema_path`` (``"maxLength"``, ``"required"``, …).

    ``message`` shows each value from the record as ``…``, quoting only property names, and is at most about 1 KB.
    ``str()`` also shortens long parts of ``path``. The underlying validator's error is not kept.
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
        location = _shorten("/".join(_shorten(str(part), _PATH_PART_LIMIT) for part in self.path), _MESSAGE_LIMIT)
        return f"{self.schema_name} {self.schema_version} at /{location}: {self.message}"


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


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
        self._validator = jsonschema_rs.validator_for(
            get_schema(name, self._version), validate_formats=True, mask=_MASK
        )
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
            _shorten(error.message, _MESSAGE_LIMIT),
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
