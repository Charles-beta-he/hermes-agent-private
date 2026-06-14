from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_unattended_dispatch_policy_gate.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_unattended_dispatch_policy_gate", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    return path


def fixtures(tmp_path: Path):
    phase50 = write(tmp_path / "phase50.json", {
        "status": "GUARDED_EXTERNAL_SEND_VERIFIED",
        "adapter": "telegram",
        "target": {"type": "telegram_home_channel"},
        "send_result": {"chat_id": "5524172309", "message_id": "4261"},
    })
    phase52 = write(tmp_path / "phase52.json", {"status": "CONTROL_TOWER_UI_API_PROJECTION_READY"})
    return phase50, phase52


def test_policy_gate_ready_but_disabled(tmp_path):
    mod = load()
    phase50, phase52 = fixtures(tmp_path)
    report = mod.build_policy(phase50_path=phase50, phase52_path=phase52)
    assert report["status"] == "UNATTENDED_DISPATCH_POLICY_GATE_READY"
    assert report["unattended_dispatch_enabled"] is False
    assert report["external_side_effects_executed"] is False
    assert report["default_mode"] == "dry_run_preview"
    assert report["policy"]["rate_limits"]["per_target_per_hour"] == 1
    assert "dry_run_preview_required" in report["policy"]


def test_policy_gate_blocks_without_phase50(tmp_path):
    mod = load()
    phase50, phase52 = fixtures(tmp_path)
    payload = json.loads(phase50.read_text())
    payload["status"] = "GUARDED_EXTERNAL_SEND_BLOCKED"
    phase50.write_text(json.dumps(payload))
    report = mod.build_policy(phase50_path=phase50, phase52_path=phase52)
    assert report["status"] == "UNATTENDED_DISPATCH_POLICY_GATE_BLOCKED"
    assert "phase50_guarded_send_not_verified" in report["blockers"]


def test_dry_run_instruction_never_sends(tmp_path):
    mod = load()
    phase50, phase52 = fixtures(tmp_path)
    report = mod.build_policy(phase50_path=phase50, phase52_path=phase52)
    instruction = mod.build_dry_run_instruction(report)
    assert instruction["mode"] == "dry_run_preview"
    assert instruction["would_send"] is False
    assert instruction["requires_human_review_before_apply"] is True
    assert instruction["chat_id"] == "5524172309"


def test_write_report_creates_policy_and_instruction(tmp_path):
    mod = load()
    phase50, phase52 = fixtures(tmp_path)
    report = mod.build_policy(phase50_path=phase50, phase52_path=phase52)
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["dry_run_instruction"]).exists()
    assert "Unattended Dispatch Policy" in Path(artifacts["md"]).read_text()
