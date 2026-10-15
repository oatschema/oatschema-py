import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast, get_args

import jsonschema_rs
import pytest

import oatschema
from oatschema import SCHEMA_VERSION, SchemaName, ValidationError, available_versions, get_validator, is_valid, validate

OATS_REPO = Path(os.environ.get("OATS_REPO", Path.home() / "projects" / "oats"))
SEGMENT = {"label": "scene", "start_time": 0, "end_time": 1.5}


def test_valid_record_passes() -> None:
    record = {"segments": [SEGMENT], "vocabulary_url": "https://example.com/vocabulary"}
    validate(record, "temporal-segmentation")
    assert is_valid(record, "temporal-segmentation")


def test_invalid_record_raises_package_error() -> None:
    record = {"segments": [{**SEGMENT, "start_time": -1}]}
    assert not is_valid(record, "temporal-segmentation")
    with pytest.raises(ValidationError) as caught:
        validate(record, "temporal-segmentation")
    error = caught.value
    assert isinstance(error, ValueError)
    assert isinstance(error.__cause__, jsonschema_rs.ValidationError)
    assert error.schema_name == "temporal-segmentation"
    assert error.schema_version == SCHEMA_VERSION
    assert error.path == ("segments", 0, "start_time")
    assert error.schema_path[-1] == "minimum"
    assert str(error).startswith(f"temporal-segmentation {SCHEMA_VERSION} at /segments/0/start_time: ")


def test_formats_are_asserted() -> None:
    assert not is_valid({"segments": [SEGMENT], "vocabulary_url": "not a uri"}, "temporal-segmentation")


def test_version_selects_schema() -> None:
    record = {"segments": [SEGMENT]}
    with pytest.raises(LookupError):
        validate(record, "temporal-segmentation", "0.6.0")
    assert not is_valid({}, "classification", "0.4.0")
    with pytest.raises(ValidationError) as caught:
        validate({}, "classification", "0.4.0")
    assert caught.value.schema_version == "0.4.0"


def test_get_validator_is_cached_per_resolved_version() -> None:
    assert get_validator("classification") is get_validator("classification", SCHEMA_VERSION)
    assert get_validator("classification", "0.6.0") is not get_validator("classification", "0.5.0")


@pytest.mark.parametrize("call", [validate, is_valid])
def test_validation_rejects_unknown_name_and_version(call: Any) -> None:
    with pytest.raises(ValueError, match="unknown schema name"):
        call({}, "nope")
    with pytest.raises(LookupError):
        call({}, "classification", "0.7")


def test_works_without_validate_extra() -> None:
    script = """
import sys
sys.modules["jsonschema_rs"] = None
import oatschema
assert oatschema.get_schema("classification")["version"] == oatschema.SCHEMA_VERSION
for call in (oatschema.get_validator, oatschema.validate, oatschema.is_valid):
    args = ("classification",) if call is oatschema.get_validator else ({}, "classification")
    try:
        call(*args)
    except ImportError as error:
        assert "pip install 'oatschema[validate]'" in str(error), error
    else:
        raise AssertionError(f"{call.__name__} did not raise ImportError")
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def schema_for_example(stem: str, names: frozenset[str]) -> SchemaName:
    matches = [name for name in names if stem.startswith(f"{name}-")]
    assert matches, f"no schema for example {stem}"
    return cast(SchemaName, max(matches, key=len))


def tag_examples(version: str, kind: str) -> list[tuple[str, SchemaName, Any]]:
    tag = f"v{version}"
    listing = subprocess.run(
        ["git", "-C", str(OATS_REPO), "ls-tree", "-r", "--name-only", tag],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    paths = [path for path in listing if f"examples/{kind}/" in path and path.endswith(".json")]
    names = frozenset(get_args(SchemaName))
    examples = []
    for path in paths:
        content = subprocess.run(
            ["git", "-C", str(OATS_REPO), "show", f"{tag}:{path}"], capture_output=True, check=True
        ).stdout
        stem = Path(path).stem
        examples.append((stem, schema_for_example(stem, names), json.loads(content)))
    assert examples, f"{tag} has no {kind} examples"
    return examples


needs_oats = pytest.mark.skipif(not (OATS_REPO / ".git").exists(), reason="set OATS_REPO to a checkout of oats")


@needs_oats
@pytest.mark.parametrize("version", available_versions())
def test_release_examples(version: str) -> None:
    for _stem, name, record in tag_examples(version, "positive"):
        validate(record, name, version)
    for stem, name, record in tag_examples(version, "negative"):
        assert not is_valid(record, name, version), stem


def test_validation_api_is_exported() -> None:
    assert {"ValidationError", "get_validator", "is_valid", "validate"} <= set(oatschema.__all__)
