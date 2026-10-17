# oatschema

The OATS (Open Annotation Task Schemas) JSON schemas, bundled for Python: strict, versioned schemas for the outputs of machine learning models, workflows and human annotation. OATS is unrelated to the W3C Web Annotation data model, formerly Open Annotation.

Schemas, documentation and the changelog live at [oatschema.org](https://oatschema.org). The schemas are maintained in [oatschema/oats](https://github.com/oatschema/oats); this package bundles them unchanged.

## Install

```
pip install oatschema              # schemas only, no dependencies
pip install 'oatschema[validate]'  # adds validation with jsonschema-rs
pip install 'oatschema[all]'       # every optional feature
```

## Usage

```python
import oatschema

oatschema.validate(record, "classification")  # raises oatschema.ValidationError
oatschema.is_valid(record, "classification", version="0.6.0")  # True or False
validator = oatschema.get_validator("classification")  # cached jsonschema-rs validator

schema = oatschema.get_schema("classification")  # a dict, for any validator or tool
text = oatschema.get_schema("classification", mode="json")  # the schema file exactly as released
```

- `get_schema` returns a new dict on every call, so you can change it without affecting other callers.
- `validate`, `is_valid` and `get_validator` need the `validate` extra. Without it, they raise `ImportError`; `get_schema` always works, so you can bring your own validator.
- `ValidationError` is a `ValueError` with `schema_name`, `schema_version`, `path` (where in the record) and `schema_path` (which schema rule failed). The jsonschema-rs error is attached as its `__cause__`.
- An unknown schema name raises `ValueError`. A version that isn't bundled, or doesn't contain that schema, raises `LookupError`.

## Schema versions

Every function takes a `version`, which defaults to `oatschema.SCHEMA_VERSION`, the newest bundled version. Versions must be exact, such as `"0.8.0"`; `"0.8"` and `"latest"` are rejected.

`oatschema.available_versions()` lists every bundled version, oldest first. Before 1.0.0, a schema version may reject records that an older one accepted, so validate a record against the version it was written for.

Each schema states its own version: in the `x-version` keyword from 0.8.0, and in `version` up to 0.7.0.

Versions 0.4.0 to 0.6.0 are listed in `oatschema.LEGACY_VERSIONS`. They predate OATS, and each schema's `$id` is `https://dorsalhub.com/schemas/open/<name>` in all three. Build each validator from a single schema, and never load two of these versions into one JSON Schema registry. From 0.7.0, `$id`s include the version (`https://oatschema.org/0.8.0/classification`), so versions can sit side by side.

## Package versions

The package version is `a.b.c.d`:

- `a.b.c` is the newest schema version the package bundles, equal to `oatschema.SCHEMA_VERSION`;
- `d` is the package's own revision, for fixes and features that don't add a schema version. It is left out when it is 0, so the first release bundling schema 0.8.0 is `0.8.0`, followed by `0.8.0.1`, `0.8.0.2` and so on.

Every package release bundles every earlier schema version too, so upgrading never removes a version you validate against. Pin like this:

- before 1.0: `oatschema~=0.8.0`, because a new schema minor version may change what records are valid;
- from 1.0: `oatschema>=1.3,<2`.

## License

Apache-2.0. See [LICENSE](LICENSE). Copyright is held by The OATS Authors, listed in [AUTHORS](AUTHORS). Contributions need a DCO sign-off; see [CONTRIBUTING.md](CONTRIBUTING.md).
