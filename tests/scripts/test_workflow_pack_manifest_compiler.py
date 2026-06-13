from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_pack_manifest_compiler.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_pack_manifest_compiler", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pack_manifests_are_ready_and_fail_closed():
    mod = load()
    report = mod.build_manifests()
    assert report["status"] == "WORKFLOW_AGENT_PACK_MANIFESTS_READY"
    assert report["validation_errors"] == []
    wf = report["workflow_pack"]
    agent = report["agent_pack"]
    assert wf["pack_id"] == "workflow_kernel_external_dispatch_governance"
    assert agent["backing_workflow_pack_id"] == wf["pack_id"]
    assert agent["default_mode"] == "local_outbox_only"
    assert agent["side_effect_policy"]["external_side_effects_enabled"] is False
    assert agent["side_effect_policy"]["unattended_external_dispatch_enabled"] is False
    assert wf["runtime_flags"]["external_side_effects_executed"] is False


def test_workflow_modules_declare_boundaries():
    mod = load()
    report = mod.build_manifests()
    modules = {m["module_id"]: m for m in report["workflow_pack"]["modules"]}
    assert "workflow_kernel.external_dispatch_local_outbox.v1" in modules
    for module in modules.values():
        assert module["human_gate"]
        assert module["side_effects"]["credential_access"] is False
        assert module["side_effects"]["external_dispatch"] is False
    assert "external_send_requires_exact_adapter_target_payload_mode" in report["workflow_pack"]["authority_boundaries"]


def test_validators_detect_missing_required_fields():
    mod = load()
    assert "workflow_pack_missing_pack_id" in mod.validate_workflow_pack({"schema_version": "1.0.0"})
    assert "agent_pack_missing_agent_pack_id" in mod.validate_agent_pack({"schema_version": "1.0.0"})


def test_write_report_creates_pack_files(tmp_path):
    mod = load()
    report = mod.build_manifests()
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["workflow_pack"]).exists()
    assert Path(artifacts["agent_pack"]).exists()
    assert Path(artifacts["md"]).read_text().startswith("# Workflow Kernel")
