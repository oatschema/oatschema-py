# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
HEADER = "# SPDX-FileCopyrightText: 2026 The OATS Authors\n# SPDX-License-Identifier: Apache-2.0\n"
HEADED_SUFFIXES = {".py", ".toml", ".yml", ".yaml"}
HEADED_NAMES = {".gitignore"}


def project_files() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (PROJECT / ".git").exists():
        pytest.skip("needs a git checkout")
    listing = subprocess.run(
        [git, "-C", str(PROJECT), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    return [PROJECT / name for name in listing if (PROJECT / name).is_file()]


def test_source_files_start_with_spdx_header() -> None:
    missing = []
    for path in project_files():
        if path.suffix not in HEADED_SUFFIXES and path.name not in HEADED_NAMES:
            continue
        text = path.read_text(encoding="utf-8")
        if text.startswith("#!"):
            text = text.split("\n", 1)[1]
        if not text.startswith(HEADER):
            missing.append(path.relative_to(PROJECT).as_posix())
    assert not missing, f"missing the SPDX header: {missing}"


def test_dorsal_hub_appears_only_in_authors_and_released_schemas() -> None:
    exempt = (PROJECT / "src" / "oatschema" / "schemas", PROJECT / "tests")
    offenders = [
        path.relative_to(PROJECT).as_posix()
        for path in project_files()
        if path.name != "AUTHORS"
        and not any(path.is_relative_to(directory) for directory in exempt)
        and "Dorsal Hub" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders
