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


def test_provider_endpoint_mapping_and_inference():
    mod = load()
    assert mod.provider_endpoint("xiaomi") == "xiaomi"
    assert mod.provider_endpoint("openai-codex") == "openai"
    assert mod.provider_endpoint("google-gemini-cli") == "google_gemini"
    assert mod.infer_provider({"provider":"xiaomi"}) == "xiaomi"
    assert mod.infer_provider({"model":"mimo-v2.5-pro"}) == "xiaomi"
    assert mod.infer_provider({"name":"multi-model-smoke"}) == "multi_provider"


def test_decide_job_skips_only_degraded_target_provider():
    mod = load()
    health = {
        "xiaomi": {"classification":"tls_error"},
        "openai": {"classification":"reachable"},
    }
    xiaomi = {"id":"x", "name":"mimo", "provider":"xiaomi", "last_status":"error"}
    openai = {"id":"o", "name":"codex", "provider":"openai-codex", "last_status":"error"}
    assert mod.decide_job(xiaomi, health)["decision"] == "skip_provider_health_degraded"
    assert mod.decide_job(openai, health)["decision"] == "retry_allowed_by_provider_health"


def test_mult_provider_job_gets_exclusion_decision():
    mod = load()
    health = {"xiaomi":{"classification":"tls_error"}, "openai":{"classification":"reachable"}}
    job = {"id":"m", "name":"multi-model-smoke-every-4h", "last_status":"error"}
    d = mod.decide_job(job, health)
    assert d["decision"] == "run_with_degraded_provider_exclusions"
    assert d["degraded_endpoints"] == ["xiaomi"]


def test_build_report_from_jobs_and_probe(tmp_path):
    mod = load()
    jobs = {
        "jobs": [
            {"id":"x", "name":"daily full", "provider":"xiaomi", "last_status":"error"},
            {"id":"o", "name":"aihot", "provider":"openai-codex", "last_status":"error"},
            {"id":"m", "name":"multi-model-smoke", "last_status":"error"},
            {"id":"u", "name":"unknown", "last_status":"error"},
        ]
    }
    probe = {"status":"DEGRADED_PARTIAL", "classification_counts":{"reachable":1,"tls_error":1}, "results":[{"id":"xiaomi","classification":"tls_error"},{"id":"openai","classification":"reachable"}]}
    jobs_path = write_json(tmp_path / "jobs.json", jobs)
    probe_path = write_json(tmp_path / "probe.json", probe)
    report = mod.build_report(jobs_path, probe_path)
    assert report["decision_counts"]["skip_provider_health_degraded"] == 1
    assert report["decision_counts"]["retry_allowed_by_provider_health"] == 1
    assert report["decision_counts"]["run_with_degraded_provider_exclusions"] == 1
    assert report["decision_counts"]["needs_provider_resolution"] == 1


def test_write_report_is_local_only(tmp_path):
    mod = load()
    report = {"status":"READY", "probe_status":"DEGRADED_PARTIAL", "decision_counts":{"run":1}, "decisions":[{"job_id":"a","name":"job","decision":"run","reason":"ok"}], "safe_boundaries":["no cron job mutation"], "next_actions":["wire gate"]}
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert len(artifacts["sha256"]) == 64
