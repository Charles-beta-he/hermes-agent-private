from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load():
    p = Path(__file__).resolve().parents[2] / "scripts" / "provider_cron_health_gate.py"
    spec = importlib.util.spec_from_file_location("provider_cron_health_gate", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def probe_report(**classes: str) -> dict:
    return {
        "status": "DEGRADED_PARTIAL",
        "results": [{"id": k, "classification": v} for k, v in classes.items()],
    }


def test_skips_provider_generation_retry_when_mapped_endpoint_degraded(tmp_path):
    mod = load()
    jobs = tmp_path / "jobs.json"
    probe = tmp_path / "probe.json"
    write_json(jobs, {"jobs": [
        {"id": "a", "name": "xiaomi generation", "last_status": "error", "last_error": "RuntimeError: Connection error.", "provider": "xiaomi"},
        {"id": "ok", "name": "ok", "last_status": "ok"},
    ]})
    write_json(probe, probe_report(xiaomi="tls_error", github="reachable", telegram="reachable"))
    report = mod.build_report(jobs, probe)
    assert report["status"] == "DEGRADED_GATED"
    decision = report["job_decisions"][0]
    assert decision["mapped_endpoint"] == "xiaomi"
    assert decision["gate_decision"] == "skip_provider_generation_retry"
    assert "no model generation call" in report["safe_boundaries"]


def test_reachable_provider_is_only_a_governed_retry_candidate(tmp_path):
    mod = load()
    jobs = tmp_path / "jobs.json"
    probe = tmp_path / "probe.json"
    write_json(jobs, {"jobs": [
        {"id": "oai", "name": "codex cron", "last_status": "error", "last_error": "RuntimeError: Connection error.", "model": "openai-codex/gpt"},
    ]})
    write_json(probe, probe_report(openai="reachable"))
    report = mod.build_report(jobs, probe)
    decision = report["job_decisions"][0]
    assert report["status"] == "READY_WITH_GUARDS"
    assert decision["gate_decision"] == "allow_single_governed_retry_candidate"
    assert "generation may still fail" in decision["typed_reason"]


def test_telegram_delivery_remains_external_send_blocked_even_when_reachable(tmp_path):
    mod = load()
    jobs = tmp_path / "jobs.json"
    probe = tmp_path / "probe.json"
    write_json(jobs, {"jobs": [
        {"id": "tg", "name": "daily", "last_status": "error", "last_delivery_error": "Telegram send failed: ProxyError"},
    ]})
    write_json(probe, probe_report(telegram="reachable"))
    report = mod.build_report(jobs, probe)
    decision = report["job_decisions"][0]
    assert decision["gate_decision"] == "manual_governed_notification_retry_candidate"
    assert "unattended/external sends remain blocked" in decision["typed_reason"]
    assert "no external message send" in report["safe_boundaries"]


def test_write_report_is_local_evidence_only(tmp_path):
    mod = load()
    report = {
        "status": "DEGRADED_GATED",
        "probe_status": "DEGRADED_PARTIAL",
        "failed_count": 1,
        "gate_counts": {"skip_provider_generation_retry": 1},
        "job_decisions": [
            {"job_id": "a", "name": "provider", "gate_decision": "skip_provider_generation_retry", "failure_class": "provider_generation", "mapped_endpoint": "xiaomi", "probe_classification": "tls_error", "typed_reason": "avoid burning retries"},
        ],
        "safe_boundaries": ["no cron schedule mutation", "no external message send"],
        "next_actions": ["dry-run preflight first"],
    }
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert len(artifacts["sha256"]) == 64
    written = json.loads(Path(artifacts["json"]).read_text())
    assert written["status"] == "DEGRADED_GATED"
