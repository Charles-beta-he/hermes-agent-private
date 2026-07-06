"""Read-only Personal AI OS / Agent OS CLI surfaces.

This module intentionally exposes projection/readback only. It never executes
capabilities, dispatches external messages, mutates scheduler/taskpool state,
reads secrets, or performs physical-device actions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from hermes_constants import get_default_hermes_root

PROJECT_ID = "hermes-agent-os-control-plane"
PACKET_REL = Path("workflows") / "projects" / PROJECT_ID / "12-personal-ai-os"


def packet_dir(root: Path | None = None) -> Path:
    return (root or get_default_hermes_root()) / PACKET_REL


def _read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    if not path.exists():
        return []
    rows: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_agent_os_state(root: Path | None = None) -> dict[str, Any]:
    """Return a compact read-only state projection for the Personal AI OS packet."""
    packet = packet_dir(root)
    doctor = _read_json(packet / "09-doctor-report.json", {}) or {}
    backlog = _read_json(packet / "07-backlog.json", {"items": []}) or {"items": []}
    capabilities = _read_json(packet / "10-capability-projection.json", {}) or {}
    authority_report = _read_json(packet / "11-authority-ledger-report.json", {}) or {}
    authority_records = _read_jsonl(packet / "11-authority-ledger-projection.jsonl")
    data_vault = _read_json(packet / "12-data-vault-projection.json", {}) or {}
    mission = _read_json(packet / "13-mission-runtime-state.json", {}) or {}
    model_review = _read_json(packet / "14-model-review-projection.json", {}) or {}
    device_agent = _read_json(packet / "16-device-agent-proposal-projection.json", {}) or {}
    ux_path = packet / "15-ux-shell-projection.md"

    items = backlog.get("items", []) if isinstance(backlog, dict) else []
    backlog_by_status: dict[str, int] = {}
    backlog_by_phase: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        backlog_by_status[item.get("status", "unknown")] = backlog_by_status.get(item.get("status", "unknown"), 0) + 1
        backlog_by_phase[item.get("phase", "unknown")] = backlog_by_phase.get(item.get("phase", "unknown"), 0) + 1

    ux_text = ux_path.read_text(encoding="utf-8") if ux_path.exists() else ""
    panels = []
    for line in ux_text.splitlines():
        if line.startswith("### "):
            panels.append(line[4:].strip())

    return {
        "ok": bool(doctor.get("ok")) and packet.exists(),
        "project_id": PROJECT_ID,
        "packet_dir": str(packet),
        "doctor": {
            "ok": doctor.get("ok"),
            "findings": doctor.get("findings", []),
            "domain_entity_count": doctor.get("domain_entity_count"),
            "backlog_count": doctor.get("backlog_count"),
        },
        "backlog": {
            "count": len(items),
            "by_status": backlog_by_status,
            "by_phase": backlog_by_phase,
            "external_dependencies": [i for i in items if isinstance(i, dict) and i.get("status") == "external_dependency_open"],
        },
        "capabilities": {
            "ok": capabilities.get("ok"),
            "node_count": capabilities.get("node_count"),
            "source_counts": capabilities.get("source_counts"),
            "would_execute": capabilities.get("would_execute", capabilities.get("projected_nodes_would_execute")),
        },
        "authority": {
            "ok": authority_report.get("ok"),
            "record_count": authority_report.get("record_count", len(authority_records)),
            "records": authority_records,
            "runtime_mutation": authority_report.get("runtime_mutation"),
            "external_side_effects_executed": authority_report.get("external_side_effects_executed"),
            "payment_or_credential_action_executed": authority_report.get("payment_or_credential_action_executed"),
        },
        "data_vault": {
            "ok": data_vault.get("ok"),
            "item_count": data_vault.get("item_count"),
            "sensitivity_counts": data_vault.get("sensitivity_counts"),
            "raw_secret_exported": data_vault.get("raw_secret_exported"),
            "global_memory_wip_write": data_vault.get("global_memory_wip_write"),
        },
        "mission_runtime": {
            "ok": mission.get("ok"),
            "stage": mission.get("stage"),
            "objective": mission.get("objective"),
            "evidence_nodes": mission.get("evidence_nodes", []),
            "open_boundaries": mission.get("open_boundaries", []),
            "stop_conditions": mission.get("stop_conditions", []),
            "evidence_node_count": len(mission.get("evidence_dag", [])) if isinstance(mission.get("evidence_dag"), list) else None,
            "runtime_mutation": mission.get("runtime_mutation"),
            "external_side_effects_executed": mission.get("external_side_effects_executed"),
            "taskpool_lifecycle_write_executed": mission.get("taskpool_lifecycle_write_executed"),
        },
        "model_review": {
            "ok": model_review.get("ok"),
            "mode": model_review.get("mode"),
            "automation_ceiling": (model_review.get("model_review") or {}).get("automation_ceiling") if isinstance(model_review.get("model_review"), dict) else None,
            "live_model_fanout_executed": model_review.get("live_model_fanout_executed"),
            "external_side_effects_executed": model_review.get("external_side_effects_executed"),
        },
        "ux_shell": {
            "exists": ux_path.exists(),
            "panels": panels,
        },
        "device_agent": {
            "ok": device_agent.get("ok"),
            "mode": device_agent.get("mode"),
            "device_count": len(device_agent.get("device_nodes", [])) if isinstance(device_agent.get("device_nodes"), list) else None,
            "proposal_count": len(device_agent.get("agent_proposal_inbox", [])) if isinstance(device_agent.get("agent_proposal_inbox"), list) else None,
            "device_nodes": device_agent.get("device_nodes", []),
            "agent_proposal_inbox": device_agent.get("agent_proposal_inbox", []),
            "service_contract_stubs": device_agent.get("service_contract_stubs", []),
            "device_physical_effect_executed": device_agent.get("device_physical_effect_executed"),
            "agent_proposal_auto_executed": device_agent.get("agent_proposal_auto_executed"),
        },
        "side_effects_executed": False,
    }


def build_agent_os_receipt(state: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only receipt packet from an already-loaded Agent OS state."""
    return {
        "schema": "PersonalAIOSReceipt.v0",
        "ok": state.get("ok") is True,
        "project_id": state.get("project_id"),
        "packet_dir": state.get("packet_dir"),
        "counts": {
            "capabilities": state["capabilities"].get("node_count"),
            "backlog": state["backlog"].get("count"),
            "authority_records": state["authority"].get("record_count"),
            "evidence_nodes": state["mission_runtime"].get("evidence_node_count"),
            "external_dependencies": len(state["backlog"].get("external_dependencies", [])),
        },
        "boundary": {
            "capability_execution": state["capabilities"].get("would_execute") is True,
            "external_dispatch": state["authority"].get("external_side_effects_executed") is True,
            "runtime_mutation": state["authority"].get("runtime_mutation") is True,
            "payment_or_credential_action": state["authority"].get("payment_or_credential_action_executed") is True,
            "physical_device_effect": state["device_agent"].get("device_physical_effect_executed") is True,
        },
        "open_external_dependencies": state["backlog"].get("external_dependencies", []),
        "doctor_findings": state["doctor"].get("findings", []),
        "side_effects_executed": False,
    }


