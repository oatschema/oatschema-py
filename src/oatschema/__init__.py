# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

"""OATS (Open Annotation Task Schemas) JSON schemas, bundled for Python."""

from oatschema._numbers import CheckedRecord, ConvertedNumber, DroppedNumber, DroppedNumberWarning, check_numbers
from oatschema._schemas import LEGACY_VERSIONS, SCHEMA_VERSION, SchemaName, available_versions, get_schema
from oatschema._validation import ValidationError, Validator, get_validator, is_valid, iter_errors, validate

__version__ = "0.9.0.1"

__all__ = [
    "CheckedRecord",
    "ConvertedNumber",
    "DroppedNumber",
    "DroppedNumberWarning",
    "LEGACY_VERSIONS",
    "SCHEMA_VERSION",
    "SchemaName",
    "ValidationError",
    "Validator",
    "__version__",
    "available_versions",
    "check_numbers",
    "get_schema",
    "get_validator",
    "is_valid",
    "iter_errors",
    "validate",
]
