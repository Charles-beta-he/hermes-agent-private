from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_pack_registry_projection.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_pack_registry_projection", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    return path


def fixtures(tmp_path: Path):
    workflow = write(tmp_path / "workflow.json", {
        "pack_id": "wf",
        "pack_version": "v1",
        "modules": [
            {
                "module_id": "m1",
                "layer": "read",
                "entrypoint": "x.py",
                "risk_level": "read_only",
                "human_gate": "none",
                "side_effects": {"external_dispatch": False, "credential_access": False, "cron_mutation": False, "model_generation": False},
            },
            {
                "module_id": "m2",
                "layer": "local",
                "entrypoint": "y.py",
                "risk_level": "local_write",
                "human_gate": "local_only",
                "side_effects": {"external_dispatch": False, "credential_access": False, "cron_mutation": False, "model_generation": False},
            },
        ],
    })
    agent = write(tmp_path / "agent.json", {
        "agent_pack_id": "agent.wf",
        "agent_version": "v1",
        "side_effect_policy": {
            "external_side_effects_enabled": False,
            "unattended_external_dispatch_enabled": False,
            "credential_access_enabled": False,
        },
    })
    phase49 = write(tmp_path / "phase49.json", {"status": "WORKFLOW_AGENT_PACK_MANIFESTS_READY"})
    return workflow, agent, phase49


def test_registry_projection_ready_and_read_only(tmp_path):
    mod = load()
    workflow, agent, phase49 = fixtures(tmp_path)
    report = mod.build_registry(workflow_pack_path=workflow, agent_pack_path=agent, phase49_path=phase49)
    assert report["status"] == "PACK_REGISTRY_PROJECTION_READY"
    assert report["classification"]["module_count"] == 2
    assert report["classification"]["local_write_module_count"] == 1
    assert report["classification"]["blockers"] == []
    assert "no external message send" in report["safe_boundaries"]
    assert report["guarded_single_send_requirements"]["current_status"] == "not_selected"


def test_registry_blocks_unexpected_external_flags(tmp_path):
    mod = load()
    workflow, agent, phase49 = fixtures(tmp_path)
    agent_payload = json.loads(agent.read_text())
    agent_payload["side_effect_policy"]["external_side_effects_enabled"] = True
    agent.write_text(json.dumps(agent_payload))
    report = mod.build_registry(workflow_pack_path=workflow, agent_pack_path=agent, phase49_path=phase49)
    assert report["status"] == "PACK_REGISTRY_PROJECTION_BLOCKED"
    assert "agent_external_side_effects_enabled_unexpected" in report["classification"]["blockers"]


def test_write_report_creates_markdown(tmp_path):
    mod = load()
    workflow, agent, phase49 = fixtures(tmp_path)
    report = mod.build_registry(workflow_pack_path=workflow, agent_pack_path=agent, phase49_path=phase49)
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert "Phase50 guarded send requirements" in Path(artifacts["md"]).read_text()
