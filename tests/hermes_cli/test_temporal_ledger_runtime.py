from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import hermes_cli.temporal_ledger_runtime as runtime_mod
from hermes_cli.subcommands.agent_os import build_agent_os_parser
from hermes_cli.temporal_ledger_runtime import (
    AUTHORITY_SCHEMA,
    activate_runtime,
    deactivate_runtime,
    inspect_runtime,
    runtime_status,
)


STUB_MODULE = '''from datetime import datetime\n\ndef append_event(path, submission):\n    raise NotImplementedError\n\ndef replay_ledger(path):\n    raw = open(path, "rb").read()\n    return {"schema": "TemporalCommitmentLedgerReplay.v1", "row_count": 0 if not raw else 1, "ledger_digest": __import__("hashlib").sha256(raw).hexdigest(), "runtime_mutation": False, "external_side_effects_executed": False, "authority_effect": "none"}\n\ndef inspect_ledger(path, as_of):\n    replay = replay_ledger(path)\n    return {**replay, "schema": "TemporalCommitmentLedgerInspection.v1", "as_of": as_of.isoformat(), "commitments": {}, "active_commitment_ids": [], "expired_commitment_ids": []}\n'''


def _write_module(tmp_path: Path) -> tuple[Path, str]:
    module = tmp_path / "temporal_commitment_ledger.py"
    module.write_text(STUB_MODULE, encoding="utf-8")
    return module, hashlib.sha256(module.read_bytes()).hexdigest()


def _gate(home: Path, module: Path, digest: str, *, action: str = "activate", **overrides) -> Path:
    now = datetime.now(timezone.utc)
    payload = {
        "schema": AUTHORITY_SCHEMA,
        "authorization_id": f"auth:test-{action}",
        "granted_by": "user",
        "source_ref": "session:user-message:test",
        "action": action,
        "module_source": str(module.resolve()),
        "module_sha256": digest,
        "runtime_manifest": str((home / "state/personal-ai-os/temporal-commitment-runtime.json").resolve()),
        "ledger_path": str((home / "state/personal-ai-os/temporal-commitments.v1.jsonl").resolve()),
        "evidence_refs": ["test:focused-runtime-contract"],
        "rollback": {
            "action": "deactivate" if action == "activate" else "activate",
            "command": "hermes agent-os temporal-ledger deactivate --authority-gate <exact-path>" if action == "activate" else "hermes agent-os temporal-ledger activate --authority-gate <exact-path>",
        },
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }
    payload.update(overrides)
    path = home.parent / f"{action}-authority.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_activate_creates_content_addressed_release_manifest_empty_ledger_and_receipt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    gate = _gate(home, module, digest)

    result = activate_runtime(home=home, authority_gate=gate)

    assert result["ok"] is True
    assert result["activated"] is True
    assert result["module_sha256"] == digest
    assert result["readback"]["trusted"] is True
    assert result["readback"]["active"] is True
    assert result["readback"]["row_count"] == 0
    release = home / "runtime/personal-ai-os/releases" / digest / "temporal_commitment_ledger.py"
    assert release.read_bytes() == module.read_bytes()
    assert (release.stat().st_mode & 0o222) == 0
    ledger = home / "state/personal-ai-os/temporal-commitments.v1.jsonl"
    assert ledger.read_bytes() == b""
    receipts = (home / "state/personal-ai-os/temporal-commitment-runtime-receipts.jsonl").read_text().splitlines()
    assert len(receipts) == 1
    assert json.loads(receipts[0])["outcome"] == "activated"


def test_activation_fails_closed_on_wrong_digest_without_runtime_residue(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, _ = _write_module(tmp_path)
    gate = _gate(home, module, "0" * 64)

    result = activate_runtime(home=home, authority_gate=gate)

    assert result["ok"] is False
    assert result["activated"] is False
    assert result["error_code"] == "module_digest_mismatch"
    assert not (home / "state/personal-ai-os").exists()
    assert not (home / "runtime/personal-ai-os").exists()


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"issued_at": "1999-12-31T00:00:00+00:00", "expires_at": "2000-01-01T00:00:00+00:00"}, "authority_expired"),
        ({"granted_by": "model"}, "authority_not_user_granted"),
        ({"evidence_refs": []}, "authority_missing_evidence"),
    ],
)
def test_activation_rejects_invalid_authority(tmp_path: Path, overrides: dict, error_code: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    gate = _gate(home, module, digest, **overrides)

    result = activate_runtime(home=home, authority_gate=gate)

    assert result["ok"] is False
    assert result["error_code"] == error_code


def test_activation_rejects_long_lived_authority(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    now = datetime.now(timezone.utc)
    gate = _gate(
        home,
        module,
        digest,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=31)).isoformat(),
    )

    result = activate_runtime(home=home, authority_gate=gate)

    assert result["ok"] is False
    assert result["error_code"] == "authority_ttl_too_long"


