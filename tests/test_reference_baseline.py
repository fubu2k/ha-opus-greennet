"""Phase 8: the fixed reference environment stays explicit and consistent.

The fixed reference job pins one Home Assistant patch release and builds
hassfest from the same immutable home-assistant/core commit. The moving
latest-stable job and ``hassfest@master`` remain additional signals only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
REFERENCE_REQUIREMENTS = (ROOT / "requirements_test_reference.txt").read_text(
    encoding="utf-8"
)
TEST_REQUIREMENTS = (ROOT / "requirements_test.txt").read_text(encoding="utf-8")
HACS = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))

REFERENCE_HA_VERSION = "2026.10.2"
REFERENCE_HA_CORE_SHA = "8d7fd8b68677c2585be36ec86043308f403875b8"


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _requirement_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_reference_requirements_pin_one_patch_release():
    lines = _requirement_lines(REFERENCE_REQUIREMENTS)
    assert "-r requirements_test.txt" in lines
    assert f"homeassistant=={REFERENCE_HA_VERSION}" in lines
    assert sum(line.startswith("homeassistant") for line in lines) == 1


def test_shared_tool_pins_are_exact_and_compatible_with_the_reference():
    lines = _requirement_lines(TEST_REQUIREMENTS)
    for line in lines:
        if line.startswith("homeassistant"):
            minimum = re.fullmatch(r"homeassistant>=([\d.]+)", line)
            assert minimum, line
            assert _version(minimum.group(1)) <= _version(REFERENCE_HA_VERSION)
            assert minimum.group(1) == HACS["homeassistant"]
        else:
            assert re.fullmatch(r"[A-Za-z0-9_.-]+==[\w.]+", line), line


def test_workflow_uses_the_same_reference_release_and_core_commit():
    assert f'REFERENCE_HA_VERSION: "{REFERENCE_HA_VERSION}"' in WORKFLOW
    assert f'REFERENCE_HA_CORE_SHA: "{REFERENCE_HA_CORE_SHA}"' in WORKFLOW
    assert "requirements_test_reference.txt" in WORKFLOW
    assert "repository: home-assistant/core" in WORKFLOW
    assert "script/hassfest/docker/Dockerfile" in WORKFLOW


def _job(name: str) -> str:
    """Return the YAML text of one top-level job of the workflow."""
    match = re.search(
        rf"^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        WORKFLOW,
        re.M | re.S,
    )
    assert match, name
    return match.group(1)


def test_reference_jobs_use_a_pinned_runner_image():
    for job in ("reference", "validate-reference"):
        assert "    runs-on: ubuntu-24.04\n" in _job(job), job


def test_moving_signals_are_kept_separately():
    assert "    runs-on: ubuntu-latest\n" in _job("test")
    assert "home-assistant/actions/hassfest@master" in _job("validate")


def test_reference_job_records_environment_and_traffic():
    for evidence in (
        "python -VV",
        "pip freeze",
        "OPUS_TRAFFIC_BASELINE_JSON",
        "actions/upload-artifact@",
        "--junitxml=",
    ):
        assert evidence in WORKFLOW, evidence
