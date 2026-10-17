# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import json
import re
from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any, Literal, overload

SchemaName = Literal[
    "audio-transcription",
    "classification",
    "document-extraction",
    "embedding",
    "entity-extraction",
    "generic",
    "geolocation",
    "llm-output",
    "object-detection",
    "ranking",
    "regression",
    "temporal-segmentation",
]

SCHEMA_VERSION = "0.8.0"
LEGACY_VERSIONS = ("0.4.0", "0.5.0", "0.6.0")

_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_MANIFEST_NAME = "MANIFEST.json"


def _root() -> Traversable:
    return files("oatschema") / "schemas"


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


@cache
def _bundled() -> dict[str, frozenset[str]]:
    bundled: dict[str, frozenset[str]] = {}
    for entry in _root().iterdir():
        if entry.is_dir() and _VERSION_PATTERN.fullmatch(entry.name):
            bundled[entry.name] = frozenset(
                item.name.removesuffix(".json")
                for item in entry.iterdir()
                if item.is_file() and item.name.endswith(".json") and item.name != _MANIFEST_NAME
            )
    return dict(sorted(bundled.items(), key=lambda item: _version_key(item[0])))


@cache
def _read(name: str, version: str) -> bytes:
    return (_root() / version / f"{name}.json").read_bytes()


def available_versions() -> list[str]:
    """Return every bundled schema version, oldest first.

    Versions in ``LEGACY_VERSIONS`` predate OATS: their ``$id`` is ``https://dorsalhub.com/schemas/open/<name>``
    in every version, so build each validator from a single schema and never load two of them into one registry.
    """
    return list(_bundled())


def resolve(name: str, version: str | None) -> str:
    """Return the exact version ``get_schema(name, version)`` would read, after checking that it exists."""
    resolved = SCHEMA_VERSION if version is None else version
    bundled = _bundled()
    if not any(name in names for names in bundled.values()):
        known = sorted(frozenset().union(*bundled.values()))
        raise ValueError(f"unknown schema name {name!r}; expected one of {', '.join(known)}")
    if resolved not in bundled:
        raise LookupError(f"schema version {resolved!r} is not bundled; available: {', '.join(bundled)}")
    if name not in bundled[resolved]:
        raise LookupError(f"schema {name!r} is not in schema version {resolved}")
    return resolved


@overload
def get_schema(name: SchemaName, version: str | None = None, *, mode: Literal["dict"] = "dict") -> dict[str, Any]: ...
@overload
def get_schema(name: SchemaName, version: str | None = None, *, mode: Literal["json"]) -> str: ...
def get_schema(name: SchemaName, version: str | None = None, *, mode: Literal["dict", "json"] = "dict") -> Any:
    """Return the schema ``name`` from schema version ``version``, or from ``SCHEMA_VERSION`` if it is ``None``.

    With ``mode="dict"`` (the default), each call returns a new dict, so callers may modify it freely.
    With ``mode="json"``, it returns the schema file's text exactly as released.
    ``version`` must be an exact bundled version such as ``"0.8.0"``; partial versions and aliases are not accepted.

    Raises:
        ValueError: ``name`` is not a schema in any bundled version, or ``mode`` is not ``"dict"`` or ``"json"``.
        LookupError: ``version`` is not bundled, or does not contain ``name``.
    """
    if mode not in ("dict", "json"):
        raise ValueError(f"mode must be 'dict' or 'json', not {mode!r}")
    data = _read(name, resolve(name, version))
    if mode == "json":
        return data.decode("utf-8")
    return json.loads(data)
