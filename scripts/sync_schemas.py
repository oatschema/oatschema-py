"""Bundle one OATS release into the package.

Usage: ``python scripts/sync_schemas.py <oats-repo> <tag>``, e.g. ``python scripts/sync_schemas.py ~/projects/oats v0.7.0``.

Schemas are read from the tag, never the working tree, and copied byte for byte into
``src/oatschema/schemas/<version>/`` alongside a ``MANIFEST.json`` of SHA-256 checksums.
The tag must exist on ``origin`` at the same commit, and an existing version directory is never overwritten.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_DEST = Path(__file__).resolve().parent.parent / "src" / "oatschema" / "schemas"
METASCHEMA = "https://json-schema.org/draft/2020-12/schema"
FIRST_OATSCHEMA_ORG_VERSION = (0, 7, 0)
RESERVED_NAMES = frozenset({"changelog", "docs", "latest"})
NAME_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
TAG_PATTERN = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
MANIFEST_NAME = "MANIFEST.json"


class SyncError(Exception):
    """A release that cannot be bundled."""


def parse_tag(tag: str) -> tuple[int, int, int]:
    """Return the numeric version of a release tag such as ``v0.7.0``."""
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise SyncError(f"{tag!r} is not a release tag of the form vX.Y.Z")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def expected_id(name: str, version: tuple[int, int, int]) -> str:
    """Return the ``$id`` a schema must have in the given release."""
    if version >= FIRST_OATSCHEMA_ORG_VERSION:
        return f"https://oatschema.org/{'.'.join(map(str, version))}/{name}"
    return f"https://dorsalhub.com/schemas/open/{name}"


def check_name(name: str) -> None:
    """Reject schema names the package and website cannot serve."""
    if not NAME_PATTERN.fullmatch(name):
        raise SyncError(f"{name!r} is not a lowercase kebab-case name starting with a letter")
    if name in RESERVED_NAMES:
        raise SyncError(f"{name!r} is a reserved name")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise SyncError(f"duplicate keys {duplicates}")
    return dict(pairs)


def check_schema(name: str, data: bytes, version: tuple[int, int, int]) -> None:
    """Reject a schema file whose content does not belong in the given release."""
    check_name(name)
    try:
        schema = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as error:
        raise SyncError(f"{name}: not UTF-8: {error}") from error
    except json.JSONDecodeError as error:
        raise SyncError(f"{name}: invalid JSON: {error}") from error
    except SyncError as error:
        raise SyncError(f"{name}: {error}") from error
    if not isinstance(schema, dict):
        raise SyncError(f"{name}: not a JSON object")
    version_text = ".".join(map(str, version))
    expected = {"$schema": METASCHEMA, "$id": expected_id(name, version), "version": version_text}
    for key, value in expected.items():
        if schema.get(key) != value:
            raise SyncError(f"{name}: {key} is {schema.get(key)!r}, expected {value!r}")


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def resolve_tag(repo: Path, tag: str) -> str:
    """Return the tag's commit, after checking that ``origin`` has the same tag at the same commit."""
    local = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").decode().strip()
    remote_lines = _git(repo, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    remote: dict[str, str] = {}
    for line in remote_lines.decode().splitlines():
        sha, ref = line.split("\t")
        remote[ref] = sha
    remote_commit = remote.get(f"refs/tags/{tag}^{{}}", remote.get(f"refs/tags/{tag}"))
    if remote_commit is None:
        raise SyncError(f"{tag} has not been pushed to origin")
    if remote_commit != local:
        raise SyncError(f"{tag} is {local} locally but {remote_commit} on origin")
    return local


def read_schemas(repo: Path, tag: str) -> dict[str, bytes]:
    """Return each schema in the tag's ``schemas/`` directory, keyed by name."""
    listing = _git(repo, "ls-tree", "--name-only", f"{tag}:schemas").decode().splitlines()
    files = sorted(entry for entry in listing if entry.endswith(".json"))
    if not files:
        raise SyncError(f"{tag} has no schemas")
    return {entry.removesuffix(".json"): _git(repo, "show", f"{tag}:schemas/{entry}") for entry in files}


def build_manifest(tag: str, commit: str, schemas: dict[str, bytes]) -> bytes:
    """Return the ``MANIFEST.json`` content for a bundled release."""
    manifest = {
        "tag": tag,
        "commit": commit,
        "files": {f"{name}.json": hashlib.sha256(data).hexdigest() for name, data in sorted(schemas.items())},
    }
    return (json.dumps(manifest, indent=2) + "\n").encode()


def sync(repo: Path, tag: str, dest: Path) -> Path:
    """Bundle the release ``tag`` of ``repo`` into ``dest`` and return its version directory."""
    version = parse_tag(tag)
    target = dest / ".".join(map(str, version))
    if target.exists():
        raise SyncError(f"{target} already exists; bundled releases are never rewritten")
    commit = resolve_tag(repo, tag)
    schemas = read_schemas(repo, tag)
    for name, data in schemas.items():
        check_schema(name, data, version)
    dest.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=dest))
    staging.chmod(0o755)
    try:
        for name, data in schemas.items():
            (staging / f"{name}.json").write_bytes(data)
        (staging / MANIFEST_NAME).write_bytes(build_manifest(tag, commit, schemas))
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("repo", type=Path, help="path to a checkout of oatschema/oats")
    parser.add_argument("tag", help="release tag, e.g. v0.7.0")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="schemas directory to bundle into")
    args = parser.parse_args(argv)
    try:
        target = sync(args.repo.expanduser(), args.tag, args.dest)
    except SyncError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"bundled {args.tag} into {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
