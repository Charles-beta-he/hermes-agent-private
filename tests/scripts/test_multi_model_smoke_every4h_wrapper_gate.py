from __future__ import annotations

import importlib.util
import json
from pathlib import Path

WRAPPER = Path.home() / ".hermes" / "scripts" / "multi-model-smoke-every4h-wrapper.py"


def load():
    spec = importlib.util.spec_from_file_location("multi_model_smoke_every4h_wrapper", WRAPPER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_report(path: Path, decision: str = "run_with_degraded_provider_exclusions") -> Path:
    path.write_text(json.dumps({
        "report_id": "test_gate",
        "decisions": [
            {"job_id": "2c14ca2a9bd2", "decision": decision, "reason": "multi-provider excludes degraded", "degraded_endpoints": ["xiaomi"]}
        ],
    }, ensure_ascii=False))
    return path


def test_script_args_exclude_xiaomi_models():
    mod = load()
    args, meta = mod.script_args_for_decision({"decision":"run_with_degraded_provider_exclusions", "degraded_endpoints":["xiaomi"]})
    assert "--skip-claude" in args
    assert "mimo-v2.5-pro" not in args
    assert "gpt-5.5" in args
    assert "deepseek-v4-pro" in args
    assert meta["degraded_endpoints_excluded"] == ["xiaomi"]


def test_script_args_preserve_base_when_no_gate():
    mod = load()
    args, meta = mod.script_args_for_decision(None)
    assert args == ["--skip-claude", "--timeout", "45"]
    assert meta["selected_models"] == []


def test_load_gate_decision_from_override(tmp_path, monkeypatch):
    mod = load()
    report = write_report(tmp_path / "gate.json")
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(report))
    d = mod.load_gate_decision()
    assert d["decision"] == "run_with_degraded_provider_exclusions"
    assert d["gate_report_id"] == "test_gate"


def test_default_gate_path_points_to_cron_provider_health_gate():
    mod = load()
    assert "workflow-kernel-cron-provider-health-gate" in mod.DEFAULT_GATE_GLOB


def test_main_plan_only_does_not_call_original(tmp_path, monkeypatch, capsys):
    mod = load()
    report = write_report(tmp_path / "gate.json")
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(report))
    monkeypatch.setenv("HERMES_MULTI_MODEL_SMOKE_EVERY4H_PLAN_ONLY", "1")
    monkeypatch.setenv("HERMES_CRON_WRAPPER_RECEIPT_LEDGER", str(tmp_path / "receipts.jsonl"))
    def boom(_args):
        raise AssertionError("original smoke should not run in plan-only mode")
    monkeypatch.setattr(mod, "run_original", boom)
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "provider_health_exclusion_plan"
    assert payload["model_generation_attempted"] is False
    assert payload["cron_state_mutated"] is False
    assert payload["degraded_endpoints_excluded"] == ["xiaomi"]
    assert "mimo-v2.5-pro" not in payload["script_args"]


def test_main_passes_exclusion_args_to_original(tmp_path, monkeypatch, capsys):
    mod = load()
    report = write_report(tmp_path / "gate.json")
    monkeypatch.setenv("HERMES_CRON_HEALTH_GATE_REPORT", str(report))
    monkeypatch.setenv("HERMES_CRON_WRAPPER_RECEIPT_LEDGER", str(tmp_path / "receipts.jsonl"))
    captured = {}
    def fake_run(args):
        captured["args"] = args
        return {"status":"fake_original"}
    monkeypatch.setattr(mod, "run_original", fake_run)
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "fake_original"
    assert "mimo-v2.5-pro" not in captured["args"]
    assert "gpt-5.5" in captured["args"]
    assert payload["provider_health_gate"]["degraded_endpoints_excluded"] == ["xiaomi"]
