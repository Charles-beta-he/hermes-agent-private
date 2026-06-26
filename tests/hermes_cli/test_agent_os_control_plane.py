from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.agent_os_control_plane import doctor, route_shadow, status


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _seed_agent_os(home: Path, *, runtime_mutation: bool = False, shadow_executes: bool = False) -> None:
    project = "hermes-agent-os-control-plane"
    root = home / "workflows" / "projects" / project
    _write_json(
        home / "project-profiles" / f"{project}.json",
        {
            "project": project,
            "status": "p0-read-only-projection-validated+p1-sample-shadow-validated",
            "ssot_root": str(root),
            "progress_ledger": str(root / "progress-ledger.md"),
            "current_components": {
                "runtime_implementation": "advisory_readback_only/default-off",
                "capability_graph_projection": "validated-local-read-only-slice/2-nodes",
                "capability_intent": "shadow-only",
            },
        },
    )
    _write_json(
        root / "06-projection" / "capability_graph_projection.json",
        {
            "project": project,
            "runtime_mutation": runtime_mutation,
            "nodes": [
                {
                    "id": "tool:send_message",
                    "kind": "tool",
                    "responsibility": "Send messages to connected platforms",
                    "side_effects": "S4",
                    "source_ref": "test",
                },
                {
                    "id": "skill:hermes-agent",
                    "kind": "skill",
                    "responsibility": "Operate Hermes Agent control plane",
                    "side_effects": "S0",
                    "source_ref": "test",
                },
            ],
        },
    )
    _write_json(root / "06-projection" / "doctor_report.json", {"ok": True, "findings": []})
    _write_json(
        root / "07-shadow" / "router-shadow-report.json",
        {"ok": True, "records": 1, "blocked_by_policy": 0, "would_execute_anything": shadow_executes},
    )


def test_agent_os_status_reads_projection_without_runtime_mutation(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    payload = status(tmp_path)
    assert payload["ok"] is True
    assert payload["mode"] == "advisory_readback_only"
    assert payload["projection"]["node_count"] == 2
    assert payload["runtime_effect"].startswith("none")


def test_agent_os_doctor_fails_closed_on_runtime_mutation(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path, runtime_mutation=True)
    payload = doctor(tmp_path)
    assert payload["ok"] is False
    assert any(f["check"] == "runtime_mutation_default_off" for f in payload["findings"])


def test_agent_os_doctor_fails_closed_if_shadow_report_would_execute(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path, shadow_executes=True)
    payload = doctor(tmp_path)
    assert payload["ok"] is False
    assert any(f["check"] == "shadow_no_execute" for f in payload["findings"])


def test_agent_os_route_shadow_blocks_external_side_effects(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    payload = route_shadow("send a Telegram message", tmp_path)
    assert payload["ok"] is True
    assert payload["would_execute"] is False
    assert payload["intent"]["risk_class"] == "high"
    assert any(f["gate"] == "default_off_external_side_effects" for f in payload["policy_findings"])
