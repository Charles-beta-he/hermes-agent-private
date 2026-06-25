from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_kernel_limited_unattended_sender.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_kernel_limited_unattended_sender", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def manifest(path: Path) -> Path:
    p = path / "manifest.json"
    p.write_text(json.dumps({
        "schema": "workflow_kernel_open_gate_v1",
        "status": "OPENED_LIMITED_UNATTENDED",
        "opened": True,
        "kill_switch_enabled": False,
        "manifest_digest": "md",
        "target": "telegram",
        "purpose": "workflow_kernel_status_smoke",
        "payload_template": "Workflow Kernel opened limited unattended dispatch: governed one-target smoke.",
    }), encoding="utf-8")
    return p


def fake_registry():
    return {
        "registry_digest": "rd",
        "multi_target_enabled": True,
        "cross_adapter_enabled": False,
        "targets": [
            {
                "target_id": "telegram_home",
                "enabled": True,
                "kill_switch_enabled": False,
                "adapter": "telegram",
                "send_target": "telegram",
                "daily_limit": 1,
                "purpose_allowlist": ["workflow_kernel_status_smoke"],
                "compensation_path": "manual correction",
            }
        ],
    }


def test_execute_sends_once_when_manifest_registry_and_rate_limit_allow(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setattr(mod, "registry_snapshot", fake_registry)
    monkeypatch.setattr(mod, "validate_registry", lambda registry: {"status": "PASS", "errors": []})
    monkeypatch.setattr(mod, "validate_open_manifest", lambda mf: {"status": "PASS", "errors": []})
    monkeypatch.setattr(mod, "run_send", lambda target, payload: {"returncode": 0, "parsed": {"success": True, "platform": "telegram", "chat_id": "1", "message_id": "2"}, "stdout": "{}", "stderr": "", "command": ["hermes", "send"]})

    result = mod.build_or_execute(manifest(tmp_path), tmp_path / "ledger.jsonl", execute=True, target_id="telegram_home")

    assert result["status"] == "LIMITED_UNATTENDED_DISPATCH_EXECUTED"
    assert result["executed"] is True
    target = result["target_receipts"][0]
    assert target["status"] == "LIMITED_UNATTENDED_SEND_EXECUTED"
    assert target["external_side_effects_executed"] is True
    assert target["send_result"]["message_id"] == "2"


def test_execute_is_blocked_by_duplicate_and_daily_limit_without_send(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setattr(mod, "registry_snapshot", fake_registry)
    monkeypatch.setattr(mod, "validate_registry", lambda registry: {"status": "PASS", "errors": []})
    monkeypatch.setattr(mod, "validate_open_manifest", lambda mf: {"status": "PASS", "errors": []})
    calls: list[str] = []

    def fake_send(target: str, payload: str):
        calls.append(payload)
        return {"returncode": 0, "parsed": {"success": True, "platform": "telegram", "chat_id": "1", "message_id": "2"}, "stdout": "{}", "stderr": "", "command": ["hermes", "send"]}

    monkeypatch.setattr(mod, "run_send", fake_send)

    mf_path = manifest(tmp_path)
    first = mod.build_or_execute(mf_path, tmp_path / "ledger.jsonl", execute=True, target_id="telegram_home")
    duplicate = mod.build_or_execute(mf_path, tmp_path / "ledger.jsonl", execute=True, target_id="telegram_home")

    assert first["status"] == "LIMITED_UNATTENDED_DISPATCH_EXECUTED"
    assert duplicate["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    target = duplicate["target_receipts"][0]
    assert set(target["blockers"]) == {"duplicate_idempotency_key", "daily_limit_exhausted"}
    assert target["external_side_effects_executed"] is False
    assert calls == ["Workflow Kernel opened limited unattended dispatch: governed one-target smoke."]
