# Changelog

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
