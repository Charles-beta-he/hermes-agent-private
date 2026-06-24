from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_control_tower_projection.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_control_tower_projection", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    return path


def fixtures(tmp_path: Path):
    registry = write(tmp_path / "registry.json", {
        "status": "PACK_REGISTRY_PROJECTION_READY",
        "workflow_pack_id": "wf",
        "agent_pack_id": "agent.wf",
        "module_rows": [{"module_id": "m1", "risk_level": "read_only"}],
    })
    phase50 = write(tmp_path / "phase50.json", {
        "status": "GUARDED_EXTERNAL_SEND_VERIFIED",
        "adapter": "telegram",
        "target": {"type": "telegram_home_channel"},
        "external_side_effect_executed": True,
        "unattended_external_dispatch_enabled": False,
        "send_result": {"message_id": "4261", "success": True},
    })
    sequence = write(tmp_path / "sequence.json", {
        "phase_count": 77,
        "phases": [
            {"phase": 92, "name": "Publication Governance Terminal Summary", "next_unlocked": [93]},
            {"phase": 93, "name": "LoopRuntimeContract Adoption Gate", "gate": "loop_runtime_enabled=false; scheduler_enabled=false; worker_spawn_enabled=false; external_side_effects=false", "status": "loop_runtime_contract_dry_run_doctor_passed"},
        ],
    })
    readiness = write(tmp_path / "readiness.json", {
        "status": "RUNTIME_READINESS_SUMMARY_DEGRADED_NOT_BLOCKED",
        "checks": {"loop_runtime_contract_doctor_passed": True},
        "artifact_states": {
            "loop_runtime_contract": {
                "status": "PASS",
                "path": "/tmp/loop-runtime-contract.json",
                "exists": True,
            }
        },
    })
    candidate = write(tmp_path / "candidate_receipt.json", {
        "status": "PASS",
        "receipt_id": "loopdry_candidate",
        "contract_id": "hermes.workflow_kernel.control_tower_runtime_promotion",
        "dry_run_only": True,
        "loop_runtime_enabled": False,
        "scheduler_enabled": False,
        "worker_spawn_enabled": False,
        "taskpool_write_enabled": False,
        "mission_write_enabled": False,
        "external_side_effects": False,
    })
    authorization = write(tmp_path / "authorization_packet.json", {
        "packet_id": "authz.hermes.workflow_kernel.control_tower_runtime_promotion.v1",
        "status": "HUMAN_AUTHORIZATION_REQUIRED",
        "candidate": {
            "contract_id": "hermes.workflow_kernel.control_tower_runtime_promotion",
            "receipt_id": "loopdry_candidate",
            "doctor_status": "PASS",
        },
        "requested_authority": {
            "activate_control_tower_runtime_api": True,
            "enable_scheduler": False,
            "spawn_worker": False,
            "write_taskpool": False,
            "external_dispatch": False,
        },
        "human_decision_required": ["exact_executor_binding", "post_action_readback_surface"],
        "escape_hatch": {"kill_switch": "disable_loop_runtime_contract_promotion"},
    })
    return registry, phase50, sequence, readiness, candidate, authorization


def test_projection_ready(tmp_path):
    mod = load()
    registry, phase50, sequence, readiness, candidate, authorization = fixtures(tmp_path)
    report = mod.build_projection(registry_path=registry, phase50_path=phase50, sequence_path=sequence, readiness_path=readiness, candidate_receipt_path=candidate, authorization_packet_path=authorization)
    assert report["status"] == "CONTROL_TOWER_UI_API_PROJECTION_READY"
    assert report["blockers"] == []
    assert report["operator_summary"]["last_external_send"]["message_id"] == "4261"
    assert report["operator_summary"]["loop_runtime_contract"]["doctor_passed"] is True
    assert report["operator_summary"]["loop_runtime_contract"]["adoption_phase"] == 93
    assert report["operator_summary"]["loop_runtime_contract"]["runtime_promotion_enabled"] is False
    assert report["operator_summary"]["loop_runtime_candidate"]["contract_id"] == "hermes.workflow_kernel.control_tower_runtime_promotion"
    assert report["operator_summary"]["loop_runtime_candidate"]["doctor_passed"] is True
    assert report["operator_summary"]["loop_runtime_candidate"]["runtime_promotion_enabled"] is False
    assert report["operator_summary"]["runtime_promotion_authorization"]["status"] == "HUMAN_AUTHORIZATION_REQUIRED"
    assert report["operator_summary"]["runtime_promotion_authorization"]["activate_control_tower_runtime_api_requested"] is True
    assert report["operator_summary"]["runtime_promotion_authorization"]["scheduler_requested"] is False
    assert report["operator_summary"]["runtime_promotion_authorization"]["kill_switch"] == "disable_loop_runtime_contract_promotion"
    assert any(panel["id"] == "loop_runtime_contract" for panel in report["panels"])
    assert any(panel["id"] == "loop_runtime_candidate" for panel in report["panels"])
    assert any(panel["id"] == "runtime_promotion_authorization" for panel in report["panels"])
    assert "loop runtime contract projected read-only" in report["safe_boundaries"]
    assert "no additional external send" in report["safe_boundaries"]


def test_projection_blocks_without_phase50_receipt(tmp_path):
    mod = load()
    registry, phase50, sequence, readiness, candidate, authorization = fixtures(tmp_path)
    payload = json.loads(phase50.read_text())
    payload["status"] = "GUARDED_EXTERNAL_SEND_BLOCKED"
    phase50.write_text(json.dumps(payload))
    report = mod.build_projection(registry_path=registry, phase50_path=phase50, sequence_path=sequence)
    assert report["status"] == "CONTROL_TOWER_UI_API_PROJECTION_BLOCKED"
    assert "phase50_guarded_send_not_verified" in report["blockers"]


def test_write_report_creates_html(tmp_path):
    mod = load()
    registry, phase50, sequence, readiness, candidate, authorization = fixtures(tmp_path)
    report = mod.build_projection(registry_path=registry, phase50_path=phase50, sequence_path=sequence, readiness_path=readiness, candidate_receipt_path=candidate, authorization_packet_path=authorization)
    artifacts = mod.write_report(report, tmp_path / "out")
    html = Path(artifacts["html"]).read_text()
    assert "Workflow Kernel Control Tower Projection" in html
    assert "4261" in html
