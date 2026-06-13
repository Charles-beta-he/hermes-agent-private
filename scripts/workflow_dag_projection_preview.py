#!/usr/bin/env python3
"""Preview a small workflow DAG without execution authority.

This CLI is intentionally local and read-only by default: it parses a JSON DAG,
validates node references, rejects cycles, maps approval nodes to draft Human
Intervention Packet plans, and emits a deterministic graph hash. It does not
claim tasks, call models, execute tools, mutate ledgers, send external messages,
or write credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-dag-projection-preview"


class DagValidationError(ValueError):
    """Raised when a workflow DAG cannot be safely projected."""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(data: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _normalize_nodes(raw_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, node in enumerate(raw_nodes):
        node_id = node.get("id") or node.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise DagValidationError(f"node[{index}] missing string id")
        if node_id in seen:
            raise DagValidationError(f"duplicate node id: {node_id}")
        seen.add(node_id)
        depends_on = node.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
            raise DagValidationError(f"node {node_id} depends_on must be a list of node ids")
        node_type = node.get("type", "task")
        if not isinstance(node_type, str) or not node_type:
            raise DagValidationError(f"node {node_id} type must be a non-empty string")
        normalized.append(
            {
                "id": node_id,
                "type": node_type,
                "depends_on": sorted(depends_on),
                "title": node.get("title", node_id),
                "risk": node.get("risk", "local_read_only"),
                "side_effect": node.get("side_effect", "none"),
                "question": node.get("question"),
                "options": node.get("options", []),
                "recommend": node.get("recommend"),
            }
        )
    return sorted(normalized, key=lambda item: item["id"])


def _topological_order(nodes: list[dict[str, Any]]) -> list[str]:
    node_ids = {node["id"] for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in node_ids}
    for node in nodes:
        for dep in node["depends_on"]:
            if dep not in node_ids:
                raise DagValidationError(f"node {node['id']} depends on unknown node: {dep}")
            outgoing[dep].append(node["id"])
            indegree[node["id"]] += 1
    queue: deque[str] = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for child in sorted(outgoing[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
        queue = deque(sorted(queue))
    if len(ordered) != len(nodes):
        cyclic = sorted(node_id for node_id, degree in indegree.items() if degree > 0)
        raise DagValidationError(f"cycle detected involving: {', '.join(cyclic)}")
    return ordered


def _approval_packets(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for node in nodes:
        if node["type"] not in {"approval", "human_approval", "human_intervention"}:
            continue
        options = node["options"] if node["options"] else ["Approve this bounded step", "Keep blocked and request a safer local-only alternative"]
        if not all(isinstance(option, str) and option.strip() for option in options):
            raise DagValidationError(f"approval node {node['id']} options must be non-empty strings")
        packets.append(
            {
                "node_id": node["id"],
                "question": node["question"] or f"How should Hermes proceed at workflow approval node {node['id']}?",
                "options": options,
                "recommend": node["recommend"] or options[0],
                "durable_command_template": "hermes mission handoff <mission_id> --question <question> --option <option> --recommend <recommend>",
                "status": "draft_only_not_recorded",
            }
        )
    return packets


def build_preview(dag: dict[str, Any]) -> dict[str, Any]:
    workflow_id = dag.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise DagValidationError("workflow_id must be a non-empty string")
    raw_nodes = dag.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise DagValidationError("nodes must be a non-empty list")
    nodes = _normalize_nodes(raw_nodes)
    order = _topological_order(nodes)
    graph_material = {
        "workflow_id": workflow_id,
        "version": dag.get("version", "draft"),
        "nodes": [
            {
                "id": node["id"],
                "type": node["type"],
                "depends_on": node["depends_on"],
                "risk": node["risk"],
                "side_effect": node["side_effect"],
            }
            for node in nodes
        ],
    }
    packets = _approval_packets(nodes)
    return {
        "status": "PASS",
        "projection_only": True,
        "workflow_id": workflow_id,
        "version": dag.get("version", "draft"),
        "graph_hash": _digest(graph_material),
        "topological_order": order,
        "node_count": len(nodes),
        "approval_packet_count": len(packets),
        "approval_packet_plans": packets,
        "boundary": "no_execution_no_task_claim_no_model_call_no_adapter_write_no_handoff_recorded",
    }


def _write_preview(output_dir: Path, preview: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = preview["graph_hash"].split(":", 1)[1][:16]
    path = output_dir / f"{preview['workflow_id']}.{digest}.preview.json"
    path.write_text(json.dumps(preview, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dag", type=Path, help="JSON workflow DAG fixture to preview")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true", help="write the preview artifact locally")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        preview = build_preview(_load_json(args.dag))
    except (OSError, json.JSONDecodeError, DagValidationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    if args.write:
        preview["written"] = str(_write_preview(args.output_dir, preview))
    print(json.dumps(preview, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
