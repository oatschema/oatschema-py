# Changelog

## 0.9.0.0 (2026-11-06)

Bundles OATS 0.9.0, so `SCHEMA_VERSION` is now `"0.9.0"` and the bundled versions are 0.4.0 to 0.9.0. Earlier versions are unchanged. See the [OATS changelog](https://github.com/oatschema/oats/blob/main/CHANGELOG.md) for what the schemas changed: most importantly, every number is bounded within ±(2^53−1), and `llm-output` `seed` is a string.

**Pin:** `oatschema[validate]>=0.9.0.0,<0.10`. A consumer pinned `<0.9` stays on 0.8.0.2 until it widens the pin, and should pass `version` explicitly if its records aren't written for 0.9.0.

### Breaking

- `validate`, `is_valid` and `iter_errors`, with no `version`, now check against 0.9.0.
- A `date-time` with a non-digit where a digit belongs, such as `-----11-06T10:00:00Z`, is now invalid in every schema version, unless `check_formats=False`. jsonschema-rs accepted some of `+ , - . /` there. oatschema now checks `date-time` itself, with the same rules as oatschema-js. Each timestamp in a record takes about 2 to 3 µs longer to check.
- A string or property name with an unpaired surrogate, such as `"\ud800"`, is now invalid in every schema version, even with `check_formats=False`, because RFC 7493 forbids them. Before, some such records raised `TypeError` and others were reported valid. The error's `path` locates it, and its `keyword` and `schema_path` are empty. For such a record, `iter_errors` reports only the surrogates. oatschema-js, and other JSON Schema validators, still accept them. Every record is scanned first, which adds about 1 µs to a small record and about 70 µs to a 1,536-float embedding.
- The `validate` extra now needs `jsonschema-rs>=0.58`, up from 0.38. Before 0.49, it accepted infinity (a literal such as `1e400`) in a bounded field, and before 0.58 it reported some non-JSON values as invalid rather than raising `TypeError`.

### Added

- `check_numbers(record, name, version=None, *, on_bad_number="drop", check_formats=True)` returns a `CheckedRecord(record, dropped, converted)`, for making a record's numbers safe before writing it. It works on every schema version and never rounds. Conversions keep the value exactly: numpy values to Python ones, `-0.0` to `0.0`, a whole float in an `integer` field to an int, and, from 0.9.0, an int `seed` to a string. A bad number (NaN, ±infinity, beyond ±(2^53−1), or outside its field's bounds) under a property is dropped, reported in `dropped` and with a `DroppedNumberWarning`. In a list, or in a required property, it rejects the record with `ValidationError`, as any bad number does with `on_bad_number="raise"`.
- New public types: `CheckedRecord`, `DroppedNumber`, `ConvertedNumber` and `DroppedNumberWarning`.
- `ValidationError`'s `schema_path` and `keyword` may be empty, for a check no schema keyword expresses. `str()` escapes unpaired surrogates in `path`, so it can always be printed.
- The sync script rejects a schema named `tools`, as OATS 0.9.0 does.

## 0.8.0.2 (2026-10-30)

No schema changes: the bundled schema versions are still 0.4.0 to 0.8.0. Nothing breaks: every call whose arguments have the documented types behaves as in 0.8.0.1.

- Arguments of the wrong type raise a `TypeError` that names the expected call, before any lookup, in `get_schema`, `get_validator`, `Validator`, `validate`, `is_valid` and `iter_errors`. Swapping the record and the schema name, as in `is_valid("classification", record)`, raises `TypeError: is_valid() takes the record first, then the schema name; expected is_valid(record, name, version=None, *, check_formats=True)`. Before, it raised `TypeError: unhashable type: 'dict'` from inside the package.
- The same goes for a schema name that isn't a str, a version that is neither a str nor `None`, a `check_formats` that isn't a bool, and a `get_schema` `mode` that isn't a str. Before, these raised `ValueError`, `LookupError` or an unrelated `TypeError`, depending on the value. Catch `TypeError` if you pass names or versions you haven't checked.
- A record that isn't JSON data, such as a set, an arbitrary object or a dict with non-str keys, raises `TypeError` from `validate`, `is_valid` and `iter_errors`, and from the `Validator` methods. Before, it raised jsonschema-rs's `ValueError` (`Unsupported type: 'set'`), which `except ValueError` mistook for an invalid record.
- A record that isn't a JSON object, such as a list or `None`, is still reported as invalid, not as a `TypeError`.
- `ValidationError` and `Validator` now say they belong to `oatschema`, not the private `oatschema._validation`: tracebacks end `oatschema.ValidationError: …`, and pickles refer to `oatschema.ValidationError`. Errors pickled by 0.8.0.1 still unpickle.
- The README describes when the API may break, and how to pin: `oatschema[validate]>=0.8.0.1,<0.9`. It no longer suggests `oatschema~=0.8.0`.

