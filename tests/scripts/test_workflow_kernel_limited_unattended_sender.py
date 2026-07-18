from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

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


VALID_NEUTRAL_LEDGER_ENTRY = {
    "schema": "workflow_kernel_limited_unattended_send_receipt_v1",
    "status": "LIMITED_UNATTENDED_SEND_BLOCKED",
    "executed": False,
    "day_bucket": "20260718",
    "idempotency_key": "historical-blocked-key",
    "blockers": ["historical_blocker"],
}


def write_valid_neutral_ledger(path: Path) -> None:
    path.write_text(json.dumps(VALID_NEUTRAL_LEDGER_ENTRY) + "\n", encoding="utf-8")


def test_default_ledger_is_profile_scoped_and_seeds_frozen_history(tmp_path, monkeypatch):
    mod = load()
    legacy = tmp_path / "frozen.jsonl"
    runtime = tmp_path / "profile/state/workflow-kernel/limited-unattended/receipts.jsonl"
    legacy.write_text('{"receipt":1}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "LEGACY_LEDGER", legacy)
    monkeypatch.setattr(mod, "DEFAULT_LEDGER", runtime)

    result = mod.prepare_default_ledger(runtime)

    assert result["status"] == "seeded"
    assert runtime.read_bytes() == legacy.read_bytes()


def test_explicit_ledger_override_keeps_caller_managed_permissions(tmp_path):
    mod = load()
    parent = tmp_path / "explicit"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    ledger = parent / "receipts.jsonl"
    ledger.write_text('{"existing":true}\n', encoding="utf-8")
    ledger.chmod(0o644)

    mod.append_ledger(ledger, {"executed": False})

    assert parent.stat().st_mode & 0o777 == 0o755
    assert ledger.stat().st_mode & 0o777 == 0o644


def test_execute_direct_call_seeds_default_legacy_before_rate_limit_check(tmp_path, monkeypatch):
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
    source_ledger = tmp_path / "source.jsonl"
    write_valid_neutral_ledger(source_ledger)
    first = mod.build_or_execute(mf_path, source_ledger, execute=True, target_id="telegram_home")
    assert first["executed"] is True

    legacy = tmp_path / "frozen.jsonl"
    legacy.write_bytes(source_ledger.read_bytes())
    runtime = tmp_path / "profile/state/workflow-kernel/limited-unattended/receipts.jsonl"
    monkeypatch.setattr(mod, "LEGACY_LEDGER", legacy)
    monkeypatch.setattr(mod, "DEFAULT_LEDGER", runtime)

    duplicate = mod.build_or_execute(mf_path, runtime, execute=True, target_id="telegram_home")

    assert runtime.read_bytes().startswith(legacy.read_bytes())
    assert duplicate["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    assert set(duplicate["target_receipts"][0]["blockers"]) == {"duplicate_idempotency_key", "daily_limit_exhausted"}
    assert calls == ["Workflow Kernel opened limited unattended dispatch: governed one-target smoke."]


def test_execute_main_fails_closed_when_runtime_ledger_is_missing(tmp_path, monkeypatch, capsys):
    mod = load()
    runtime = tmp_path / "profile/state/workflow-kernel/limited-unattended/receipts.jsonl"
    monkeypatch.setattr(mod, "DEFAULT_LEDGER", runtime)
    monkeypatch.setattr(mod, "LEGACY_LEDGER", tmp_path / "missing-frozen.jsonl")
    calls: list[bool] = []
    monkeypatch.setattr(mod, "_build_or_execute_unlocked", lambda *args, **kwargs: calls.append(True))

    exit_code = mod.main(["--ledger", str(runtime), "--execute", "--json"])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert calls == []
    assert result["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    assert result["blockers"] == ["production_ledger_missing"]
    assert result["external_side_effects_executed"] is False
    assert not runtime.exists()


def test_execute_main_fails_closed_when_runtime_ledger_is_empty(tmp_path, monkeypatch, capsys):
    mod = load()
    runtime = tmp_path / "receipts.jsonl"
    runtime.write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "DEFAULT_LEDGER", runtime)
    monkeypatch.setattr(mod, "LEGACY_LEDGER", tmp_path / "missing-frozen.jsonl")
    calls: list[bool] = []
    monkeypatch.setattr(mod, "_build_or_execute_unlocked", lambda *args, **kwargs: calls.append(True))

    exit_code = mod.main(["--ledger", str(runtime), "--execute", "--json"])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert calls == []
    assert result["blockers"] == ["production_ledger_empty"]


def test_execute_fails_closed_when_runtime_ledger_is_malformed(tmp_path, monkeypatch):
    mod = load()
    runtime = tmp_path / "receipts.jsonl"
    runtime.write_text("{not-json}\n", encoding="utf-8")
    calls: list[bool] = []
    monkeypatch.setattr(mod, "_build_or_execute_unlocked", lambda *args, **kwargs: calls.append(True))

    result = mod.build_or_execute(tmp_path / "manifest.json", runtime, execute=True)

    assert calls == []
    assert result["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    assert result["blockers"] == ["production_ledger_invalid_json"]


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"executed": False},
        {"schema": "unknown_receipt_v99", "executed": False},
        {
            "schema": "workflow_kernel_limited_unattended_reservation_v1",
            "status": "LIMITED_UNATTENDED_SEND_PENDING",
            "reservation_state": "pending",
            "executed": False,
        },
    ],
)
def test_execute_fails_closed_when_runtime_ledger_entry_schema_is_invalid(tmp_path, entry):
    mod = load()
    runtime = tmp_path / "receipts.jsonl"
    runtime.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mod.build_or_execute(tmp_path / "manifest.json", runtime, execute=True)

    assert result["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    assert result["blockers"] == ["production_ledger_invalid_entry"]


def test_execute_accepts_known_valid_ledger_schemas(tmp_path, monkeypatch):
    mod = load()
    entries = [
        VALID_NEUTRAL_LEDGER_ENTRY,
        {
            "schema": "workflow_kernel_limited_unattended_reservation_v1",
            "status": "LIMITED_UNATTENDED_SEND_PENDING",
            "reservation_state": "pending",
            "executed": False,
            "day_bucket": "20260718",
            "idempotency_key": "pending-key",
            "target_id": "telegram_home",
        },
        {
            "schema": "workflow_kernel_limited_unattended_dispatch_receipt_v2",
            "status": "LIMITED_UNATTENDED_DISPATCH_EXECUTED",
            "executed": True,
            "external_side_effects_executed": True,
            "day_bucket": "20260718",
            "executed_count": 1,
            "target_count": 1,
            "blockers": [],
            "target_receipts": [
                {
                    "schema": "workflow_kernel_limited_unattended_target_receipt_v2",
                    "status": "LIMITED_UNATTENDED_SEND_EXECUTED",
                    "executed": True,
                    "external_side_effects_executed": True,
                    "day_bucket": "20260718",
                    "idempotency_key": "executed-key",
                    "target_id": "telegram_home",
                    "blockers": [],
                }
            ],
        },
    ]
    monkeypatch.setattr(
        mod,
        "_build_or_execute_unlocked",
        lambda *args, **kwargs: {
            "status": "LIMITED_UNATTENDED_DISPATCH_READY",
            "executed": False,
            "target_count": 0,
        },
    )

    for index, entry in enumerate(entries):
        runtime = tmp_path / f"receipts-{index}.jsonl"
        runtime.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        result = mod.build_or_execute(tmp_path / "manifest.json", runtime, execute=True)
        assert result["status"] == "LIMITED_UNATTENDED_DISPATCH_READY"


def test_execute_rejects_dispatch_receipt_with_inconsistent_target_count(tmp_path):
    mod = load()
    entry = {
        "schema": "workflow_kernel_limited_unattended_dispatch_receipt_v2",
        "status": "LIMITED_UNATTENDED_DISPATCH_EXECUTED",
        "executed": True,
        "external_side_effects_executed": True,
        "day_bucket": "20260718",
        "executed_count": 1,
        "target_count": 0,
        "blockers": [],
        "target_receipts": [
            {
                "schema": "workflow_kernel_limited_unattended_target_receipt_v2",
                "status": "LIMITED_UNATTENDED_SEND_EXECUTED",
                "executed": True,
                "external_side_effects_executed": True,
                "day_bucket": "20260718",
                "idempotency_key": "executed-key",
                "target_id": "telegram_home",
                "blockers": [],
            }
        ],
    }
    runtime = tmp_path / "receipts.jsonl"
    runtime.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mod.build_or_execute(tmp_path / "manifest.json", runtime, execute=True)

    assert result["blockers"] == ["production_ledger_invalid_entry"]


def test_execute_rejects_dispatch_receipt_consistency_mismatches(tmp_path):
    mod = load()
    base = {
        "schema": "workflow_kernel_limited_unattended_dispatch_receipt_v2",
        "status": "LIMITED_UNATTENDED_DISPATCH_EXECUTED",
        "executed": True,
        "external_side_effects_executed": True,
        "day_bucket": "20260718",
        "executed_count": 1,
        "target_count": 1,
        "blockers": [],
        "target_receipts": [
            {
                "schema": "workflow_kernel_limited_unattended_target_receipt_v2",
                "status": "LIMITED_UNATTENDED_SEND_EXECUTED",
                "executed": True,
                "external_side_effects_executed": True,
                "day_bucket": "20260718",
                "idempotency_key": "executed-key",
                "target_id": "telegram_home",
                "blockers": [],
            }
        ],
    }
    variants = []
    for mutation in (
        lambda entry: entry.update(executed_count=0),
        lambda entry: entry.update(executed=False, external_side_effects_executed=False),
        lambda entry: entry.update(external_side_effects_executed=False),
        lambda entry: entry.update(status="LIMITED_UNATTENDED_DISPATCH_BLOCKED"),
        lambda entry: entry["target_receipts"][0].update(external_side_effects_executed=False),
        lambda entry: entry["target_receipts"][0].update(status="LIMITED_UNATTENDED_SEND_BLOCKED"),
    ):
        variant = json.loads(json.dumps(base))
        mutation(variant)
        variants.append(variant)

    for index, entry in enumerate(variants):
        runtime = tmp_path / f"invalid-consistency-{index}.jsonl"
        runtime.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert mod._execute_ledger_blocker(runtime) == "production_ledger_invalid_entry"


def test_execute_holds_exclusive_lock_across_dispatch(tmp_path, monkeypatch):
    mod = load()
    runtime = tmp_path / "receipts.jsonl"
    write_valid_neutral_ledger(runtime)
    events: list[str] = []

    @contextmanager
    def fake_lock(path, **kwargs):
        events.append(f"locked:{path}")
        yield
        events.append("unlocked")

    def fake_dispatch(*args, **kwargs):
        events.append("dispatch")
        return {"status": "LIMITED_UNATTENDED_DISPATCH_EXECUTED", "executed": True, "target_count": 1}

    monkeypatch.setattr(mod, "exclusive_ledger_lock", fake_lock)
    monkeypatch.setattr(mod, "_build_or_execute_unlocked", fake_dispatch)

    result = mod.build_or_execute(tmp_path / "manifest.json", runtime, execute=True)

    assert result["executed"] is True
    assert events == [f"locked:{runtime}", "dispatch", "unlocked"]


def test_execute_sends_once_when_manifest_registry_and_rate_limit_allow(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setattr(mod, "registry_snapshot", fake_registry)
    monkeypatch.setattr(mod, "validate_registry", lambda registry: {"status": "PASS", "errors": []})
    monkeypatch.setattr(mod, "validate_open_manifest", lambda mf: {"status": "PASS", "errors": []})
    monkeypatch.setattr(mod, "run_send", lambda target, payload: {"returncode": 0, "parsed": {"success": True, "platform": "telegram", "chat_id": "1", "message_id": "2"}, "stdout": "{}", "stderr": "", "command": ["hermes", "send"]})

    ledger = tmp_path / "ledger.jsonl"
    write_valid_neutral_ledger(ledger)
    result = mod.build_or_execute(manifest(tmp_path), ledger, execute=True, target_id="telegram_home")

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
    ledger = tmp_path / "ledger.jsonl"
    write_valid_neutral_ledger(ledger)
    first = mod.build_or_execute(mf_path, ledger, execute=True, target_id="telegram_home")
    duplicate = mod.build_or_execute(mf_path, ledger, execute=True, target_id="telegram_home")

    assert first["status"] == "LIMITED_UNATTENDED_DISPATCH_EXECUTED"
    assert duplicate["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    target = duplicate["target_receipts"][0]
    assert set(target["blockers"]) == {"duplicate_idempotency_key", "daily_limit_exhausted"}
    assert target["external_side_effects_executed"] is False
    assert calls == ["Workflow Kernel opened limited unattended dispatch: governed one-target smoke."]


def test_preview_does_not_seed_or_append_default_ledger(tmp_path, monkeypatch):
    mod = load()
    runtime = tmp_path / "profile/state/workflow-kernel/limited-unattended/receipts.jsonl"
    legacy = tmp_path / "frozen.jsonl"
    legacy.write_text('{"executed":false}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "DEFAULT_LEDGER", runtime)
    monkeypatch.setattr(mod, "LEGACY_LEDGER", legacy)
    monkeypatch.setattr(mod, "registry_snapshot", fake_registry)
    monkeypatch.setattr(mod, "validate_registry", lambda registry: {"status": "PASS", "errors": []})
    monkeypatch.setattr(mod, "validate_open_manifest", lambda mf: {"status": "PASS", "errors": []})

    result = mod.build_or_execute(manifest(tmp_path), runtime, execute=False)

    assert result["dry_run"] is True
    assert result["external_side_effects_executed"] is False
    assert not runtime.exists()


def test_execute_ledger_validation_returns_structured_blockers_for_unsafe_inputs(tmp_path, monkeypatch):
    mod = load()
    invalid_utf8 = tmp_path / "invalid.jsonl"
    invalid_utf8.write_bytes(b"\xff\n")
    directory = tmp_path / "directory.jsonl"
    directory.mkdir()
    unreadable = tmp_path / "unreadable.jsonl"
    unreadable.write_text('{}\n', encoding="utf-8")
    original_read_text = Path.read_text

    assert mod._execute_ledger_blocker(invalid_utf8) == "production_ledger_invalid_encoding"
    assert mod._execute_ledger_blocker(directory) == "production_ledger_not_regular_file"

    def guarded_read_text(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    assert mod._execute_ledger_blocker(unreadable) == "production_ledger_unreadable"


def test_execute_rejects_symlink_ledger_without_touching_target(tmp_path, monkeypatch):
    mod = load()
    target = tmp_path / "target.jsonl"
    target.write_text('{"executed":false}\n', encoding="utf-8")
    target.chmod(0o644)
    ledger = tmp_path / "ledger.jsonl"
    ledger.symlink_to(target)
    calls = []
    monkeypatch.setattr(mod, "_build_or_execute_unlocked", lambda *args, **kwargs: calls.append(True))

    result = mod.build_or_execute(tmp_path / "manifest.json", ledger, execute=True)

    assert result["blockers"] == ["production_ledger_symlink"]
    assert calls == []
    assert target.stat().st_mode & 0o777 == 0o644


def test_execute_rejects_group_or_world_writable_explicit_parent(tmp_path, monkeypatch):
    mod = load()
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    ledger = parent / "receipts.jsonl"
    write_valid_neutral_ledger(ledger)
    calls = []
    monkeypatch.setattr(mod, "_build_or_execute_unlocked", lambda *args, **kwargs: calls.append(True))

    result = mod.build_or_execute(tmp_path / "manifest.json", ledger, execute=True)

    assert result["blockers"] == ["production_ledger_parent_not_private"]
    assert calls == []


def test_execute_rejects_symlinked_ledger_ancestor(tmp_path, monkeypatch):
    mod = load()
    outside = tmp_path / "outside"
    parent = outside / "private"
    parent.mkdir(parents=True, mode=0o700)
    parent.chmod(0o700)
    ledger = parent / "receipts.jsonl"
    write_valid_neutral_ledger(ledger)
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    calls = []
    monkeypatch.setattr(mod, "_build_or_execute_unlocked", lambda *args, **kwargs: calls.append(True))

    result = mod.build_or_execute(tmp_path / "manifest.json", alias / "private/receipts.jsonl", execute=True)

    assert result["blockers"] == ["production_ledger_ancestor_symlink"]
    assert calls == []


def test_execute_fails_closed_without_no_follow_support(tmp_path, monkeypatch):
    mod = load()
    ledger = tmp_path / "receipts.jsonl"
    write_valid_neutral_ledger(ledger)
    monkeypatch.delattr(mod.os, "O_NOFOLLOW", raising=False)

    result = mod.build_or_execute(tmp_path / "manifest.json", ledger, execute=True)

    assert result["blockers"] == ["production_runtime_permissions_unsupported"]
    assert result["external_side_effects_executed"] is False


def test_public_append_ledger_uses_exclusive_lock(tmp_path, monkeypatch):
    mod = load()
    ledger = tmp_path / "receipts.jsonl"
    events = []

    @contextmanager
    def fake_lock(path, **kwargs):
        events.append("lock")
        yield
        events.append("unlock")

    monkeypatch.setattr(mod, "exclusive_ledger_lock", fake_lock)
    mod.append_ledger(ledger, {"executed": False})

    assert events == ["lock", "unlock"]


def test_public_append_ledger_rejects_symlink_target(tmp_path):
    mod = load()
    target = tmp_path / "target.jsonl"
    target.write_text('{"original":true}\n', encoding="utf-8")
    ledger = tmp_path / "receipts.jsonl"
    ledger.symlink_to(target)

    with pytest.raises(OSError):
        mod.append_ledger(ledger, {"executed": False})

    assert target.read_text(encoding="utf-8") == '{"original":true}\n'


def test_execute_fails_closed_when_private_permissions_are_unsupported(tmp_path, monkeypatch):
    mod = load()
    ledger = tmp_path / "receipts.jsonl"
    write_valid_neutral_ledger(ledger)
    monkeypatch.setattr(mod.os, "name", "nt")

    result = mod.build_or_execute(tmp_path / "manifest.json", ledger, execute=True)

    assert result["blockers"] == ["production_runtime_permissions_unsupported"]
    assert result["external_side_effects_executed"] is False


def test_pending_reservation_blocks_retry_if_final_receipt_append_crashes(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setattr(mod, "registry_snapshot", fake_registry)
    monkeypatch.setattr(mod, "validate_registry", lambda registry: {"status": "PASS", "errors": []})
    monkeypatch.setattr(mod, "validate_open_manifest", lambda mf: {"status": "PASS", "errors": []})
    calls = []
    monkeypatch.setattr(mod, "run_send", lambda target, payload: calls.append(payload) or {"returncode": 0, "parsed": {"success": True, "message_id": "2"}, "stdout": "{}", "stderr": "", "command": ["hermes", "send"]})
    ledger = tmp_path / "ledger.jsonl"
    write_valid_neutral_ledger(ledger)
    real_append = mod._append_ledger_unlocked

    def fail_final_append(path, receipt, **kwargs):
        if receipt.get("schema") == "workflow_kernel_limited_unattended_dispatch_receipt_v2":
            raise OSError("simulated crash after send")
        return real_append(path, receipt, **kwargs)

    monkeypatch.setattr(mod, "_append_ledger_unlocked", fail_final_append)
    with pytest.raises(OSError, match="simulated crash"):
        mod.build_or_execute(manifest(tmp_path), ledger, execute=True, target_id="telegram_home")

    monkeypatch.setattr(mod, "_append_ledger_unlocked", real_append)
    retry = mod.build_or_execute(manifest(tmp_path), ledger, execute=True, target_id="telegram_home")

    assert calls == ["Workflow Kernel opened limited unattended dispatch: governed one-target smoke."]
    assert retry["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    assert "pending_idempotency_reservation" in retry["target_receipts"][0]["blockers"]