def _emit(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if isinstance(payload, dict):
        print(f"Personal AI OS: {'OK' if payload.get('ok') else 'NOT OK'}")
        print(f"packet: {payload.get('packet_dir')}")
        print(f"backlog: {payload['backlog']['by_status']}")
        print(f"capabilities: {payload['capabilities']['node_count']} nodes")
        print(f"authority: {payload['authority']['record_count']} records")
        print(f"data vault: {payload['data_vault']['item_count']} items")
        print(f"mission evidence: {payload['mission_runtime']['evidence_node_count']} nodes")
        print(f"model ceiling: {payload['model_review']['automation_ceiling']}")
        print(f"ux panels: {', '.join(payload['ux_shell']['panels'])}")


def cmd_agent_os(args: argparse.Namespace) -> int:
    action = getattr(args, "agent_os_action", None) or "status"
    state = load_agent_os_state()
    if action == "status":
        _emit(state, as_json=bool(getattr(args, "json", False)))
        return 0 if state.get("ok") else 1
    if action == "doctor":
        payload = state["doctor"]
        _emit(payload, as_json=bool(getattr(args, "json", False)))
        return 0 if payload.get("ok") else 1
    if action == "backlog":
        payload = state["backlog"]
        _emit(payload, as_json=True)
        return 0
    if action == "panels":
        payload = state["ux_shell"]
        _emit(payload, as_json=True)
        return 0 if payload.get("exists") else 1
    if action == "capabilities":
        payload = state["capabilities"]
        _emit(payload, as_json=True)
        return 0 if payload.get("ok") else 1
    if action == "receipt":
        payload = build_agent_os_receipt(state)
        _emit(payload, as_json=bool(getattr(args, "json", False)))
        return 0 if payload.get("ok") else 1
    raise SystemExit(f"unknown agent-os action: {action}")


def build_agent_os_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "agent-os",
        help="Read-only Personal AI OS / Web4.0 Agent OS projections",
        description=(
            "Inspect the local Personal AI OS packet. This command is read-only: "
            "it never invokes capabilities, external dispatch, scheduler/taskpool lifecycle writes, "
            "secret entry, payment, or physical-device effects."
        ),
    )
    sub = parser.add_subparsers(dest="agent_os_action")

    status = sub.add_parser("status", help="Show consolidated Agent OS projection status")
    status.add_argument("--json", action="store_true", help="Emit JSON")

    doctor = sub.add_parser("doctor", help="Show packet doctor summary")
    doctor.add_argument("--json", action="store_true", help="Emit JSON")

    sub.add_parser("backlog", help="Emit backlog summary as JSON")
    sub.add_parser("panels", help="Emit UX shell panel projection as JSON")
    sub.add_parser("capabilities", help="Emit capability projection summary as JSON")
    receipt = sub.add_parser("receipt", help="Emit read-only receipt packet")
    receipt.add_argument("--json", action="store_true", help="Emit JSON")

    parser.set_defaults(func=cmd_agent_os)
    return parser
