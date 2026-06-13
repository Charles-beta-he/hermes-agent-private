from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_external_dispatch_policy_gate.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_external_dispatch_policy_gate", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def candidate(tmp_path: Path) -> Path:
    payload = {
        "status": "EXTERNAL_DISPATCH_EXPANSION_CANDIDATE_READY",
        "report_id": "candidate",
        "candidate_tracks": [
            {"track": "P47A_LOCAL_OUTBOX_ONLY"},
            {"track": "P47C_GUARDED_SINGLE_SEND"},
        ],
        "adapter_options": [
            {"adapter": "local_outbox_simulator", "external_side_effects": False},
            {"adapter": "telegram", "external_side_effects": True},
        ],
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload))
    return path


def test_default_local_outbox_policy_gate_is_fail_closed(tmp_path):
    mod = load()
    report = mod.build_policy_gate(candidate_path=candidate(tmp_path))
    assert report["status"] == "EXTERNAL_DISPATCH_POLICY_GATE_LOCAL_OUTBOX_RECORDED"
    assert report["selected_track"] == "P47A_LOCAL_OUTBOX_ONLY"
    assert report["selected_adapter"] == "local_outbox_simulator"
    assert report["receipt"]["external_side_effects_executed"] is False
    assert report["receipt"]["unattended_external_dispatch_enabled"] is False
    assert report["outbox_item"]["delivery_status"] == "local_outbox_recorded_not_dispatched"
    assert "no send_message call" in report["safe_boundaries"]


def test_rejects_external_adapter_without_exact_gate(tmp_path):
    mod = load()
    try:
        mod.build_policy_gate(candidate_path=candidate(tmp_path), track="P47C_GUARDED_SINGLE_SEND", adapter="telegram")
    except SystemExit as exc:
        assert "external-side-effect adapters" in str(exc)
    else:
        raise AssertionError("expected external adapter to be rejected")


def test_write_report_creates_outbox_and_receipt(tmp_path):
    mod = load()
    report = mod.build_policy_gate(candidate_path=candidate(tmp_path), payload="preview only")
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["outbox"]).exists()
    assert Path(artifacts["receipt"]).exists()
    saved = json.loads(Path(artifacts["json"]).read_text())
    assert saved["receipt_artifact"]["sha256"]
