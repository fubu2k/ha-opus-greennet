"""Workflow hygiene that is cheap to keep: pinned actions, minimal rights."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
# The moving hassfest action is a deliberate extra signal next to the pinned
# reference job (see test_quality_scale.py::test_ci_enforces_the_promised_gates).
MOVING_ON_PURPOSE = {"home-assistant/actions/hassfest@master"}


def _uses(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", text, re.MULTILINE)


def test_workflows_exist() -> None:
    assert {path.name for path in WORKFLOWS} >= {"tests.yml", "release-asset.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_actions_are_pinned_to_commit_hashes(path: Path) -> None:
    references = _uses(path)
    assert references
    for reference in references:
        if reference in MOVING_ON_PURPOSE:
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", reference), reference


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_workflows_default_to_read_only_and_keep_no_checkout_credentials(
    path: Path,
) -> None:
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE)
    # Every checkout step says explicitly that its token is not persisted.
    checkouts = re.findall(r"uses: actions/checkout@[^\n]*\n((?:\s+[^\n]+\n)*)", text)
    assert checkouts
    for step in checkouts:
        assert "persist-credentials: false" in step.split("\n\n")[0], step


def test_only_the_release_upload_job_can_write() -> None:
    for path in WORKFLOWS:
        writes = "contents: write" in path.read_text(encoding="utf-8")
        assert writes == (path.name == "release-asset.yml"), path.name
    release = (ROOT / ".github/workflows/release-asset.yml").read_text(encoding="utf-8")
    # The grant sits on the job, not on the workflow.
    assert re.search(r"^    permissions:\n      contents: write\n", release, re.M)


def test_dependabot_keeps_the_pinned_actions_current() -> None:
    config = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert "package-ecosystem: github-actions" in config
