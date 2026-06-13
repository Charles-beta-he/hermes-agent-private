from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

WRAPPER = Path.home() / ".hermes" / "scripts" / "multi-model-smoke-daily-full-wrapper.py"


def load():
    spec = importlib.util.spec_from_file_location("multi_model_smoke_daily_full_wrapper", WRAPPER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_report(path: Path, decision: str = "skip_provider_health_degraded") -> Path:
    path.write_text(json.dumps({
        "report_id": "test_gate",
        "decisions": [
            {"job_id": "42baeebb646c", "decision": decision, "reason": "endpoint xiaomi classified tls_error", "endpoint": "xiaomi"}
        ],
    }, ensure_ascii=False))
    return path


def test_load_gate_decision_from_override(tmp_path, monkeypatch):
    mod = load()
    report = write_report(tmp_path / "gate.json", "skip_provider_health_degraded")
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(report))
    d = mod.load_gate_decision()
    assert d["decision"] == "skip_provider_health_degraded"
    assert d["gate_report_id"] == "test_gate"


def test_default_gate_path_points_to_cron_provider_health_gate():
    mod = load()
    assert "workflow-kernel-cron-provider-health-gate" in mod.DEFAULT_GATE_GLOB


def test_main_skips_and_does_not_call_original(tmp_path, monkeypatch, capsys):
    mod = load()
    report = write_report(tmp_path / "gate.json")
    ledger = tmp_path / "skips.jsonl"
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(report))
    monkeypatch.setenv("HERMES_CRON_HEALTH_SKIP_LEDGER", str(ledger))
    first = mod.should_skip_for_provider_health()
    second = mod.should_skip_for_provider_health()
    assert first["status"] == "provider_health_degraded_skip"
    assert first["model_generation_attempted"] is False
    assert first["external_side_effects"] is False
    assert second["deduped"] is True
    assert len(ledger.read_text().splitlines()) == 1


def test_main_does_not_call_original_when_gate_says_skip(tmp_path, monkeypatch, capsys):
    mod = load()
    report = write_report(tmp_path / "gate.json")
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(report))
    monkeypatch.setenv("HERMES_CRON_HEALTH_SKIP_LEDGER", str(tmp_path / "skips.jsonl"))
    monkeypatch.setenv("HERMES_CRON_WRAPPER_RECEIPT_LEDGER", str(tmp_path / "receipts.jsonl"))
    def boom():
        raise AssertionError("original smoke should not run")
    monkeypatch.setattr(mod, "run_original", boom)
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "provider_health_degraded_skip"


def test_main_runs_original_when_gate_allows(tmp_path, monkeypatch, capsys):
    mod = load()
    report = write_report(tmp_path / "gate.json", decision="retry_allowed_by_provider_health")
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(report))
    monkeypatch.setenv("HERMES_CRON_HEALTH_SKIP_LEDGER", str(tmp_path / "skips.jsonl"))
    monkeypatch.setenv("HERMES_CRON_WRAPPER_RECEIPT_LEDGER", str(tmp_path / "receipts.jsonl"))
    monkeypatch.setattr(mod, "run_original", lambda: {"status": "original_called"})
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "original_called"
