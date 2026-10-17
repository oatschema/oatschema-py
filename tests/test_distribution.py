# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import shutil
import subprocess
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest

import oatschema

PROJECT = Path(__file__).resolve().parent.parent
PACKAGE = PROJECT / "src" / "oatschema"


def package_files() -> set[str]:
    return {
        f"oatschema/{path.relative_to(PACKAGE).as_posix()}"
        for path in PACKAGE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("building needs uv")
    out = tmp_path_factory.mktemp("dist")
    subprocess.run([uv, "build", "--quiet", "--out-dir", str(out), str(PROJECT)], check=True)
    return out


def test_wheel_contains_exactly_the_package(dist: Path) -> None:
    (wheel,) = dist.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        all_names = archive.namelist()
    names = {name for name in all_names if not name.startswith("oatschema-")}
    assert names == package_files()
    dist_info = f"oatschema-{oatschema.__version__}.dist-info"
    assert {f"{dist_info}/licenses/LICENSE", f"{dist_info}/licenses/AUTHORS"} <= set(all_names)
    assert "oatschema/py.typed" in names
    assert {f"oatschema/schemas/{version}/MANIFEST.json" for version in oatschema.available_versions()} <= names


def test_wheel_metadata(dist: Path) -> None:
    (wheel,) = dist.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        metadata = Parser().parsestr(archive.read(f"oatschema-{oatschema.__version__}.dist-info/METADATA").decode())
    assert metadata["Name"] == "oatschema"
    assert metadata["Version"] == oatschema.__version__
    assert metadata["Requires-Python"] == ">=3.11"
    assert metadata["License-Expression"] == "Apache-2.0"
    assert set(metadata.get_all("License-File") or []) == {"LICENSE", "AUTHORS"}
    assert metadata["Author-email"] == "OATS maintainers <maintainers@oatschema.org>"
    assert not any(classifier.startswith("License ::") for classifier in metadata.get_all("Classifier") or [])
    assert set(metadata.get_all("Provides-Extra") or []) == {"all", "validate"}
    requirements = metadata.get_all("Requires-Dist") or []
    assert requirements, "the validate extra should declare jsonschema-rs"
    assert all("extra ==" in requirement for requirement in requirements)


def test_sdist_can_run_the_tests(dist: Path) -> None:
    (sdist,) = dist.glob("*.tar.gz")
    root = f"oatschema-{oatschema.__version__}"
    with tarfile.open(sdist) as archive:
        names = {name.removeprefix(f"{root}/") for name in archive.getnames()}
    assert {"pyproject.toml", "README.md", "LICENSE", "AUTHORS", "scripts/sync_schemas.py"} <= names
    assert {path.relative_to(PROJECT).as_posix() for path in (PROJECT / "tests").glob("*.py")} <= names
    assert {f"src/{name}" for name in package_files()} <= names
    assert not any(name.startswith((".claude", "venv/", ".venv/")) for name in names)
