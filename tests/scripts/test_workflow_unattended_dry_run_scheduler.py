from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_unattended_dry_run_scheduler.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_unattended_dry_run_scheduler", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def policy() -> dict:
    return {
        "status": "UNATTENDED_DISPATCH_POLICY_GATE_READY",
        "policy_digest": "abc123",
        "policy_version": "20260613.phase54.v1",
        "unattended_dispatch_enabled": False,
        "policy": {
            "target_allowlist": [{
                "adapter": "telegram",
                "target_type": "telegram_home_channel",
                "chat_id": "5524172309",
                "allowed_purposes": ["operator_control_tower_status"],
            }],
            "rate_limits": {"per_target_per_hour": 1, "per_target_per_day": 3},
        },
    }


def test_dry_run_receipt_never_sends():
    mod = load()
    receipt = mod.build_dry_run_receipt(policy(), now=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc))
    assert receipt["status"] == "UNATTENDED_DRY_RUN_RECEIPT_RECORDED"
    assert receipt["would_send"] is False
    assert receipt["external_side_effects_executed"] is False
    assert receipt["unattended_dispatch_enabled"] is False
    assert receipt["adapter"] == "telegram"
    assert receipt["time_bucket_utc_hour"] == "20260613T03"


def test_policy_not_fail_closed_blocks():
    mod = load()
    p = policy()
    p["unattended_dispatch_enabled"] = True
    receipt = mod.build_dry_run_receipt(p)
    assert receipt["status"] == "UNATTENDED_DRY_RUN_BLOCKED"
    assert "policy_not_fail_closed" in receipt["blockers"]


def test_explicit_receipt_and_report_paths_keep_caller_managed_directory_permissions(tmp_path):
    mod = load()
    receipt = mod.build_dry_run_receipt(policy())
    explicit = tmp_path / "explicit"
    explicit.mkdir(mode=0o755)
    explicit.chmod(0o755)
    ledger = explicit / "receipts.jsonl"
    ledger.write_text("", encoding="utf-8")
    ledger.chmod(0o644)
    mod.append_receipt(ledger, receipt)
    assert ledger.read_text().count("UNATTENDED_DRY_RUN_RECEIPT_RECORDED") == 1
    report = mod.build_report(receipt, ledger_path=ledger, cadence="daily")
    out = explicit / "out"
    out.mkdir(mode=0o755)
    out.chmod(0o755)
    artifacts = mod.write_report(report, out)
    assert Path(artifacts["json"]).exists()
    assert "Unattended Dry-run Scheduler" in Path(artifacts["md"]).read_text()
    assert explicit.stat().st_mode & 0o777 == 0o755
    assert out.stat().st_mode & 0o777 == 0o755
    assert ledger.stat().st_mode & 0o777 == 0o644


def test_default_runtime_paths_seed_frozen_ledger_without_writing_docs(tmp_path, monkeypatch):
    mod = load()
    legacy = tmp_path / "repo/docs/frozen.jsonl"
    runtime_dir = tmp_path / "profile/state/workflow-kernel/unattended-dry-run-scheduler"
    runtime_ledger = runtime_dir / "receipts.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"receipt":1}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "LEGACY_LEDGER", legacy)
    monkeypatch.setattr(mod, "DEFAULT_OUT", runtime_dir)
    monkeypatch.setattr(mod, "DEFAULT_LEDGER", runtime_ledger)

    result = mod.prepare_default_ledger(runtime_ledger)
    receipt = mod.build_dry_run_receipt(policy())
    mod.append_receipt(runtime_ledger, receipt)
    report = mod.build_report(receipt, ledger_path=runtime_ledger, cadence="daily")
    artifacts = mod.write_report(report, runtime_dir)

    assert result["status"] == "seeded"
    assert runtime_ledger.read_bytes().startswith(legacy.read_bytes())
    assert (tmp_path / "profile/state/workflow-kernel").stat().st_mode & 0o777 == 0o700
    assert runtime_dir.stat().st_mode & 0o777 == 0o700
    assert runtime_ledger.stat().st_mode & 0o777 == 0o600
    assert Path(artifacts["json"]).stat().st_mode & 0o777 == 0o600
    assert Path(artifacts["md"]).stat().st_mode & 0o777 == 0o600


def test_preview_without_write_flags_does_not_seed_default_ledger(tmp_path, monkeypatch, capsys):
    mod = load()
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy()), encoding="utf-8")
    legacy = tmp_path / "frozen.jsonl"
    legacy.write_text('{"receipt":1}\n', encoding="utf-8")
    runtime = tmp_path / "profile/state/workflow-kernel/unattended-dry-run-scheduler/receipts.jsonl"
    monkeypatch.setattr(mod, "LEGACY_LEDGER", legacy)
    monkeypatch.setattr(mod, "DEFAULT_LEDGER", runtime)

    exit_code = mod.main(["--policy", str(policy_path), "--ledger", str(runtime), "--json"])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["external_side_effects_executed"] is False
    assert not runtime.exists()


def test_append_receipt_rejects_symlink_ledger(tmp_path):
    mod = load()
    target = tmp_path / "target.jsonl"
    target.write_text('{"original":true}\n', encoding="utf-8")
    ledger = tmp_path / "receipts.jsonl"
    ledger.symlink_to(target)

    with pytest.raises(OSError):
        mod.append_receipt(ledger, {"would_send": False})

    assert target.read_text(encoding="utf-8") == '{"original":true}\n'


@pytest.mark.parametrize("artifact_name", [
    "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json",
    "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.md",
])
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_private_report_rejects_linked_output_before_overwrite(tmp_path, monkeypatch, artifact_name, link_kind):
    mod = load()
    runtime_dir = tmp_path / "profile/state/workflow-kernel/unattended-dry-run-scheduler"
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, "DEFAULT_OUT", runtime_dir)
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve-me", encoding="utf-8")
    linked_output = runtime_dir / artifact_name
    if link_kind == "symlink":
        linked_output.symlink_to(victim)
    else:
        linked_output.hardlink_to(victim)
    receipt = mod.build_dry_run_receipt(policy())
    report = mod.build_report(receipt, ledger_path=runtime_dir / "receipts.jsonl", cadence="daily")

    with pytest.raises(OSError, match="unsafe private file target"):
        mod.write_report(report, runtime_dir)

    assert victim.read_text(encoding="utf-8") == "preserve-me"
