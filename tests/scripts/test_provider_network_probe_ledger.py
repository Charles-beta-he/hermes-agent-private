from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


def load():
    p = Path(__file__).resolve().parents[2] / "scripts" / "provider_network_probe_ledger.py"
    spec = importlib.util.spec_from_file_location("provider_network_probe_ledger", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_http_status_treats_auth_errors_as_reachable():
    mod = load()
    assert mod.classify_http_status(200, None) == "reachable"
    assert mod.classify_http_status(401, None) == "reachable"
    assert mod.classify_http_status(404, None) == "reachable"
    assert mod.classify_http_status(503, None) == "remote_5xx"
    assert mod.classify_http_status(None, "TimeoutError: timed out") == "timeout"
    assert mod.classify_http_status(None, "URLError: ProxyError 503 Service Unavailable") == "proxy_or_gateway_error"
    assert mod.classify_http_status(None, "SSLError: certificate verify failed") == "tls_error"


def test_overall_status_boundaries():
    mod = load()
    assert mod.overall_status([{"classification":"reachable"}]) == "PASS"
    assert mod.overall_status([{"classification":"reachable"},{"classification":"timeout"}]) == "DEGRADED_PARTIAL"
    assert mod.overall_status([{"classification":"timeout"},{"classification":"network_error"}]) == "BLOCKED_NETWORK"


def test_proxy_redaction(monkeypatch):
    mod = load()
    assert mod.redact_proxy("http://user:pass@example.com:8080") == "http://[REDACTED]@example.com:8080"
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@example.com:8080")
    snap = mod.proxy_snapshot()
    assert snap["HTTPS_PROXY"] == "http://[REDACTED]@example.com:8080"


def test_xiaomi_endpoint_uses_runtime_base_url_shape(monkeypatch):
    mod = load()
    monkeypatch.setenv("XIAOMI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
    ep = mod.resolve_endpoint({
        "id": "xiaomi",
        "url": "https://api.xiaomimimo.com/v1/models",
        "base_url_env_var": "XIAOMI_BASE_URL",
        "base_url_default": "https://api.xiaomimimo.com/v1",
    })
    assert ep["url"] == "https://token-plan-cn.xiaomimimo.com/v1/models"
    assert ep["configured_base_url_host"] == "token-plan-cn.xiaomimimo.com"


def test_default_xiaomi_probe_no_longer_uses_retired_mimogpt_host(monkeypatch):
    mod = load()
    monkeypatch.delenv("XIAOMI_BASE_URL", raising=False)
    monkeypatch.setattr(mod, "_dotenv_value", lambda _name: None)
    xiaomi = next(e for e in mod.DEFAULT_ENDPOINTS if e["id"] == "xiaomi")
    ep = mod.resolve_endpoint(xiaomi)
    assert ep["url"] == "https://api.xiaomimimo.com/v1/models"
    assert "api.mimogpt.com" not in ep["url"]


def test_write_report_is_local_evidence_only(tmp_path):
    mod = load()
    report = {
        "status": "DEGRADED_PARTIAL",
        "probe_count": 2,
        "classification_counts": {"reachable": 1, "timeout": 1},
        "results": [
            {"id":"github", "classification":"reachable", "status_code":200, "url_host":"github.com", "duration_ms":10},
            {"id":"telegram", "classification":"timeout", "status_code":None, "url_host":"api.telegram.org", "duration_ms":5000},
        ],
        "safe_boundaries": ["no LLM generation call", "no external message send"],
        "next_actions": ["test action"],
    }
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert len(artifacts["sha256"]) == 64
    written = json.loads(Path(artifacts["json"]).read_text())
    assert written["status"] == "DEGRADED_PARTIAL"