def test_activation_rejects_target_path_escape(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    gate = _gate(home, module, digest, ledger_path=str(tmp_path / "outside.jsonl"))

    result = activate_runtime(home=home, authority_gate=gate)

    assert result["ok"] is False
    assert result["error_code"] == "authority_target_mismatch"


def test_activation_rejects_symlink_and_multilink_sources(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    symlink = tmp_path / "linked.py"
    symlink.symlink_to(module)
    symlink_gate = _gate(home, module, digest, module_source=str(symlink))
    assert activate_runtime(home=home, authority_gate=symlink_gate)["error_code"] == "unsafe_module_source"

    hardlink = tmp_path / "hardlinked.py"
    os.link(module, hardlink)
    hardlink_gate = _gate(home, hardlink, digest)
    assert activate_runtime(home=home, authority_gate=hardlink_gate)["error_code"] == "unsafe_module_source"


def test_status_fails_closed_when_active_release_bytes_drift(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    assert activate_runtime(home=home, authority_gate=_gate(home, module, digest))["ok"] is True
    release = home / "runtime/personal-ai-os/releases" / digest / "temporal_commitment_ledger.py"
    release.chmod(0o600)
    release.write_text(STUB_MODULE + "# drift\n", encoding="utf-8")
    release.chmod(0o444)

    status = runtime_status(home)

    assert status["ok"] is False
    assert status["trusted"] is False
    assert status["active"] is False
    assert "active_module_digest_mismatch" in status["findings"]


def test_deactivate_is_authority_bound_preserves_ledger_and_release_and_reads_back(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    assert activate_runtime(home=home, authority_gate=_gate(home, module, digest))["ok"] is True
    ledger = home / "state/personal-ai-os/temporal-commitments.v1.jsonl"
    ledger.write_bytes(b"kept\n")
    deactivate_gate = _gate(home, module, digest, action="deactivate")

    result = deactivate_runtime(home=home, authority_gate=deactivate_gate)

    assert result["ok"] is True
    assert result["deactivated"] is True
    assert result["readback"]["active"] is False
    assert ledger.read_bytes() == b"kept\n"
    release = home / "runtime/personal-ai-os/releases" / digest / "temporal_commitment_ledger.py"
    assert release.exists()
    receipts = (home / "state/personal-ai-os/temporal-commitment-runtime-receipts.jsonl").read_text().splitlines()
    assert [json.loads(row)["outcome"] for row in receipts] == ["activated", "deactivated"]


def test_inspect_runtime_uses_digest_verified_active_module(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    assert activate_runtime(home=home, authority_gate=_gate(home, module, digest))["ok"] is True

    inspection = inspect_runtime(home=home, as_of="2026-07-30T00:00:00+00:00")

    assert inspection["ok"] is True
    assert inspection["trusted"] is True
    assert inspection["inspection"]["schema"] == "TemporalCommitmentLedgerInspection.v1"
    assert inspection["inspection"]["row_count"] == 0
    assert inspection["inspection"]["runtime_mutation"] is False
    assert inspection["inspection"]["external_side_effects_executed"] is False


def test_repeated_exact_activation_is_idempotent_without_duplicate_receipt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    gate = _gate(home, module, digest)
    first = activate_runtime(home=home, authority_gate=gate)
    second = activate_runtime(home=home, authority_gate=gate)

    assert first["outcome"] == "activated"
    assert second["outcome"] == "already_active"
    receipts = (home / "state/personal-ai-os/temporal-commitment-runtime-receipts.jsonl").read_text().splitlines()
    assert len(receipts) == 1


def test_consumed_activation_authority_cannot_reactivate_after_deactivation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    activate_gate = _gate(home, module, digest)
    assert activate_runtime(home=home, authority_gate=activate_gate)["ok"] is True
    assert deactivate_runtime(home=home, authority_gate=_gate(home, module, digest, action="deactivate"))["ok"] is True

    replayed = activate_runtime(home=home, authority_gate=activate_gate)

    assert replayed["ok"] is False
    assert replayed["error_code"] == "authority_replayed"
    assert runtime_status(home)["active"] is False


def test_activation_receipt_failure_rolls_manifest_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    module, digest = _write_module(tmp_path)
    gate = _gate(home, module, digest)

    def fail_receipt(*_args, **_kwargs):
        raise OSError("injected receipt failure")

    monkeypatch.setattr(runtime_mod, "_append_receipt", fail_receipt)
    result = activate_runtime(home=home, authority_gate=gate)

    assert result["ok"] is False
    assert result["error_code"] == "runtime_activation_failed"
    assert not (home / "state/personal-ai-os/temporal-commitment-runtime.json").exists()
    assert runtime_status(home)["active"] is False


def test_activation_rejects_symlinked_runtime_lock(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / "state/personal-ai-os"
    state.mkdir(parents=True)
    module, digest = _write_module(tmp_path)
    outside = tmp_path / "outside.lock"
    outside.write_text("untouched", encoding="utf-8")
    (state / "temporal-commitment-runtime.lock").symlink_to(outside)

    result = activate_runtime(home=home, authority_gate=_gate(home, module, digest))

    assert result["ok"] is False
    assert result["error_code"] == "unsafe_runtime_lock"
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_agent_os_parser_exposes_temporal_ledger_runtime_actions() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_agent_os_parser(subparsers)

    status = parser.parse_args(["agent-os", "temporal-ledger", "status", "--home", "/tmp/h", "--json"])
    assert status.agent_os_action == "temporal-ledger"
    assert status.temporal_ledger_action == "status"
    assert status.home == "/tmp/h"
    assert status.json is True

    activate = parser.parse_args(["agent-os", "temporal-ledger", "activate", "--authority-gate", "/tmp/gate.json"])
    assert activate.temporal_ledger_action == "activate"
    assert activate.authority_gate == "/tmp/gate.json"

    inspect = parser.parse_args(["agent-os", "temporal-ledger", "inspect", "--as-of", "2026-07-30T00:00:00+00:00"])
    assert inspect.temporal_ledger_action == "inspect"
    assert inspect.as_of == "2026-07-30T00:00:00+00:00"
