#!/usr/bin/env python3
"""Build a governed capability-absorption registry from taskpool markdown.

This is a read-only backfill/projection tool. It scans task markdown files,
extracts external-source/capability absorption records, writes a JSON registry
and a small dashboard, and does not mutate task lifecycle state.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DEFAULT_TASKS_DIR = Path("/Users/charles/Documents/brain/05-实践与项目/tasks")
DEFAULT_OUT_DIR = Path("/Users/charles/.hermes/hermes-agent/docs/private/capability-absorption-registry")

ABSORPTION_MARKERS = (
    "repo_capability_absorption",
    "吸收",
    "可吸收",
    "仓库精读",
    "开源",
    "github.com",
    "source_url",
    "source-url",
    "评估-",
    "学习-",
    "技术储备",
    "视频提炼",
    "插件能力",
    "能力清单",
)

TARGET_SURFACES = ("adapter", "cli_wrapper", "core", "mcp", "sidecar", "skill", "skill-graph", "taskpool", "local-brain", "provider-quota")
INTEGRATION_LEVELS = ("evaluate", "integrate", "productionize", "reproduce", "wrap")


def _split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw = parts[1]
    body = parts[2]
    meta: Dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip() or line.startswith(" ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        meta[key] = _parse_scalar(value)
    return meta, body


def _parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        try:
            return ast.literal_eval(value)
        except Exception:
            return value
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        try:
            return ast.literal_eval(value)
        except Exception:
            return value.strip('"\'')
    return value


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return [str(value)]


def _title_from(path: Path, body: str) -> str:
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return path.stem


def _is_absorption_task(path: Path, meta: Dict[str, Any], body: str) -> bool:
    haystack = " ".join([
        path.name,
        str(meta.get("capability_delta", "")),
        str(meta.get("acceptance_evidence", "")),
        str(meta.get("acceptance_criteria", "")),
        body[:6000],
    ]).lower()
    return any(marker.lower() in haystack for marker in ABSORPTION_MARKERS)


def _extract_source_refs(meta: Dict[str, Any], body: str) -> List[str]:
    refs = []
    for key in ("evidence_refs", "source_refs"):
        refs.extend(_as_list(meta.get(key)))
    refs.extend(f"url:{url}" for url in re.findall(r"https?://[^\s\]')\",]+", body[:3000]))
    seen = set()
    out = []
    for ref in refs:
        ref = ref.strip()
        if not ref or ref in seen:
            continue
        if ref.startswith("http"):
            ref = f"url:{ref}"
        seen.add(ref)
        out.append(ref)
    return out


def _infer_target_surface(meta: Dict[str, Any], text: str) -> str:
    arch = str(meta.get("architecture_target", "")).strip()
    if arch in {"taskpool", "skill-graph", "local-brain", "provider-quota"}:
        return arch
    lowered = text.lower()
    for surface in TARGET_SURFACES:
        if surface in lowered:
            return surface
    return arch or "unknown"


def _infer_integration_level(text: str) -> str:
    lowered = text.lower()
    match = re.search(r"repo_capability_absorption:[^\s]+->[^:]+:([a-z_]+)", lowered)
    if match and match.group(1) in INTEGRATION_LEVELS:
        return match.group(1)
    for level in INTEGRATION_LEVELS:
        if level in lowered:
            return level
    if any(word in text for word in ("评估", "分析", "gap", "边界")):
        return "evaluate"
    if any(word in text for word in ("实现", "接入", "落地")):
        return "integrate"
    return "evaluate"


def _infer_absorption_layer(meta: Dict[str, Any], text: str) -> str:
    target = _infer_target_surface(meta, text)
    lowered = text.lower()
    if any(term in lowered for term in ("cli", "adapter", "mcp", "module", "模块", "控制平面", "workflow_plan", "harness", "registry")):
        return "module"
    if target in {"taskpool", "local-brain", "provider-quota", "core", "adapter", "mcp", "cli_wrapper"}:
        return "module"
    if target in {"skill", "skill-graph"}:
        return "workflow_pattern"
    if any(term in lowered for term in ("思想", "理念", "原则")):
        return "idea"
    return "design_pattern"


def _task_id_from_path(path: Path) -> str:
    name = path.name
    return name[:-3] if name.endswith(".md") else path.stem


def _record_from_task(path: Path) -> Dict[str, Any] | None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    meta, body = _split_frontmatter(text)
    if not _is_absorption_task(path, meta, body):
        return None
    title = _title_from(path, body)
    capability = str(meta.get("capability_delta") or title)
    combined = " ".join([path.name, title, capability, body[:1200]])
    source_refs = _extract_source_refs(meta, body)
    return {
        "task_id": _task_id_from_path(path),
        "task_path": str(path),
        "title": title,
        "status": str(meta.get("status") or "unknown"),
        "priority": str(meta.get("priority") or ""),
        "project": str(meta.get("project") or ""),
        "architecture_target": str(meta.get("architecture_target") or ""),
        "updated": str(meta.get("updated") or meta.get("created") or ""),
        "capability_claim": capability,
        "target_surface": _infer_target_surface(meta, combined),
        "integration_level": _infer_integration_level(combined),
        "absorption_layer": _infer_absorption_layer(meta, combined),
        "source_refs": source_refs,
        "evidence_refs": _as_list(meta.get("evidence_refs")),
        "acceptance_criteria": _as_list(meta.get("acceptance_criteria")) or _as_list(meta.get("acceptance_evidence")),
    }


def build_registry(*, tasks_dir: Path | str = DEFAULT_TASKS_DIR) -> Dict[str, Any]:
    tasks_dir = Path(tasks_dir)
    records = []
    for path in sorted(tasks_dir.glob("*.md")):
        record = _record_from_task(path)
        if record:
            records.append(record)
    by_status = Counter(record["status"] for record in records)
    by_layer = Counter(record["absorption_layer"] for record in records)
    by_target = Counter(record["target_surface"] for record in records)
    open_statuses = {"pending", "ready", "claimed", "running", "review", "blocked", "retry"}
    open_records = [r for r in records if r["status"] in open_statuses]
    return {
        "schema": "capability_absorption_registry_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tasks_dir": str(tasks_dir),
        "summary": {
            "record_count": len(records),
            "open_count": len(open_records),
            "by_status": dict(sorted(by_status.items())),
            "by_absorption_layer": dict(sorted(by_layer.items())),
            "by_target_surface": dict(sorted(by_target.items())),
        },
        "records": records,
        "open_records": sorted(open_records, key=lambda r: (r.get("priority") or "P9", r.get("updated") or "", r["task_id"])),
        "recent_done": sorted([r for r in records if r["status"] == "done"], key=lambda r: r.get("updated") or "", reverse=True)[:25],
        "safety_boundary": "registry projection only; no task lifecycle mutation, no external repo execution, no skill/memory writes",
    }


def _line_for_record(record: Dict[str, Any]) -> str:
    claim = record.get("capability_claim", "")
    if len(claim) > 120:
        claim = claim[:117] + "..."
    return f"- `{record['status']}` `{record.get('priority','')}` **{record['task_id']}** — {claim}"


def render_dashboard(registry: Dict[str, Any]) -> str:
    summary = registry["summary"]
    lines = [
        "# Capability Absorption progress",
        "",
        f"Generated: `{registry['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Records: **{summary['record_count']}**",
        f"- Open: **{summary['open_count']}**",
        f"- Status: `{json.dumps(summary['by_status'], ensure_ascii=False)}`",
        f"- Layers: `{json.dumps(summary['by_absorption_layer'], ensure_ascii=False)}`",
        f"- Target surfaces: `{json.dumps(summary['by_target_surface'], ensure_ascii=False)}`",
        "",
        "## Open absorption tasks",
        "",
    ]
    if registry["open_records"]:
        lines.extend(_line_for_record(r) for r in registry["open_records"])
    else:
        lines.append("- none")
    lines.extend(["", "## Recent done", ""])
    if registry["recent_done"]:
        lines.extend(_line_for_record(r) for r in registry["recent_done"][:15])
    else:
        lines.append("- none")
    lines.extend(["", "## Safety boundary", "", f"- {registry['safety_boundary']}", ""])
    return "\n".join(lines)


def write_artifacts(registry: Dict[str, Any], out_dir: Path | str = DEFAULT_OUT_DIR) -> Dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    registry_path = out_dir / "capability_absorption_registry_v1.json"
    dashboard_path = out_dir / "capability_absorption_dashboard.md"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dashboard_path.write_text(render_dashboard(registry), encoding="utf-8")
    return {"registry_json": str(registry_path), "dashboard_md": str(dashboard_path)}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true", help="print compact JSON summary")
    args = parser.parse_args(argv)
    registry = build_registry(tasks_dir=Path(args.tasks_dir))
    artifacts = write_artifacts(registry, Path(args.out_dir))
    result = {"status": "OK", "summary": registry["summary"], "artifacts": artifacts, "safety_boundary": registry["safety_boundary"]}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OK records={registry['summary']['record_count']} open={registry['summary']['open_count']}")
        print(f"registry: {artifacts['registry_json']}")
        print(f"dashboard: {artifacts['dashboard_md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
