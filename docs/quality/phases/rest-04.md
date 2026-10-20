# REST phase 04 – quality documents for the REST-only series

- **Status:** implemented on branch `docs/rest-only-quality`, PR #38, not yet
  merged. CI green (section 4). No code change, no version change, no release.
- **Base:** `feature/rest-only` `1b850f5` (REST phases 01–03 merged, CI run
  39494498982 green).

## 1. Scope

The quality documents describe only the REST-only integration. Documents the
upstream maintainer owns stay as they are, so that later updates from upstream
apply without conflicts: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, the payload
reference in the repository root, code, UI strings and tests. Their
references to versions before 1.0 describe the user migration and are
upstream content.

## 2. Changes

| File | Change |
|------|--------|
| `docs/quality/implementation-plan.md` | rewritten: goal (Platinum, REST only), repository target, guardrails A1, A2, A3, A5 with the frozen REST values, working rules, completed phases REST 01–04, planned phases REST 05–10, open questions O-2, O-3, O-4, O-6 |
| `docs/quality/protocol-contract.md` | introduction, request correlation, identities (section 8) and configuration entry (section 9) worded without the former transport; content unchanged |
| `docs/quality/quality-scale-evidence.md` | documentation pointers: README upgrading section, reauthentication test, CI workflow instead of removed phase reports; statuses unchanged |
| `docs/quality/phases/phase-01.md` … `phase-07.md` | removed (reports of the former transport; still in the git history and on branch `archive/dual-transport-main`) |
| `docs/quality/phases/rest-02.md` | CI result of REST phase 02 recorded (commit that missed the merge of PR #36) |

## 3. Guardrails

A1, A2, A3 and A5 are carried over with REST wording and the frozen values
of `feature/rest-only`. The former A4 (retained message protection) has no counterpart in
the REST API and is dropped. No code changed, so no traffic, entity or
identity changed.

## 4. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` | passed |
| search for references to the removed reports | none left outside `CHANGELOG.md` |

| Run | Head | Result |
|-----|------|--------|
| [39495545562](https://github.com/fubu2k/ha-opus-greennet/actions/runs/39495545562) | `247f437` | passed: HA 2026.10.2 (fixed reference), Hassfest at 2026.10.2, HA latest stable, moving hassfest |

## 5. Next step

Switch `main` to `feature/rest-only` through a pull request (no force push),
then REST phase 05 (measurement).
