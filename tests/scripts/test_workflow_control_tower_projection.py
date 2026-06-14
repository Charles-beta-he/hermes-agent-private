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
    sequence = write(tmp_path / "sequence.json", {"phase_count": 34})
    return registry, phase50, sequence


def test_projection_ready(tmp_path):
    mod = load()
    registry, phase50, sequence = fixtures(tmp_path)
    report = mod.build_projection(registry_path=registry, phase50_path=phase50, sequence_path=sequence)
    assert report["status"] == "CONTROL_TOWER_UI_API_PROJECTION_READY"
    assert report["blockers"] == []
    assert report["operator_summary"]["last_external_send"]["message_id"] == "4261"
    assert "no additional external send" in report["safe_boundaries"]


def test_projection_blocks_without_phase50_receipt(tmp_path):
    mod = load()
    registry, phase50, sequence = fixtures(tmp_path)
    payload = json.loads(phase50.read_text())
    payload["status"] = "GUARDED_EXTERNAL_SEND_BLOCKED"
    phase50.write_text(json.dumps(payload))
    report = mod.build_projection(registry_path=registry, phase50_path=phase50, sequence_path=sequence)
    assert report["status"] == "CONTROL_TOWER_UI_API_PROJECTION_BLOCKED"
    assert "phase50_guarded_send_not_verified" in report["blockers"]


def test_write_report_creates_html(tmp_path):
    mod = load()
    registry, phase50, sequence = fixtures(tmp_path)
    report = mod.build_projection(registry_path=registry, phase50_path=phase50, sequence_path=sequence)
    artifacts = mod.write_report(report, tmp_path / "out")
    html = Path(artifacts["html"]).read_text()
    assert "Workflow Kernel Control Tower Projection" in html
    assert "4261" in html
