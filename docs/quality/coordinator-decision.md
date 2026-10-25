# Coordinator and library boundary: decision

REST series, phase 7. This page records why the integration keeps its own
`OpusGreenNetCoordinator` and does not move to Home Assistant's
`DataUpdateCoordinator`, and where the boundary between transport and Home
Assistant runs today.

Labels: **B** = verified in code or by a test, **E** = design decision,
**A** = open assumption or missing proof.

## Decision (E)

1. **Keep the existing coordinator and dispatcher.** No evidence shows a defect
   that a base class would fix.
2. **No thin push `DataUpdateCoordinator` facade and no larger migration.** No
   gain in network load or correctness is shown, but the risk is real
   (fan-out, equality, events, startup order; see the table).
3. **A transport library boundary exists at module level and is tested.** A
   separate package, `py.typed` or a source distribution stays out of scope.

Rejecting the migration is a valid outcome of the evaluation. It can be
reopened if one of the reasons below stops being true.

## Why not `DataUpdateCoordinator`

| Criterion | Existing coordinator | Push `DataUpdateCoordinator` facade |
|---|---|---|
| Central state | `devices` holds one `EnOceanDevice` per gateway device (B) | State stays there; the facade would need `data` as a second reference |
| Distribution | One dispatcher signal per device; an entity only wakes for its own device (B, `_dispatch_state`) | One listener set per coordinator: every entity is notified on every update, unless one coordinator is created per device (A: not checked against the Home Assistant source) |
| Equality / `always_update=False` | Not needed. Writes are suppressed when the visible projection is unchanged (B, `entity.py`) | Devices are changed in place; comparing an object with itself would suppress real updates, and `always_update=True` removes the benefit |
| Button events | Own handler; press and release stay separate occurrences (B, `event.py`) | State equality must never suppress an event, so the side channel would stay |
| Startup | Stream first: the first device list resolves a future during setup (B) | `async_config_entry_first_refresh` does not fit a stream-first setup without rework |
| Availability | Gateway availability and stream state are separate (B) | `last_update_success` does not express that |
| Network load | Unchanged | Unchanged: the transport is the same stream |
| Effort and risk | None | The coordinator is about 1,900 lines; every platform, lifecycle and race test would be touched |

## Library boundary (B)

- `api.py` imports only the standard library, `aiohttp` and `wire.py`;
  `wire.py` imports only the standard library. The session is injected and never
  closed by the client. Tests: `test_client_modules_import_no_home_assistant_or_integration_code`,
  `test_integration_never_creates_its_own_client_session`,
  `test_client_never_closes_the_injected_session`.
- `const.py`, `enocean_device.py` and `protocol.py` import no Home Assistant
  code either. They hold the gateway document knowledge (EEP tables, state
  model, feedback fields), not transport. Test:
  `test_domain_modules_import_no_home_assistant`.
- The package `__init__.py` imports Home Assistant. The modules are free of it
  as files, not as an importable package. A real library would need its own
  package (A: not planned).
- `OpusGreenNetConfigEntry = ConfigEntry[OpusGreenNetRuntimeData]` is used by
  setup, platforms and diagnostics. `config_flow.py` does not touch a config
  entry and needs no alias. There is no `cast` and no `type: ignore` in the
  integration code.

## Open (A)

- The behaviour of the Home Assistant base class with many listeners was not
  checked against its source, because the decision rests on the other reasons.
