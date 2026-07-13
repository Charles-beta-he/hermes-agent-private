from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@pytest.mark.parametrize("workflow_name", ["history-check.yml", "contributor-check.yml"])
def test_branch_sensitive_checks_use_project_pr_base(workflow_name: str):
    text = (WORKFLOWS / workflow_name).read_text(encoding="utf-8")

    assert "origin/main" not in text
    assert "github.base_ref || github.event.repository.default_branch" in text
    assert "git check-ref-format --branch" in text
    assert 'refs/remotes/origin/${BASE_REF}' in text
    assert 'refs/heads/${BASE_REF}' in text


def test_history_check_compares_against_resolved_remote_ref():
    text = (WORKFLOWS / "history-check.yml").read_text(encoding="utf-8")

    assert 'git merge-base "$BASE_REMOTE_REF" HEAD' in text
    assert "Common ancestor with ${BASE_REF}" in text


def test_contributor_check_compares_against_resolved_remote_ref():
    text = (WORKFLOWS / "contributor-check.yml").read_text(encoding="utf-8")

    assert 'MERGE_BASE=$(git merge-base "$BASE_REMOTE_REF" HEAD)' in text
