# oatschema

[![PyPI](https://img.shields.io/pypi/v/oatschema.svg)](https://pypi.org/project/oatschema/)
[![Python versions](https://img.shields.io/pypi/pyversions/oatschema.svg)](https://pypi.org/project/oatschema/)
[![CI](https://github.com/oatschema/oatschema-python/actions/workflows/ci.yml/badge.svg)](https://github.com/oatschema/oatschema-python/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/oatschema/oatschema-python/blob/main/LICENSE)

The [OATS](https://oatschema.org) (Open Annotation Task Schemas) JSON schemas for Python.

OATS is a set of strict, versioned JSON Schema 2020-12 schemas for the outputs of machine learning models, workflow steps and human annotation: one shape for a classification, a transcription or a set of detected objects, whatever produced it. This package bundles the schemas so you can validate those records anywhere. (OATS is unrelated to the W3C Web Annotation data model, formerly Open Annotation.)

- **No dependencies.** The core uses only the standard library. Validation is an optional extra built on [jsonschema-rs](https://github.com/Stranger6667/jsonschema).
- **Exactly as released.** Each schema is byte-identical to its [oatschema/oats](https://github.com/oatschema/oats) release, and a checksum manifest ships with every version.
- **Every version.** Each release bundles every schema version from 0.4.0 on, so records keep validating against the version they were written for.
- **Typed.** Ships `py.typed`. Schema names are a `Literal`, so mypy and pyright catch typos.

## Quick start

```
pip install 'oatschema[validate]'
```

```python
import oatschema

oatschema.validate({"labels": [{"label": "cat", "score": 0.92}]}, "classification")  # passes
oatschema.validate({"labels": [{"label": "cat", "score": 1.5}]}, "classification")
# oatschema.ValidationError: classification 0.8.0 at /labels/0/score: 1.5 is greater than the maximum of 1

schema = oatschema.get_schema("classification")  # a fresh dict, safe to modify
schema["$id"]  # "https://oatschema.org/0.8.0/classification"
oatschema.SCHEMA_VERSION  # "0.8.0", used when you don't pass a version
```

`pip install oatschema` installs the schemas alone, with no dependencies; `get_schema` works without the extra. `oatschema[all]` installs every optional feature.

## Schemas

| Name | Holds | Since |
| :--- | :--- | :--- |
| `audio-transcription` | Transcribed speech, with timed segments, speakers and non-verbal events | 0.4.0 |
| `classification` | Predicted labels with scores and an optional vocabulary | 0.4.0 |
| `document-extraction` | Document layout and content: text blocks, coordinates and pages | 0.4.0 |
| `embedding` | A dense or sparse vector and the model that produced it | 0.4.0 |
| `entity-extraction` | Entities linked to their evidence, and relations between them | 0.4.0 |
| `generic` | Arbitrary key-value data | 0.4.0 |
| `geolocation` | A geographic feature, as a strict subset of GeoJSON | 0.4.0 |
| `llm-output` | One prompt and response, with generation parameters and usage | 0.4.0 |
| `object-detection` | Boxes, polygons and their hierarchy within a source | 0.4.0 |
| `ranking` | Ranked results for a query, such as search or retrieval hits | 0.8.0 |
| `regression` | Numeric predictions, intervals and forecasts | 0.4.0 |
| `temporal-segmentation` | Labelled time intervals, such as chapters, scenes or speaker turns | 0.7.0 |

Browse every schema, its fields and the changelog at [oatschema.org](https://oatschema.org).

## Validating records

`validate` raises `oatschema.ValidationError`, and `is_valid` returns a bool. Both use a cached [jsonschema-rs](https://github.com/Stranger6667/jsonschema) validator that also checks formats such as `uri` and `date-time`. `get_validator` returns that validator, for when you want its full API.

```python
try:
    oatschema.validate(record, "classification")
except oatschema.ValidationError as error:
    error.path  # ("labels", 0, "score"): where in the record
    error.schema_path  # ("properties", "labels", ..., "maximum"): which rule failed
    error.schema_name, error.schema_version  # "classification", "0.8.0"
```

`ValidationError` is a `ValueError`, and reports the first error it finds. The jsonschema-rs error is attached as its `__cause__`, so your code never needs to import jsonschema-rs error types.

**Bring your own validator.** `get_schema` returns a plain dict for any JSON Schema 2020-12 validator. `x-license` and `x-version` are the only non-standard keywords, and 2020-12 validators ignore them. With [jsonschema](https://python-jsonschema.readthedocs.io):

```python
from jsonschema import Draft202012Validator

schema = oatschema.get_schema("classification")
validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
validator.is_valid({"labels": [{"label": "cat", "score": 0.92}]})  # True
```

## API

| Name | Description |
| :--- | :--- |
| `get_schema(name, version=None)` | The schema as a new dict on every call |
| `get_schema(name, version=None, mode="json")` | The schema file's text, exactly as released |
| `available_versions()` | Every bundled schema version, oldest first |
| `SCHEMA_VERSION` | The default schema version, the newest bundled |
| `LEGACY_VERSIONS` | The bundled versions from before OATS: 0.4.0 to 0.6.0 |
| `SchemaName` | The schema names, as a `Literal` |
| `validate(record, name, version=None)` | Raises `ValidationError` if `record` doesn't conform. Needs `[validate]` |
| `is_valid(record, name, version=None)` | `True` or `False`. Needs `[validate]` |
| `get_validator(name, version=None)` | The cached `jsonschema_rs` validator. Needs `[validate]` |
| `ValidationError` | A `ValueError` with `schema_name`, `schema_version`, `path` and `schema_path` |

- `version` must be exact: `"0.8.0"`, never `"0.8"` or `"latest"`.
- A name that exists in no bundled version raises `ValueError`. A version that isn't bundled, or that lacks the schema, raises `LookupError`.
- Without the `validate` extra, the validation functions raise `ImportError`, naming the extra to install.

## Versions

**Schema versions.** Before OATS 1.0, a new release may reject records an older one accepted, or the reverse, so pass the version a record was written for:

```python
oatschema.is_valid({"labels": []}, "classification", version="0.7.0")  # False: 0.7.0 needed a vocabulary or producer
oatschema.is_valid({"labels": []}, "classification")  # True: from 0.8.0 an empty list means "checked, found nothing"
```

- From 0.7.0, each `$id` includes its version (`https://oatschema.org/0.8.0/classification`), so several versions can share one registry.
- Each schema states its own version: in `x-version` from 0.8.0, and in `version` up to 0.7.0.
- **Legacy versions.** 0.4.0 to 0.6.0 predate OATS and are listed in `LEGACY_VERSIONS`. All three use the same `$id` for each schema (`https://dorsalhub.com/schemas/open/<name>`). Build each validator from a single schema, and never load two of these versions into one registry.

**Package versions.** The package version is `a.b.c.d`:

- `a.b.c` is the newest bundled schema version, equal to `SCHEMA_VERSION`;
- `d` is the package's own revision, for fixes and features that add no schema version. It is left out when it is 0, so `0.8.0` is followed by `0.8.0.1`, `0.8.0.2`, and so on.

Upgrading never removes a schema version, but it does move the default `SCHEMA_VERSION`. Before 1.0, pin with `oatschema~=0.8.0`, because a new schema minor version may change which records are valid. From 1.0, use `oatschema>=1.3,<2`.

## Contributing

Schema changes belong in [oatschema/oats](https://github.com/oatschema/oats). For this package, see [CONTRIBUTING.md](https://github.com/oatschema/oatschema-python/blob/main/CONTRIBUTING.md). Commits need a DCO sign-off (`git commit -s`).

## License

Apache-2.0. Copyright [The OATS Authors](https://github.com/oatschema/oatschema-python/blob/main/AUTHORS).
