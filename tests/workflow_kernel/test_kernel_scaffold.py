from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path


def test_kernel_package_exports_phase26_contract():
    wk = importlib.import_module("workflow_kernel")
    assert wk.PHASE == "26"
    assert wk.RUNTIME_SWITCH_ENABLED is False
    assert "kernel_scaffold_compat_only" in wk.CAPABILITIES


def test_json_io_writes_canonical_json_with_sha(tmp_path):
    io = importlib.import_module("workflow_kernel.io")
    payload = {"b": 2, "a": 1}
    result = io.write_json_artifact(tmp_path / "artifact.json", payload)
    written = (tmp_path / "artifact.json").read_bytes()
    assert written == b'{\n  "a": 1,\n  "b": 2\n}\n'
    assert result["sha256"] == hashlib.sha256(written).hexdigest()
    assert result["path"].endswith("artifact.json")
    assert io.read_json_artifact(tmp_path / "artifact.json") == {"a": 1, "b": 2}


def test_json_io_missing_artifact_is_typed_not_exception(tmp_path):
    io = importlib.import_module("workflow_kernel.io")
    missing = io.read_json_artifact(tmp_path / "missing.json", missing_ok=True)
    assert missing == {"missing": True, "path": str(tmp_path / "missing.json")}


def test_phase26_scripts_can_use_kernel_io_without_runtime_switch():
    migration = Path("scripts/workflow_architecture_migration_evaluation.py").read_text()
    health_gate = Path("scripts/cron_provider_health_gate.py").read_text()
    assert "workflow_kernel.io" in migration
    assert "workflow_kernel.io" in health_gate
