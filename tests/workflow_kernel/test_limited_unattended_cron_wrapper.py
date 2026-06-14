from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_kernel_limited_unattended_cron_wrapper.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_kernel_limited_unattended_cron_wrapper", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cron_wrapper_silent_on_expected_daily_duplicate(monkeypatch, capsys):
    mod = load()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=2,
            stdout=json.dumps({
                "status": "LIMITED_UNATTENDED_SEND_BLOCKED",
                "blockers": ["duplicate_idempotency_key", "daily_limit_exhausted"],
                "rate_limit": {"allowed": False},
            }),
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main() == 0
    assert capsys.readouterr().out == ""


def test_cron_wrapper_outputs_compact_receipt_on_execution(monkeypatch, capsys):
    mod = load()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps({
                "status": "LIMITED_UNATTENDED_SEND_EXECUTED",
                "target": "telegram",
                "purpose": "workflow_kernel_status_smoke",
                "send_result": {"message_id": "unit", "chat_id": "chat"},
                "idempotency_key": "idem",
                "day_bucket": "20260615",
            }),
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "LIMITED_UNATTENDED_SEND_EXECUTED"
    assert out["message_id"] == "unit"


def test_cron_wrapper_alerts_on_unexpected_blocker(monkeypatch, capsys):
    mod = load()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=2,
            stdout=json.dumps({
                "status": "LIMITED_UNATTENDED_SEND_BLOCKED",
                "blockers": ["manifest_not_opened"],
                "rate_limit": {"allowed": False},
            }),
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "WORKFLOW_KERNEL_LIMITED_UNATTENDED_ALERT"
    assert out["blockers"] == ["manifest_not_opened"]
