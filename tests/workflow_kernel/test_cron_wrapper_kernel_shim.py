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


def test_wrapper_run_receipt_records_kernel_gate_metadata(tmp_path, monkeypatch):
    ledger = tmp_path / "receipts.jsonl"
    monkeypatch.setenv("HERMES_CRON_WRAPPER_RECEIPT_LEDGER", str(ledger))
    receipt = cw.record_wrapper_run_receipt(
        job_id=cw.EVERY4H_JOB_ID,
        job_name=cw.EVERY4H_JOB_NAME,
        payload={"status": "ok", "ok": True, "returncode": 0, "summary": {"status": "pass"}},
        gate_decision={"decision": "retry_allowed_by_provider_health", "gate_report": "/tmp/gate.json", "gate_report_id": "gate1"},
        runtime_mode="provider_exclusion_or_original_run",
    )
    assert receipt["kernel_shim"] is True
    assert receipt["gate_report_id"] == "gate1"
    assert receipt["payload_status"] == "ok"
    assert receipt["cron_state_mutated"] is False
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["runtime_mode"] == "provider_exclusion_or_original_run"
