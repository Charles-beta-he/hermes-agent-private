from __future__ import annotations

import importlib.resources
import json
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_NAME = "workflow-run-event-v1.schema.json"


def test_jsonschema_is_an_exact_pinned_core_dependency() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "jsonschema==4.26.0" in metadata["project"]["dependencies"]


def test_packaged_workflow_run_event_schema_matches_generated_schema() -> None:
    packaged = importlib.resources.files("workflow_kernel.schemas").joinpath(SCHEMA_NAME).read_bytes()
    generated = (REPO_ROOT / "schemas" / SCHEMA_NAME).read_bytes()

    assert packaged == generated
    assert json.loads(packaged)["$id"] == "https://hermes-agent.local/schemas/workflow-run-event-v1.schema.json"


def test_schema_is_declared_for_wheel_and_sdist() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = metadata["tool"]["setuptools"]["package-data"]
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "schemas/*.json" in package_data.get("workflow_kernel", [])
    assert "recursive-include workflow_kernel/schemas *.json" in manifest
