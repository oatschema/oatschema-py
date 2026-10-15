import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import sync_schemas
from sync_schemas import SyncError


def schema_bytes(name: str, version: str, /, **overrides: Any) -> bytes:
    numeric = sync_schemas.parse_tag(f"v{version}")
    schema = {
        "$schema": sync_schemas.METASCHEMA,
        "$id": sync_schemas.expected_id(name, numeric),
        "title": name,
        "version": version,
        "type": "object",
    }
    schema.update(overrides)
    return json.dumps(schema, indent=2).encode()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True, text=True).stdout


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "oats"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "remote", "add", "origin", str(origin))
    (repo / "schemas").mkdir()
    for name in ("classification", "temporal-segmentation"):
        (repo / "schemas" / f"{name}.json").write_bytes(schema_bytes(name, "0.7.0"))
    (repo / "schemas" / "README.md").write_text("not a schema\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "release")
    git(repo, "tag", "-a", "v0.7.0", "-m", "v0.7.0")
    git(repo, "push", "-q", "origin", "HEAD", "--tags")
    return repo


@pytest.mark.parametrize("tag", ["0.7.0", "v0.7", "v0.7.0-rc1", "latest"])
def test_parse_tag_rejects(tag: str) -> None:
    with pytest.raises(SyncError):
        sync_schemas.parse_tag(tag)


def test_expected_id_switches_at_0_7_0() -> None:
    assert sync_schemas.expected_id("generic", (0, 6, 0)) == "https://dorsalhub.com/schemas/open/generic"
    assert sync_schemas.expected_id("generic", (0, 7, 0)) == "https://oatschema.org/0.7.0/generic"
    assert sync_schemas.expected_id("generic", (1, 0, 0)) == "https://oatschema.org/1.0.0/generic"


@pytest.mark.parametrize("name", ["docs", "latest", "changelog", "3d-detection", "Generic", "llm_output", "a--b", "-a"])
def test_check_name_rejects(name: str) -> None:
    with pytest.raises(SyncError):
        sync_schemas.check_name(name)


def test_check_schema_accepts_both_id_forms() -> None:
    sync_schemas.check_schema("generic", schema_bytes("generic", "0.6.0"), (0, 6, 0))
    sync_schemas.check_schema("generic", schema_bytes("generic", "0.7.0"), (0, 7, 0))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (schema_bytes("generic", "0.7.0", version="0.6.0"), "version"),
        (schema_bytes("generic", "0.7.0", **{"$id": "https://dorsalhub.com/schemas/open/generic"}), r"\$id"),
        (schema_bytes("generic", "0.7.0", **{"$schema": "http://json-schema.org/draft-07/schema#"}), r"\$schema"),
        (b'{"version": "0.7.0", "version": "0.7.0"}', "duplicate keys"),
        (b"[]", "not a JSON object"),
        (b"{", "invalid JSON"),
        (b'{"title": "\xff"}', "not UTF-8"),
    ],
)
def test_check_schema_rejects(data: bytes, message: str) -> None:
    with pytest.raises(SyncError, match=message):
        sync_schemas.check_schema("generic", data, (0, 7, 0))


def test_sync_copies_tag_byte_for_byte(release_repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "schemas"
    (release_repo / "schemas" / "classification.json").write_text("working tree edit\n")
    target = sync_schemas.sync(release_repo, "v0.7.0", dest)
    assert target == dest / "0.7.0"
    assert sorted(path.name for path in target.iterdir()) == [
        "MANIFEST.json",
        "classification.json",
        "temporal-segmentation.json",
    ]
    original = schema_bytes("classification", "0.7.0")
    assert (target / "classification.json").read_bytes() == original
    manifest = json.loads((target / "MANIFEST.json").read_text())
    assert manifest["tag"] == "v0.7.0"
    assert manifest["commit"] == git(release_repo, "rev-parse", "v0.7.0^{commit}").strip()
    assert manifest["files"]["classification.json"] == hashlib.sha256(original).hexdigest()
    assert [path.name for path in dest.iterdir()] == ["0.7.0"]


def test_sync_refuses_existing_version(release_repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "schemas"
    (dest / "0.7.0").mkdir(parents=True)
    with pytest.raises(SyncError, match="never rewritten"):
        sync_schemas.sync(release_repo, "v0.7.0", dest)


def test_sync_refuses_unpushed_tag(release_repo: Path, tmp_path: Path) -> None:
    git(release_repo, "tag", "v0.7.1")
    with pytest.raises(SyncError, match="not been pushed"):
        sync_schemas.sync(release_repo, "v0.7.1", tmp_path / "schemas")


def test_sync_refuses_moved_tag(release_repo: Path, tmp_path: Path) -> None:
    git(release_repo, "commit", "-q", "--allow-empty", "-m", "later")
    git(release_repo, "tag", "-f", "-a", "v0.7.0", "-m", "moved")
    with pytest.raises(SyncError, match="on origin"):
        sync_schemas.sync(release_repo, "v0.7.0", tmp_path / "schemas")


def test_sync_writes_nothing_when_a_schema_fails(release_repo: Path, tmp_path: Path) -> None:
    (release_repo / "schemas" / "docs.json").write_bytes(schema_bytes("docs", "0.7.1"))
    for path in (release_repo / "schemas").glob("*.json"):
        if path.stem != "docs":
            path.write_bytes(schema_bytes(path.stem, "0.7.1"))
    git(release_repo, "add", ".")
    git(release_repo, "commit", "-q", "-m", "bad release")
    git(release_repo, "tag", "-a", "v0.7.1", "-m", "v0.7.1")
    git(release_repo, "push", "-q", "origin", "--tags")
    dest = tmp_path / "schemas"
    with pytest.raises(SyncError, match="reserved"):
        sync_schemas.sync(release_repo, "v0.7.1", dest)
    assert not dest.exists()
