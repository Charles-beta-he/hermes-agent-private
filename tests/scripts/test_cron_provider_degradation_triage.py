from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load():
    p = Path(__file__).resolve().parents[2] / "scripts" / "cron_provider_degradation_triage.py"
    spec = importlib.util.spec_from_file_location("cron_provider_degradation_triage", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_jobs(tmp_path: Path, jobs: list[dict]) -> Path:
    p = tmp_path / "jobs.json"
    p.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False))
    return p


def test_classifies_provider_and_network_script_failures(tmp_path):
    mod = load()
    jobs_path = write_jobs(tmp_path, [
        {"id":"a", "name":"provider job", "last_status":"error", "last_error":"RuntimeError: Connection error.", "script":"x.py"},
        {"id":"b", "name":"upstream", "last_status":"error", "last_error":"Script exited with code 1\nSSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443", "script":"upstream.py"},
        {"id":"ok", "name":"ok", "last_status":"ok"},
    ])
    report = mod.build_report(jobs_path)
    assert report["failed_count"] == 2
    by_id = {j["job_id"]: j for j in report["failed_jobs"]}
    assert by_id["a"]["primary_class"] == "provider_generation"
    assert by_id["b"]["primary_class"] == "script_network_failure"
    assert report["status"] == "DEGRADED_NOT_BLOCKED"


def test_classifies_telegram_delivery_as_delivery_not_provider(tmp_path):
    mod = load()
    jobs_path = write_jobs(tmp_path, [
        {"id":"tg", "name":"daily", "last_status":"error", "last_error":"RuntimeError: Connection error.", "last_delivery_error":"delivery error: Telegram send failed: httpx.ProxyError: 503 Service Unavailable"},
    ])
    report = mod.build_report(jobs_path)
    job = report["failed_jobs"][0]
    assert job["primary_class"] == "telegram_delivery"
    assert "telegram_delivery" in job["classes"]
    assert "external message send" in " ".join(report["safe_boundaries"])


def test_write_report_is_local_evidence_only(tmp_path):
    mod = load()
    jobs_path = write_jobs(tmp_path, [{"id":"a", "name":"provider", "last_status":"error", "last_error":"RuntimeError: Connection error."}])
    report = mod.build_report(jobs_path)
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    written = json.loads(Path(artifacts["json"]).read_text())
    assert written["failed_count"] == 1
    assert len(artifacts["sha256"]) == 64
