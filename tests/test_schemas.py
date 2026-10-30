# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast, get_args

import jsonschema_rs
import pytest
from packaging.version import Version

import oatschema
from oatschema import LEGACY_VERSIONS, SCHEMA_VERSION, SchemaName, available_versions, get_schema

SCHEMAS_DIR = Path(oatschema.__file__).parent / "schemas"
RESERVED_NAMES = {"changelog", "docs", "latest"}
NOTICE_PATTERN = re.compile(r"Copyright \d{4}(?:-\d{4})? The OATS Authors")


def declared_version(schema: dict[str, Any], version: str) -> Any:
    if Version(version) >= Version("0.8.0"):
        assert "version" not in schema
        return schema["x-version"]
    assert "x-version" not in schema
    return schema["version"]


def bundled_names(version: str) -> set[str]:
    return {path.stem for path in (SCHEMAS_DIR / version).glob("*.json") if path.name != "MANIFEST.json"}


def all_schemas() -> list[tuple[str, SchemaName]]:
    return [
        (version, cast(SchemaName, name)) for version in available_versions() for name in sorted(bundled_names(version))
    ]


def test_package_version_tracks_schema_version() -> None:
    assert ".".join(map(str, Version(oatschema.__version__).release[:3])) == SCHEMA_VERSION
    assert SCHEMA_VERSION == available_versions()[-1]


def test_available_versions_are_bundled_directories_in_order() -> None:
    directories = [path.name for path in SCHEMAS_DIR.iterdir() if path.is_dir()]
    versions = available_versions()
    assert set(versions) == set(directories)
    assert versions == sorted(versions, key=Version)
    assert versions[0] == "0.4.0"


def test_legacy_versions_are_exactly_those_before_0_7_0() -> None:
    assert LEGACY_VERSIONS == tuple(v for v in available_versions() if Version(v) < Version("0.7.0"))


def test_schema_name_matches_latest_and_covers_older_versions() -> None:
    names = set(get_args(SchemaName))
    assert names == bundled_names(SCHEMA_VERSION)
    for version in available_versions():
        assert bundled_names(version) <= names


def test_names_are_servable() -> None:
    for name in get_args(SchemaName):
        assert not name[0].isdigit()
        assert name not in RESERVED_NAMES


@pytest.mark.parametrize(("version", "name"), all_schemas())
def test_schema_identity(version: str, name: SchemaName) -> None:
    schema = get_schema(name, version)
    assert declared_version(schema, version) == version
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    if Version(version) < Version("0.7.0"):
        assert schema["$id"] == f"https://dorsalhub.com/schemas/open/{name}"
    else:
        assert schema["$id"] == f"https://oatschema.org/{version}/{name}"
    if Version(version) >= Version("0.8.0"):
        assert schema["x-license"]["id"] == "Apache-2.0"
        assert NOTICE_PATTERN.fullmatch(schema["x-license"]["notice"])


@pytest.mark.parametrize(("version", "name"), all_schemas())
def test_schema_is_valid_against_metaschema(version: str, name: SchemaName) -> None:
    jsonschema_rs.meta.validate(get_schema(name, version))


@pytest.mark.parametrize("version", available_versions())
def test_manifest_matches_bundled_files(version: str) -> None:
    manifest = json.loads((SCHEMAS_DIR / version / "MANIFEST.json").read_text())
    assert manifest["tag"] == f"v{version}"
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (SCHEMAS_DIR / version).iterdir()
        if path.name != "MANIFEST.json"
    }
    assert actual == manifest["files"]


def test_get_schema_defaults_to_schema_version() -> None:
    assert get_schema("classification") == get_schema("classification", SCHEMA_VERSION)
    assert declared_version(get_schema("classification"), SCHEMA_VERSION) == SCHEMA_VERSION


def test_get_schema_returns_independent_copies() -> None:
    first = get_schema("classification")
    first["properties"].clear()
    first["title"] = "changed"
    second = get_schema("classification")
    assert second["properties"]
    assert second["title"] != "changed"
    assert second is not get_schema("classification")


@pytest.mark.parametrize("name", ["classifications", "open/classification", "", "MANIFEST"])
def test_get_schema_rejects_unknown_name(name: Any) -> None:
    with pytest.raises(ValueError, match="unknown schema name"):
        get_schema(name)


@pytest.mark.parametrize("version", ["0.7", "latest", "v0.7.0", "0.7.0.0", "0.3.0", "9.9.9", ""])
def test_get_schema_rejects_inexact_or_unbundled_version(version: str) -> None:
    with pytest.raises(LookupError, match="is not bundled"):
        get_schema("classification", version)


@pytest.mark.parametrize(("name", "version"), [("temporal-segmentation", "0.6.0"), ("ranking", "0.7.0")])
def test_get_schema_rejects_name_missing_from_version(name: SchemaName, version: str) -> None:
    with pytest.raises(LookupError, match=f"not in schema version {version}"):
        get_schema(name, version)


def test_unknown_name_wins_over_unknown_version() -> None:
    with pytest.raises(ValueError):
        get_schema("nope", "9.9.9")  # type: ignore[call-overload]


def test_get_schema_json_mode_returns_released_text() -> None:
    text = get_schema("classification", "0.6.0", mode="json")
    assert isinstance(text, str)
    assert text == (SCHEMAS_DIR / "0.6.0" / "classification.json").read_text(encoding="utf-8")
    assert json.loads(text) == get_schema("classification", "0.6.0", mode="dict")


def test_get_schema_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode must be"):
        get_schema("classification", mode="yaml")  # type: ignore[call-overload]


def test_json_mode_reports_name_and_version_errors() -> None:
    with pytest.raises(ValueError, match="unknown schema name"):
        get_schema("nope", mode="json")  # type: ignore[call-overload]
    with pytest.raises(LookupError):
        get_schema("classification", "0.7", mode="json")


@pytest.mark.parametrize("name", [None, 1, b"classification", {"labels": []}, ["classification"]])
def test_get_schema_rejects_a_non_str_name(name: Any) -> None:
    with pytest.raises(TypeError, match=r"the schema name must be a str, .*expected get_schema\(name, version=None"):
        get_schema(name)


@pytest.mark.parametrize("version", [8, 0.8, b"0.8.0", ["0.8.0"], {"version": "0.8.0"}])
@pytest.mark.parametrize("mode", ["dict", "json"])
def test_get_schema_rejects_a_non_str_version(version: Any, mode: Any) -> None:
    with pytest.raises(TypeError, match=r"version must be a str .*expected get_schema\(name, version=None"):
        get_schema("classification", version, mode=mode)


@pytest.mark.parametrize("mode", [1, None, b"json", ["json"]])
def test_get_schema_rejects_a_non_str_mode(mode: Any) -> None:
    with pytest.raises(TypeError, match=r"mode must be 'dict' or 'json'.*expected get_schema\(name"):
        get_schema("classification", mode=mode)