## 0.8.0.1 (2026-10-19)

No schema changes: the bundled schema versions are still 0.4.0 to 0.8.0.

**Breaking:** `get_validator` returns an `oatschema.Validator`, not a `jsonschema_rs.Validator`.

- `oatschema.Validator` has `validate(record)`, `is_valid(record)` and `iter_errors(record)`, and read-only `schema_name`, `schema_version` and `schema`. No jsonschema-rs type appears in the public API.
- New `iter_errors(record, name, version=None)` returns every error, as `ValidationError`s. `validate` still raises one error: the first it finds.
- New keyword-only `check_formats=True` on `validate`, `is_valid`, `iter_errors`, `get_validator` and `Validator`. Pass `False` to stop checking `format` (`date-time`, `uri`), as JSON Schema 2020-12 validators do by default. `Validator.check_formats` shows the setting. Any value other than `True` or `False` raises `TypeError`.
- Error messages are now short, whatever the size of the record. Values from the record appear as `…` (`… is longer than 128 characters`), and a message is at most about 1 KB. Before, a multi-megabyte string was copied into the message and the traceback, more than once.
- Failing records are faster to report: about 35 µs instead of about 300 µs for a record with a 200,000-character value.
- `ValidationError` no longer chains the jsonschema-rs error as `__cause__`, and keeps no reference to the failing value. It has a new `keyword` attribute, the last keyword in `schema_path` (for example `"maxLength"`). Message wording has changed.
- `ValidationError` can be pickled and copied, so it can cross process boundaries (`multiprocessing`, `concurrent.futures` process pools). In 0.8.0, unpickling or copying one raised `TypeError`.
- `validate` and `is_valid` are faster on small records, because the schema name and version are looked up once and cached.

Migrating:

- If you only call `is_valid(record)` or `validate(record)` on the result of `get_validator`, nothing changes, except that `validate` now raises `oatschema.ValidationError` instead of `jsonschema_rs.ValidationError`. Catch `oatschema.ValidationError`, or `ValueError`.
- Replace the jsonschema-rs validator's `iter_errors(record)` with `Validator.iter_errors(record)` or `oatschema.iter_errors(record, name, version)`. The errors it yields are `oatschema.ValidationError`s: use `path`, `schema_path`, `keyword` and `message` in place of `instance_path`, `schema_path`, `kind` and `message`. There is no `instance`; read the value from your record at `path`.
- If you read `error.__cause__` to get the jsonschema-rs error, use the attributes above instead.
- If you need jsonschema-rs's full API, build your own validator from `get_schema(name, version)` with `jsonschema_rs.validator_for(schema, validate_formats=True)`.

## 0.8.0 (2026-10-18)

First release.

- Bundles OATS schema versions 0.4.0, 0.5.0, 0.6.0, 0.7.0 and 0.8.0, copied unchanged from the `oatschema/oats` release tags. 0.8.0 has 12 schemas, including the new `ranking`. 0.4.0 to 0.6.0 are legacy versions with `dorsalhub.com` `$id`s.
- `get_schema`, `available_versions`, `SCHEMA_VERSION`, `LEGACY_VERSIONS` and `SchemaName`.
- `validate`, `is_valid`, `get_validator` and `ValidationError`, with the `validate` extra.
