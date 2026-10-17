# Contributing

The schemas themselves are maintained in [oatschema/oats](https://github.com/oatschema/oats). Propose schema changes there; this repository only bundles released versions and provides the Python API.

## Development

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests scripts
```

The release-example tests validate every bundled version against that release's positive and negative examples. They read the tags from a checkout of oats, which defaults to `~/projects/oats`; set `OATS_REPO` to point elsewhere. Without a checkout they are skipped.

## Package versions

The package version is `a.b.c.d`. `a.b.c` is the newest bundled schema version (`oatschema.SCHEMA_VERSION`), and `d` is the package's own revision, omitted when it is 0. See the README for how users should pin.

- Never publish `a.b.c` before oats has tagged and pushed `va.b.c`.
- Every release bundles every schema version from 0.4.0 onward. A version is never removed or changed once bundled.
- A package-only fix or feature bumps `d`: `0.8.0.1`, `0.8.0.2`, and so on.

## Bundling a new schema release

```
uv run python scripts/sync_schemas.py ~/projects/oats vX.Y.Z
```

The script copies `schemas/*.json` byte for byte from the tag into `src/oatschema/schemas/X.Y.Z/` and writes a `MANIFEST.json` with the tag, its commit and each file's SHA-256. It never reads the working tree. It refuses to run if:

- the tag isn't on `origin`, or points to a different commit there;
- the version directory already exists;
- a schema name isn't lowercase kebab-case starting with a letter, or is reserved (`changelog`, `docs`, `latest`), because oatschema.org serves schemas at its root;
- a file isn't UTF-8 JSON, or has duplicate keys;
- `$schema` isn't draft 2020-12, or `$id` has the wrong form: `https://dorsalhub.com/schemas/open/<name>` before 0.7.0, `https://oatschema.org/<version>/<name>` from 0.7.0;
- the schema's version doesn't match the tag. Up to 0.7.0 it is the top-level `version` keyword. From 0.8.0 it is `x-version`, a top-level `version` is rejected, and `x-license` must be Apache-2.0 with a "Copyright YYYY[-YYYY] The OATS Authors" notice.

Then, in one change:

1. Set `SCHEMA_VERSION` in `src/oatschema/_schemas.py` to the new version, and add any new names to `SchemaName`.
2. Set `__version__` in `src/oatschema/__init__.py` to `X.Y.Z`.
3. Add a CHANGELOG entry.

The tests check that these agree with the bundled files.

## Releasing

A maintainer builds and publishes each release by hand, with `uv build` and then `uv publish`. CI only runs the checks and never publishes.

## Licensing and sign-off

- OATS is licensed under Apache-2.0, and contributions are accepted under the same licence. There is no CLA.
- Contributors keep the copyright in their contributions. Copyright is held by The OATS Authors, and you may add yourself or your employer to `AUTHORS`.
- Every commit needs a [Developer Certificate of Origin](https://developercertificate.org/) sign-off: commit with `git commit -s`.
- Every new source file (`.py`, `.toml`, `.yml`) starts with this header, which the tests check:

  ```
  # SPDX-FileCopyrightText: 2026 The OATS Authors
  # SPDX-License-Identifier: Apache-2.0
  ```

  Keep the year as it is in new files; it is the year the project started, not the current year.
