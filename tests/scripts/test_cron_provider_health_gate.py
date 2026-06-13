from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load():
    p = Path(__file__).resolve().parents[2] / "scripts" / "cron_provider_health_gate.py"
    spec = importlib.util.spec_from_file_location("cron_provider_health_gate", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def test_infer_endpoint_from_provider_model_deliver_and_name():
    mod = load()
    assert mod.infer_endpoint({"provider":"xiaomi"}) == "xiaomi"
    assert mod.infer_endpoint({"model":"mimo-v2.5-pro"}) == "xiaomi"
    assert mod.infer_endpoint({"deliver":"telegram:he cheng"}) == "telegram"
    assert mod.infer_endpoint({"name":"hermes-upstream-ratchet-review"}) == "github"
    assert mod.infer_endpoint({"name":"unknown"}) is None


def test_decide_degraded_pinned_provider_skips_only_target_provider():
    mod = load()
    statuses = {
        "xiaomi": {"id":"xiaomi", "classification":"tls_error"},
        "openai": {"id":"openai", "classification":"reachable"},
    }
    decision = mod.decide_job({"id":"j1", "name":"mimo", "provider":"xiaomi", "last_status":"error"}, {"primary_class":"provider_generation"}, statuses)
    assert decision["decision"] == "skip_provider_health_degraded"
    assert decision["target_endpoint"] == "xiaomi"
    assert "tls_error" in decision["reason"]


def test_unpinned_provider_job_excludes_degraded_endpoints():
    mod = load()
    statuses = {"xiaomi": {"id":"xiaomi", "classification":"tls_error"}, "openai": {"id":"openai", "classification":"reachable"}}
    decision = mod.decide_job({"id":"j2", "name":"untyped", "last_status":"error"}, {"primary_class":"provider_generation"}, statuses)
    assert decision["decision"] == "run_with_degraded_provider_exclusions"
    assert "xiaomi" in decision["reason"]


def test_telegram_reachable_still_requires_governed_manual_retry():
    mod = load()
    statuses = {"telegram": {"id":"telegram", "classification":"reachable"}}
    decision = mod.decide_job({"id":"tg", "name":"daily", "provider":"openai-codex", "deliver":"telegram:dm", "last_status":"error"}, {"primary_class":"telegram_delivery"}, statuses)
    assert decision["target_endpoint"] == "telegram"
    assert decision["decision"] == "retry_allowed_by_provider_health_manual_only"
    assert "governed notification ledger" in decision["reason"]


def test_build_and_write_report(tmp_path):
    mod = load()
    jobs = {"jobs": [
        {"id":"ok", "name":"ok", "last_status":"ok"},
        {"id":"a", "name":"mimo", "provider":"xiaomi", "last_status":"error"},
        {"id":"b", "name":"generic", "last_status":"error"},
    ]}
    probe = {"results": [{"id":"xiaomi", "classification":"tls_error"}, {"id":"openai", "classification":"reachable"}]}
    triage = {"failed_jobs": [{"job_id":"a", "primary_class":"provider_generation"}, {"job_id":"b", "primary_class":"provider_generation"}]}
    jobs_path = write_json(tmp_path / "jobs.json", jobs)
    probe_path = write_json(tmp_path / "probe.json", probe)
    triage_path = write_json(tmp_path / "triage.json", triage)
    report = mod.build_report(jobs_path, probe_path, triage_path)
    assert report["failed_job_count"] == 2
    assert report["decision_counts"]["skip_provider_health_degraded"] == 1
    assert report["decision_counts"]["run_with_degraded_provider_exclusions"] == 1
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert len(artifacts["sha256"]) == 64
