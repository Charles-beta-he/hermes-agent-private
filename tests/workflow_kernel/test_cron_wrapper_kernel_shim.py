from __future__ import annotations

import json
from pathlib import Path

from workflow_kernel import cron_wrappers as cw


def write_gate(path: Path, job_id: str, decision: str, extra: dict | None = None) -> Path:
    payload = {"report_id": "kernel_test_gate", "decisions": [{"job_id": job_id, "decision": decision, **(extra or {})}]}
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


def test_load_gate_decision_uses_override(tmp_path, monkeypatch):
    gate = write_gate(tmp_path / "gate.json", cw.DAILY_FULL_JOB_ID, "skip_provider_health_degraded")
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(gate))
    decision = cw.load_gate_decision(cw.DAILY_FULL_JOB_ID)
    assert decision is not None
    assert decision["decision"] == "skip_provider_health_degraded"
    assert decision["gate_report_id"] == "kernel_test_gate"


def test_daily_skip_payload_is_typed_and_kernel_marked(tmp_path, monkeypatch):
    gate = write_gate(tmp_path / "gate.json", cw.DAILY_FULL_JOB_ID, "skip_provider_health_degraded")
    ledger = tmp_path / "skips.jsonl"
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(gate))
    monkeypatch.setenv("HERMES_CRON_HEALTH_SKIP_LEDGER", str(ledger))
    first = cw.daily_should_skip_for_provider_health()
    second = cw.daily_should_skip_for_provider_health()
    assert first is not None
    assert second is not None
    assert first["status"] == "provider_health_degraded_skip"
    assert first["kernel_shim"] is True
    assert first["model_generation_attempted"] is False
    assert first["cron_state_mutated"] is False
    assert second["deduped"] is True
    assert len(ledger.read_text().splitlines()) == 1


def test_every4h_excludes_degraded_provider_models():
    args, meta = cw.every4h_script_args_for_decision({"decision": "run_with_degraded_provider_exclusions", "degraded_endpoints": ["xiaomi"]})
    assert meta["kernel_shim"] is True
    assert meta["degraded_endpoints_excluded"] == ["xiaomi"]
    assert "mimo-v2.5-pro" not in args
    assert "gpt-5.5" in args
    assert "deepseek-v4-pro" in args


def test_every4h_base_args_when_no_degraded_provider():
    args, meta = cw.every4h_script_args_for_decision(None)
    assert args == cw.EVERY4H_BASE_ARGS
    assert meta["selected_models"] == []
    assert meta["kernel_shim"] is True
