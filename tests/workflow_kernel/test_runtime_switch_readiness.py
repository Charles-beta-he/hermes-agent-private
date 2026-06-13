from __future__ import annotations

import importlib
import json
from pathlib import Path


def test_contract_packet_digest_is_stable_and_redacts_secrets():
    contracts = importlib.import_module("workflow_kernel.contracts")
    packet = contracts.contract_packet(
        packet_type="runtime_switch_readiness",
        payload={"b": 2, "a": 1, "api_key": "VALUE_SHOULD_NOT_LEAK"},
        evidence_refs=["command:test"],
    )
    assert packet["packet_type"] == "runtime_switch_readiness"
    assert packet["payload"]["api_key"] == "[REDACTED]"
    assert len(packet["payload_digest"]) == 64
    again = contracts.contract_packet(packet_type="runtime_switch_readiness", payload={"a": 1, "b": 2, "api_key": "DIFFERENT"}, evidence_refs=["command:test"])
    assert again["payload_digest"] == packet["payload_digest"]


def test_ledger_appends_idempotent_event_and_reads_back(tmp_path):
    ledger = importlib.import_module("workflow_kernel.ledger")
    path = tmp_path / "events.jsonl"
    event = ledger.append_event(path, event_type="runtime_readiness", payload={"status": "ready"}, idempotency_key="same")
    duplicate = ledger.append_event(path, event_type="runtime_readiness", payload={"status": "ready"}, idempotency_key="same")
    events = ledger.read_events(path)
    assert event["event_id"] == duplicate["event_id"]
    assert len(events) == 1
    assert events[0]["payload"]["status"] == "ready"


def test_runtime_readiness_requires_compat_cli_and_safety_flags():
    runtime = importlib.import_module("workflow_kernel.runtime")
    report = runtime.compile_runtime_readiness()
    assert report["status"] == "RUNTIME_SWITCH_READY_PENDING_EXPLICIT_ENABLE"
    assert report["runtime_switch_enabled_by_default"] is False
    assert report["switch_mode"] == "compatibility_shim"
    assert report["blocked_capabilities"] == ["cron_mutation", "external_dispatch_expansion", "autorun_enablement", "config_migration"]
    assert "scripts/workflow_architecture_migration_evaluation.py" in report["compatibility_clis"]
    assert report["required_tests"]


def test_runtime_readiness_cli_writes_artifact(tmp_path):
    runtime_cli = Path("scripts/workflow_kernel_runtime_readiness.py")
    assert runtime_cli.exists()
    import subprocess, sys
    out_dir = tmp_path / "readiness"
    proc = subprocess.run([sys.executable, str(runtime_cli), "--write-report", "--out-dir", str(out_dir), "--json"], text=True, capture_output=True, check=True)
    result = json.loads(proc.stdout)
    assert result["status"] == "RUNTIME_SWITCH_READY_PENDING_EXPLICIT_ENABLE"
    assert Path(result["artifacts"]["json"]).exists()
    assert len(result["artifacts"]["sha256"]) == 64
