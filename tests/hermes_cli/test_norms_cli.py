from __future__ import annotations

import json

from agent.norm_governance import NormGovernanceStore
from hermes_cli.norms import main


def test_norms_cli_upsert_snapshot_and_stale_detection(tmp_path, capsys):
    db = tmp_path / "norms.db"

    assert main(["--db", str(db), "upsert", "global:evidence", "Evidence first", "--expected-revision", "0"]) == 0
    upserted = json.loads(capsys.readouterr().out)
    assert upserted["success"] is True
    assert upserted["record"]["revision"] == 1

    assert main(["--db", str(db), "snapshot", "--scope", "global"]) == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["schema"] == "hermes_norm_snapshot_v1"
    assert snapshot["registry_revision"] == 1

    store = NormGovernanceStore(db)
    store.upsert_norm("global:evidence", "Evidence first; agreement is not truth", expected_revision=1)

    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert main(["--db", str(db), "stale", str(snapshot_path)]) == 0
    stale = json.loads(capsys.readouterr().out)
    assert stale["stale_count"] == 1
    assert stale["stale"][0]["reason"] == "revision_changed"


def test_norms_cli_lease_conflict_returns_nonzero(tmp_path, capsys):
    db = tmp_path / "norms.db"
    assert main([
        "--db", str(db), "lease", "acquire", "file:/repo/a.py",
        "--owner-session-id", "session-a", "--ttl", "60", "--now", "100",
    ]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["success"] is True

    rc = main([
        "--db", str(db), "lease", "acquire", "file:/repo/a.py",
        "--owner-session-id", "session-b", "--ttl", "60", "--now", "101",
    ])
    assert rc == 2
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["success"] is False
    assert blocked["error"] == "lease_conflict"
    assert blocked["active_owner_session_id"] == "session-a"


def test_norms_cli_completion_gate_blocks_stale_snapshot(tmp_path, capsys):
    db = tmp_path / "norms.db"
    assert main(["--db", str(db), "upsert", "global:evidence", "v1", "--expected-revision", "0"]) == 0
    capsys.readouterr()
    assert main(["--db", str(db), "snapshot", "--scope", "global"]) == 0
    snapshot = json.loads(capsys.readouterr().out)
    store = NormGovernanceStore(db)
    store.upsert_norm("global:evidence", "v2", expected_revision=1)

    rc = main([
        "--db", str(db), "completion-gate",
        "--task-id", "t1",
        "--snapshot-hash", snapshot["snapshot_hash"],
        "--owner-session-id", "session-a",
    ])

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert payload["gate"]["reason"] == "norm_snapshot_stale"


def test_norms_cli_cleanup_expired_leases(tmp_path, capsys):
    db = tmp_path / "norms.db"
    assert main([
        "--db", str(db), "lease", "acquire", "file:/repo/expired.py",
        "--owner-session-id", "session-a", "--ttl", "1", "--now", "100",
    ]) == 0
    capsys.readouterr()
    assert main([
        "--db", str(db), "lease", "acquire", "file:/repo/live.py",
        "--owner-session-id", "session-b", "--ttl", "100", "--now", "100",
    ]) == 0
    capsys.readouterr()

    assert main(["--db", str(db), "cleanup", "--now", "102"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"success": True, "expired_leases_removed": 1}
