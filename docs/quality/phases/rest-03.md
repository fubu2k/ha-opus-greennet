# REST phase 03 – fixed CI reference (Home Assistant 2026.10.2, hassfest, icons)

- **Status:** implemented on branch `feat/rest-only-ci-reference`, not yet
  merged. CI pending (section 4). No version change, no release.
- **Base:** `feature/rest-only` (v1.0.0b1). Independent of REST phases 01
  and 02.

## 1. Reason

The REST-only base tests only against the latest stable Home Assistant and
the moving hassfest image. A new Home Assistant release can therefore turn
CI red or green without any change in this repository, and the icon check
that `tests/test_icon_translations.py` refers to (`script/check_icons.py`)
was missing.

## 2. Changes

| File | Change |
|------|--------|
| `.github/workflows/tests.yml` | new job **Home Assistant 2026.10.2 (fixed reference)**: pinned install, `pip check`, ruff, strict mypy, tests with coverage, per-module coverage gate; new job **Hassfest at Home Assistant 2026.10.2 (fixed reference)**: hassfest built from home-assistant/core `8d7fd8b6`, MDI icon check; the existing jobs (latest stable, moving hassfest) stay |
| `requirements_test_reference.txt` | **new**: `requirements_test.txt` plus `homeassistant==2026.10.2` |
| `script/check_icons.py` | **new**: checks every MDI icon in `icons.json` against the catalogue of the pinned core commit (no Home Assistant import) |
| `tests/test_quality_scale.py` | `test_ci_pins_one_fixed_reference`: workflow, requirements and job names name the same version and commit |

The reference changes only in a reviewed phase, together in the workflow and
`requirements_test_reference.txt`.

## 3. Guardrails

No integration code changed: no request, interval, entity, unique ID or
registry change (A1, A2, A3, A5 unaffected).

## 4. Commands and results

| Command | Result |
|---------|--------|
| `ruff check .`, `ruff format --check .` | passed |
| workflow YAML parse, new test's assertions against the files | passed |

| Run | Head | Result |
|-----|------|--------|
| pending | – | – |
