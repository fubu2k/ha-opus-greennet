# Phase 2: JSON boundary robustness

Scope: `custom_components/opus_greennet/wire.py`. Branch is based on
`phase-1-http-client-hardening`.

## Finding

The typed boundary in `wire.py` already checks container and field types and
never reinterprets values, so a full schema layer is not needed. One gap
remained: `json.loads` raises `RecursionError` for extremely deep nesting.
`api.py` and the coordinator only map `ValueError`, so such a document would
surface as an unhandled exception instead of a controlled gateway error.

## Changes

| Change | Reason | OWASP API Top 10 |
|---|---|---|
| `parse_json` converts `RecursionError` to `ValueError` | Hostile nesting follows the normal invalid-JSON path (stream reconnect with backoff, failed request). | API4, API8 |
| `bounded_object_items` and `MAX_LIST_ITEMS` | Opt-in cap for entries read from a gateway list. Not wired into callers yet. | API4 |

## Not changed

- NaN and Infinity are still accepted by `parse_json`; domain parsers reject them per field, and existing tests rely on that.
- Value semantics are untouched.

## Open for the next iteration

- Use `bounded_object_items` where device lists and function lists are read (`protocol.py`, `coordinator.py`); both files have not been reviewed yet.
- Decide on an inbound `device_id` format check after confirming which identifiers the gateway really sends.

## Verification

Prepared without running the suite. Run `ruff`, `mypy`, `pytest` and the coverage gate in CI. The deep-nesting test builds a 200,000-level document; if the runner is slow, reduce the depth (the interpreter's recursion limit is far lower).
