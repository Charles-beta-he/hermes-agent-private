from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "typecheck.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_workflow_contracts_typecheck_is_in_ci_matrix() -> None:
    packages = _workflow()["jobs"]["typecheck"]["strategy"]["matrix"]["package"]

    assert "apps/workflow-contracts" in packages


def test_workflow_contracts_schema_drift_test_runs_in_ci() -> None:
    steps = _workflow()["jobs"]["workflow-contracts-test"]["steps"]
    commands = [step.get("run") for step in steps if isinstance(step, dict)]

    assert "npm test --workspace @hermes/workflow-contracts" in commands
