from functools import lru_cache
from typing import TYPE_CHECKING, Any

from oatschema._schemas import SchemaName, get_schema, resolve

if TYPE_CHECKING:
    from jsonschema_rs import Validator


class ValidationError(ValueError):
    """A record that does not conform to its schema.

    ``path`` locates the failing value within the record, and ``schema_path`` the keyword that rejected it.
    The underlying validator's error is available as ``__cause__``.
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

    def __str__(self) -> str:
        location = "/".join(map(str, self.path))
        return f"{self.schema_name} {self.schema_version} at /{location}: {self.message}"


@lru_cache(maxsize=None)
def _validator(name: SchemaName, version: str) -> "Validator":
    try:
        import jsonschema_rs
    except ImportError as error:
        raise ImportError("validation needs jsonschema-rs: pip install 'oatschema[validate]'") from error
    return jsonschema_rs.validator_for(get_schema(name, version), validate_formats=True)


def get_validator(name: SchemaName, version: str | None = None) -> "Validator":
    """Return a cached ``jsonschema_rs`` validator for the schema ``name`` in schema version ``version``.

    ``version`` follows the same rules as in ``get_schema``. Format assertions (``date-time``, ``uri``, …) are enabled.
    Requires the ``validate`` extra.

    Raises:
        ImportError: ``jsonschema-rs`` is not installed.
        ValueError: ``name`` is not a schema in any bundled version.
        LookupError: ``version`` is not bundled, or does not contain ``name``.
    """
    return _validator(name, resolve(name, version))


def validate(record: Any, name: SchemaName, version: str | None = None) -> None:
    """Check ``record`` against the schema ``name`` in schema version ``version``, defaulting to ``SCHEMA_VERSION``.

    Before 1.0.0, a schema version may reject records that an older one accepted,
    so pass the version a record was written for. Requires the ``validate`` extra.

    Raises:
        ValidationError: ``record`` does not conform. Other errors are as for ``get_validator``.
    """
    resolved = resolve(name, version)
    validator = _validator(name, resolved)
    import jsonschema_rs

    try:
        validator.validate(record)
    except jsonschema_rs.ValidationError as error:
        raise ValidationError(
            error.message,
            schema_name=name,
            schema_version=resolved,
            path=tuple(error.instance_path),
            schema_path=tuple(error.schema_path),
        ) from error


def is_valid(record: Any, name: SchemaName, version: str | None = None) -> bool:
    """Return whether ``record`` conforms to the schema ``name`` in schema version ``version``.

    Arguments and errors are as for ``validate``, except that a non-conforming record returns ``False``.
    """
    return _validator(name, resolve(name, version)).is_valid(record)
