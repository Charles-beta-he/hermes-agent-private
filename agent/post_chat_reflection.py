"""Post-chat reflection candidate ledger and nightly synthesis.

This module implements the safe half of the "agent dreaming" loop: collect
post-turn learning signals as auditable candidates, then let a later synthesis
pass decide whether anything should be promoted.  It intentionally does not
write MEMORY.md, USER.md, skills, or taskpool directly.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_NIGHTLY_SCRIPT_NAME = "post_chat_reflection_nightly.py"
_NIGHTLY_JOB_NAME = "Hermes post-chat reflection nightly synthesis"

from hermes_cli.config import load_config
from hermes_constants import get_hermes_home

Candidate = Dict[str, Any]

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{8,}\b"),
    re.compile(r"\b(?:api[_-]?key|token|secret|password|cookie)\b", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_EPHEMERAL_PATTERNS = [
    re.compile(r"\b(?:PR|issue|commit|SHA)\b\s*#?[A-Za-z0-9._/-]+", re.I),
    re.compile(r"(?:本轮|刚刚|当前任务|这次任务|已完成|合了|closed|merged|done)", re.I),
    re.compile(r"\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b", re.I),
]
_CORRECTION_PATTERNS = [
    re.compile(r"(以后|下次|今后).*(不要|别|必须|先|严禁|需要)"),
    re.compile(r"(不要|别|严禁).*(再|直接|写入|模拟|口头)"),
    re.compile(r"(必须|需要|应当|先).*(真实|验证|fetch|pull|外调|确认)"),
]
_RED_LINE_PATTERNS = [re.compile(r"(严禁|红线|不能|不得|禁止)")]
_HABIT_PATTERNS = [re.compile(r"(我习惯|我偏好|我喜欢|我讨厌|默认|倾向)")]
_WORKFLOW_PATTERNS = [re.compile(r"(流程|步骤|SOP|沉淀|skill|技能|方法|工作流)", re.I)]
_TASK_PATTERNS = [re.compile(r"(后续|待办|任务池|加个任务|需要实现|落地)")]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_candidate_ledger_path() -> Path:
    return get_hermes_home() / "state" / "post_chat_reflection_candidates.jsonl"


def default_nightly_report_path() -> Path:
    return get_hermes_home() / "reports" / "post_chat_reflection_nightly.json"


def default_nightly_html_path() -> Path:
    return get_hermes_home() / "reports" / "post_chat_reflection_nightly.html"


def default_reflection_status_path() -> Path:
    return get_hermes_home() / "state" / "post_chat_reflection_status.json"


def default_backfill_status_path() -> Path:
    return get_hermes_home() / "state" / "post_chat_reflection_backfill_status.json"


def post_chat_reflection_config() -> Dict[str, Any]:
    try:
        cfg = load_config() or {}
    except Exception:
        return {"enabled": True}
    section = cfg.get("post_chat_reflection", {}) if isinstance(cfg, dict) else {}
    if not isinstance(section, dict):
        return {"enabled": True}
    merged = {"enabled": True}
    merged.update(section)
    return merged


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _stable_id(*parts: str) -> str:
    raw = "\n".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def _expand_path(value: Path | str) -> Path:
    return Path(value).expanduser()


def _contains_secret(text: str) -> bool:
    return any(p.search(text or "") for p in _SECRET_PATTERNS)


def _is_ephemeral(text: str) -> bool:
    return any(p.search(text or "") for p in _EPHEMERAL_PATTERNS)


def _classify_text(text: str) -> tuple[str, str, str]:
    """Return (type, explicitness, proposed_destination)."""
    if any(p.search(text) for p in _RED_LINE_PATTERNS):
        return "red_line", "explicit", "memory"
    if any(p.search(text) for p in _CORRECTION_PATTERNS):
        destination = "skill" if any(p.search(text) for p in _WORKFLOW_PATTERNS) else "memory"
        return "correction", "explicit", destination
    if any(p.search(text) for p in _WORKFLOW_PATTERNS):
        return "workflow_candidate", "explicit", "skill"
    if any(p.search(text) for p in _TASK_PATTERNS):
        return "task_candidate", "explicit", "taskpool"
    if any(p.search(text) for p in _HABIT_PATTERNS):
        return "habit_candidate", "explicit", "user_profile"
    return "habit_candidate", "inferred", "staging"


def _scope_for_text(text: str) -> str:
    lower = text.lower()
    if "hermes" in lower or "agent" in lower:
        return "hermes"
    if "uvisa" in lower or "lark" in lower or "飞书" in lower:
        return "project"
    if "本轮" in text or "这次" in text:
        return "session"
    return "global"


def _safe_text_from_content(content: Any) -> str:
    """Extract only user-authored text from chat content shapes.

    Never stringify arbitrary dict/list payloads: multimodal messages may carry
    data URLs, file handles, or structured metadata that must not enter the
    reflection ledger.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                text = item.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict) and content.get("type") in {"text", "input_text"}:
        text = content.get("text", "")
        return text if isinstance(text, str) else ""
    return ""


def _candidate_from_user_text(text: str, *, session_id: str, turn_index: int) -> Optional[Candidate]:
    stripped = " ".join((text or "").split())
    if not stripped:
        return None
    ctype, explicitness, destination = _classify_text(stripped)
    status = "pending"
    rejection_reason = ""
    sensitivity = "none"
    content = stripped[:1000]
    if _contains_secret(stripped):
        status = "rejected"
        rejection_reason = "sensitive"
        sensitivity = "credential_like"
        destination = "discard"
        content = "[REDACTED_SENSITIVE]"
    elif _is_ephemeral(stripped):
        status = "rejected"
        rejection_reason = "ephemeral"
        destination = "discard"
    candidate = {
        "id": _stable_id(session_id, str(turn_index), content, rejection_reason),
        "created_at": _utc_now_iso(),
        "source_session_id": session_id,
        "evidence_refs": [f"session:{session_id}:turn:{turn_index}"],
        "type": ctype,
        "content": content,
        "explicitness": explicitness,
        "scope": _scope_for_text(stripped),
        "sensitivity_level": sensitivity,
        "proposed_destination": destination,
        "promotion_policy": "never_promote" if status == "rejected" else "needs_gate",
        "status": status,
        "rejection_reason": rejection_reason,
    }
    return candidate


def build_post_chat_candidates(messages: List[Dict[str, Any]], *, session_id: str = "") -> List[Candidate]:
    """Extract bounded, suggest-only learning candidates from a conversation snapshot."""
    candidates: List[Candidate] = []
    for idx, msg in enumerate(messages or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _safe_text_from_content(msg.get("content", ""))
        candidate = _candidate_from_user_text(text, session_id=session_id or "unknown", turn_index=idx)
        if candidate and (candidate["status"] == "rejected" or candidate["type"] != "habit_candidate" or candidate["explicitness"] == "explicit"):
            candidates.append(candidate)
    return candidates[:20]


def evaluate_candidate_promotion(candidate: Candidate) -> Dict[str, Any]:
    """Pure promotion gate.  Returns a decision; never performs writes."""
    if not candidate or candidate.get("status") == "rejected":
        return {"decision": "reject", "target": "discard", "reasons": [candidate.get("rejection_reason", "rejected") if candidate else "empty"]}
    text = str(candidate.get("content") or "")
    reasons: List[str] = []
    if _contains_secret(text) or candidate.get("sensitivity_level") in {"secret_like", "credential_like"}:
        return {"decision": "reject", "target": "discard", "reasons": ["sensitive"]}
    if _is_ephemeral(text) or candidate.get("scope") == "session":
        return {"decision": "reject", "target": "discard", "reasons": ["ephemeral"]}
    evidence_count = len(set(candidate.get("evidence_refs") or []))
    ctype = candidate.get("type")
    explicitness = candidate.get("explicitness") or ("explicit" if ctype in {"red_line", "correction", "workflow_candidate", "task_candidate"} else "inferred")
    if explicitness != "explicit" and evidence_count < 3:
        return {"decision": "needs_more_evidence", "target": "staging", "reasons": ["inferred_requires_3_evidence_refs"]}
    if ctype in {"red_line", "correction"} and evidence_count >= 1:
        target = candidate.get("proposed_destination") or "memory"
        if target == "user_profile":
            target = "memory"
        return {"decision": "promote_allowed", "target": target, "reasons": ["explicit_high_value_signal"]}
    if ctype == "workflow_candidate":
        return {"decision": "review_required", "target": "skill", "reasons": ["workflow_candidates_need_skill_review"]}
    if ctype == "task_candidate":
        return {"decision": "review_required", "target": "taskpool", "reasons": ["task_candidates_need_taskpool_review"]}
    reasons.append("low_confidence_or_low_value")
    return {"decision": "needs_more_evidence", "target": "staging", "reasons": reasons}


class PostChatCandidateLedger:
    def __init__(self, path: Optional[Path | str] = None):
        self.path = _expand_path(path) if path else default_candidate_ledger_path()

    def append_many(self, candidates: Iterable[Candidate]) -> int:
        items = list(candidates or [])
        if not items:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        return len(items)

    def read_all(self) -> List[Candidate]:
        if not self.path.exists():
            return []
        rows: List[Candidate] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
        return rows

    def existing_ids(self) -> set[str]:
        return {str(row.get("id")) for row in self.read_all() if row.get("id")}


def _bounded_positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, maximum)


def _write_json(path: Path | str, payload: Dict[str, Any]) -> None:
    target = _expand_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def backfill_historical_reflection(
    *,
    max_sessions: int = 50,
    source: Optional[str] = None,
    include_children: bool = False,
    ledger_path: Optional[str] = None,
    status_path: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    session_db: Any = None,
) -> Dict[str, Any]:
    """Dreaming-style historical backfill into the suggest-only ledger.

    This collector reads bounded SessionDB history, extracts the same scrubbed
    candidate shape as the post-chat path, dedupes against the ledger, and
    writes only candidates plus a status artifact. Durable mutation still
    requires nightly synthesis and manual promotion review.
    """
    cfg = dict(post_chat_reflection_config())
    if config:
        cfg.update(config)
    if not _truthy(cfg.get("enabled"), default=True):
        return {"state": "disabled", "suggest_only": True, "candidates_written": 0}

    limit = _bounded_positive_int(
        max_sessions if max_sessions is not None else cfg.get("backfill_max_sessions"),
        default=50,
        maximum=500,
    )
    ledger = PostChatCandidateLedger(ledger_path or cfg.get("ledger_path"))
    status_target = _expand_path(status_path or cfg.get("backfill_status_path") or default_backfill_status_path())

    try:
        db = session_db
        if db is None:
            from hermes_state import SessionDB
            db = SessionDB()
        sessions = db.list_sessions_rich(
            source=source or "",
            limit=limit,
            include_children=include_children,
            order_by_last_active=True,
        )
    except Exception as exc:
        status = {
            "state": "failed",
            "suggest_only": True,
            "error": str(exc),
            "sessions_scanned": 0,
            "candidates_written": 0,
            "ledger_path": str(ledger.path),
            "status_path": str(status_target),
            "updated_at": _utc_now_iso(),
        }
        _write_json(status_target, status)
        return status

    existing = ledger.existing_ids()
    candidates_to_write: List[Candidate] = []
    sessions_scanned = 0
    per_session: List[Dict[str, Any]] = []
    for sess in sessions or []:
        if not isinstance(sess, dict):
            continue
        sid = str(sess.get("id") or "")
        if not sid:
            continue
        try:
            messages = db.get_messages(sid)
        except Exception as exc:
            per_session.append({"session_id": sid, "state": "failed", "error": str(exc)})
            continue
        sessions_scanned += 1
        extracted = build_post_chat_candidates(messages, session_id=sid)
        new_count = 0
        for candidate in extracted:
            cid = str(candidate.get("id") or "")
            if not cid or cid in existing:
                continue
            candidate = dict(candidate)
            candidate["source"] = "historical_backfill"
            candidate["backfill_policy"] = "suggest_only_manual_promotion"
            candidates_to_write.append(candidate)
            existing.add(cid)
            new_count += 1
        per_session.append({
            "session_id": sid,
            "title": sess.get("title") or "",
            "candidate_count": len(extracted),
            "new_candidate_count": new_count,
        })

    written = ledger.append_many(candidates_to_write)
    status = {
        "state": "completed" if sessions_scanned else "no_sessions",
        "suggest_only": True,
        "dreaming_style": True,
        "promotion_gate": "nightly_synthesis_then_manual_review",
        "max_sessions": limit,
        "source": source or "all",
        "include_children": include_children,
        "sessions_scanned": sessions_scanned,
        "sessions_seen": len(sessions or []),
        "candidates_found": sum(int(item.get("candidate_count", 0) or 0) for item in per_session),
        "candidates_written": written,
        "candidate_ids": [str(c.get("id")) for c in candidates_to_write if c.get("id")],
        "ledger_path": str(ledger.path),
        "status_path": str(status_target),
        "updated_at": _utc_now_iso(),
        "sessions": per_session[:limit],
    }
    _write_json(status_target, status)
    return status


def _cluster_key(candidate: Candidate) -> str:
    normalized = re.sub(r"\s+", " ", str(candidate.get("content", "")).strip().lower())
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def synthesize_nightly_reflection(candidates: List[Candidate]) -> Dict[str, Any]:
    pending = [c for c in candidates or [] if c.get("status") == "pending"]
    clusters_by_key: Dict[str, List[Candidate]] = defaultdict(list)
    for candidate in pending:
        clusters_by_key[_cluster_key(candidate)].append(candidate)
    clusters: List[Dict[str, Any]] = []
    for key, items in clusters_by_key.items():
        merged = dict(items[-1])
        evidence: List[str] = []
        for item in items:
            evidence.extend(str(ref) for ref in item.get("evidence_refs", []) if ref)
        merged["evidence_refs"] = sorted(set(evidence))
        decision = evaluate_candidate_promotion(merged)
        clusters.append({
            "cluster_id": key,
            "count": len(items),
            "type": merged.get("type"),
            "scope": merged.get("scope"),
            "content": merged.get("content"),
            "evidence_refs": merged.get("evidence_refs", []),
            "decision": decision,
            "dissent": [],
        })
    clusters.sort(key=lambda c: (-int(c["count"]), str(c["content"])))
    return {
        "generated_at": _utc_now_iso(),
        "total_candidates": len(candidates or []),
        "pending_candidates": len(pending),
        "clusters": clusters,
        "metrics": {
            "promote_allowed": sum(1 for c in clusters if c["decision"]["decision"] == "promote_allowed"),
            "review_required": sum(1 for c in clusters if c["decision"]["decision"] == "review_required"),
            "needs_more_evidence": sum(1 for c in clusters if c["decision"]["decision"] == "needs_more_evidence"),
            "rejected": sum(1 for c in candidates or [] if c.get("status") == "rejected"),
        },
    }


def render_nightly_reflection_html(report: Dict[str, Any]) -> str:
    rows = []
    for cluster in report.get("clusters", []):
        decision = cluster.get("decision", {}) if isinstance(cluster.get("decision"), dict) else {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(cluster.get('count', '')))}</td>"
            f"<td>{html.escape(str(cluster.get('type', '')))}</td>"
            f"<td>{html.escape(str(cluster.get('scope', '')))}</td>"
            f"<td>{html.escape(str(decision.get('decision', '')))}</td>"
            f"<td>{html.escape(str(decision.get('target', '')))}</td>"
            f"<td>{html.escape(str(cluster.get('content', '')))}</td>"
            "</tr>"
        )
    return """<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Hermes Post-chat Reflection</title>
<style>body{font-family:system-ui,sans-serif;margin:2rem}table{border-collapse:collapse;width:100%%}td,th{border:1px solid #ddd;padding:.5rem;vertical-align:top}th{background:#f5f5f5}</style>
</head><body>
<h1>Hermes Post-chat Reflection Nightly Report</h1>
<p>Generated: %s</p>
<pre>%s</pre>
<table><thead><tr><th>Count</th><th>Type</th><th>Scope</th><th>Decision</th><th>Target</th><th>Content</th></tr></thead><tbody>%s</tbody></table>
</body></html>""" % (
        html.escape(str(report.get("generated_at", ""))),
        html.escape(json.dumps(report.get("metrics", {}), ensure_ascii=False, indent=2)),
        "".join(rows),
    )


def write_nightly_report(report: Dict[str, Any], *, json_path: Optional[Path | str] = None, html_path: Optional[Path | str] = None) -> Dict[str, str]:
    jp = _expand_path(json_path) if json_path else default_nightly_report_path()
    hp = _expand_path(html_path) if html_path else default_nightly_html_path()
    jp.parent.mkdir(parents=True, exist_ok=True)
    hp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    hp.write_text(render_nightly_reflection_html(report), encoding="utf-8")
    return {"json_report_path": str(jp), "html_report_path": str(hp)}


def run_nightly_synthesis(*, ledger_path: Optional[str] = None, json_path: Optional[str] = None, html_path: Optional[str] = None) -> Dict[str, Any]:
    ledger = PostChatCandidateLedger(ledger_path)
    report = synthesize_nightly_reflection(ledger.read_all())
    report.update(write_nightly_report(report, json_path=json_path, html_path=html_path))
    return report


def build_promotion_review(report: Dict[str, Any]) -> Dict[str, Any]:
    """Build a manual promotion packet from a nightly report.

    This is the explicit boundary between analysis and durable mutation: the
    packet is auditable and manual-only. It never calls memory/skill/taskpool
    tools and never mutates candidate state.
    """
    actions: List[Dict[str, Any]] = []
    for cluster in report.get("clusters", []) if isinstance(report, dict) else []:
        if not isinstance(cluster, dict):
            continue
        decision = cluster.get("decision", {}) if isinstance(cluster.get("decision"), dict) else {}
        verdict = decision.get("decision")
        target = decision.get("target")
        if verdict not in {"promote_allowed", "review_required"}:
            continue
        actions.append({
            "cluster_id": cluster.get("cluster_id"),
            "decision": verdict,
            "target": target,
            "type": cluster.get("type"),
            "scope": cluster.get("scope"),
            "content": cluster.get("content"),
            "evidence_refs": cluster.get("evidence_refs", []),
            "manual_only": True,
            "suggested_action": _suggested_manual_action(str(target or ""), str(cluster.get("content") or "")),
        })
    return {
        "generated_at": _utc_now_iso(),
        "source_report_generated_at": report.get("generated_at") if isinstance(report, dict) else None,
        "manual_only": True,
        "actions": actions,
        "metrics": {
            "manual_actions": len(actions),
            "memory_or_profile": sum(1 for a in actions if a.get("target") in {"memory", "user_profile"}),
            "skill": sum(1 for a in actions if a.get("target") == "skill"),
            "taskpool": sum(1 for a in actions if a.get("target") == "taskpool"),
        },
    }


def _suggested_manual_action(target: str, content: str) -> str:
    if target in {"memory", "user_profile"}:
        return "Review evidence_refs, then use memory.add/replace manually if durable and non-ephemeral."
    if target == "skill":
        return "Review evidence_refs, then create or patch a Hermes skill manually if this is a reusable workflow."
    if target == "taskpool":
        return "Review evidence_refs, then enqueue a task manually with dependencies and acceptance criteria."
    return "Review evidence_refs manually; do not auto-promote."


def render_promotion_review_markdown(packet: Dict[str, Any]) -> str:
    lines = [
        "# Hermes Post-chat Reflection Promotion Review",
        "",
        f"Generated: {packet.get('generated_at', '')}",
        "",
        "Manual-only: this artifact is an audit packet, not an auto-promotion mechanism.",
        "",
        "## Metrics",
        "",
        "```json",
        json.dumps(packet.get("metrics", {}), ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Actions",
        "",
    ]
    for action in packet.get("actions", []):
        lines.extend([
            f"### {action.get('cluster_id')} → {action.get('target')} ({action.get('decision')})",
            "",
            f"Type: {action.get('type')} / Scope: {action.get('scope')}",
            "",
            f"Content: {action.get('content')}",
            "",
            "Evidence refs:",
        ])
        for ref in action.get("evidence_refs", []):
            lines.append(f"- {ref}")
        lines.extend(["", f"Suggested manual action: {action.get('suggested_action')}", ""])
    if not packet.get("actions"):
        lines.append("No promotion or review-required actions.")
    return "\n".join(lines).rstrip() + "\n"


def write_promotion_review(packet: Dict[str, Any], *, json_path: Optional[Path | str] = None, markdown_path: Optional[Path | str] = None) -> Dict[str, str]:
    jp = _expand_path(json_path) if json_path else get_hermes_home() / "reports" / "post_chat_reflection_promotion_review.json"
    mp = _expand_path(markdown_path) if markdown_path else get_hermes_home() / "reports" / "post_chat_reflection_promotion_review.md"
    jp.parent.mkdir(parents=True, exist_ok=True)
    mp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    mp.write_text(render_promotion_review_markdown(packet), encoding="utf-8")
    return {"promotion_review_json_path": str(jp), "promotion_review_markdown_path": str(mp)}


def run_promotion_review(*, ledger_path: Optional[str] = None, json_path: Optional[str] = None, markdown_path: Optional[str] = None) -> Dict[str, Any]:
    ledger = PostChatCandidateLedger(ledger_path)
    report = synthesize_nightly_reflection(ledger.read_all())
    packet = build_promotion_review(report)
    packet.update(write_promotion_review(packet, json_path=json_path, markdown_path=markdown_path))
    return packet


def nightly_script_path() -> Path:
    return get_hermes_home() / "scripts" / _NIGHTLY_SCRIPT_NAME


def install_nightly_script() -> Path:
    path = nightly_script_path()
    scripts_path = path.parent
    hermes_home = get_hermes_home().expanduser().resolve()
    if scripts_path.exists() and scripts_path.is_symlink():
        raise ValueError(f"Refusing to write through symlinked scripts directory: {scripts_path}")
    scripts_path.mkdir(parents=True, exist_ok=True)
    if scripts_path.is_symlink() or not scripts_path.is_dir():
        raise ValueError(f"Refusing to write through unsafe scripts directory: {scripts_path}")
    scripts_dir = scripts_path.resolve()
    if hermes_home not in {scripts_dir, *scripts_dir.parents}:
        raise ValueError(f"Refusing to write nightly script outside Hermes home: {scripts_path}")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing to write unsafe nightly script path: {path}")
    target = path.resolve() if path.exists() else scripts_dir / path.name
    if scripts_dir not in {target.parent, *target.parents}:
        raise ValueError(f"Refusing to write nightly script outside scripts directory: {path}")
    content = """from agent.post_chat_reflection import run_nightly_synthesis\n\nreport = run_nightly_synthesis()\nmetrics = report.get('metrics', {})\nactionable = int(metrics.get('promote_allowed', 0) or 0) + int(metrics.get('review_required', 0) or 0)\nif actionable:\n    print(f\"Post-chat reflection: {actionable} actionable cluster(s). JSON: {report.get('json_report_path')} HTML: {report.get('html_report_path')}\")\n"""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


def _is_expected_nightly_job(job: Dict[str, Any], *, schedule: str = "0 3 * * *", deliver: str = "local") -> bool:
    return (
        isinstance(job, dict)
        and job.get("name") == _NIGHTLY_JOB_NAME
        and job.get("no_agent") is True
        and str(job.get("script") or "") == _NIGHTLY_SCRIPT_NAME
        and not str(job.get("prompt") or "").strip()
        and job.get("profile") in {None, ""}
        and job.get("enabled") is True
        and str(job.get("state") or "scheduled") == "scheduled"
        and str(job.get("deliver") or "") == str(deliver)
        and str(job.get("schedule_display") or "") == str(schedule)
    )


def install_nightly_cron(*, schedule: str = "0 3 * * *", deliver: str = "local") -> Dict[str, Any]:
    """Install a no-agent nightly synthesis cron job.

    The job only writes JSON/HTML reports and prints output when actionable
    clusters exist. Promotion remains manual via the review packet command.
    """
    from cron.jobs import create_job, load_jobs

    matches = [job for job in load_jobs() if isinstance(job, dict) and job.get("name") == _NIGHTLY_JOB_NAME]
    unsafe = [job for job in matches if not _is_expected_nightly_job(job, schedule=schedule, deliver=deliver)]
    if unsafe:
        raise ValueError(
            "Refusing to treat existing same-name cron job as installed: "
            "expected no_agent=True, empty prompt, enabled job, matching schedule/deliver, no profile override, and script "
            f"{_NIGHTLY_SCRIPT_NAME!r}. Inspect/remove the conflicting job manually."
        )
    if len(matches) > 1:
        raise ValueError("Refusing ambiguous duplicate post-chat reflection nightly cron jobs; inspect cron jobs manually.")
    script = install_nightly_script()
    if matches:
        return {"state": "exists", "job": matches[0], "script_path": str(script)}
    job = create_job(
        prompt=None,
        schedule=schedule,
        name=_NIGHTLY_JOB_NAME,
        deliver=deliver,
        script=script.name,
        no_agent=True,
    )
    return {"state": "created", "job": job, "script_path": str(script)}


def record_post_chat_reflection(messages: List[Dict[str, Any]], *, session_id: str = "", config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract and persist post-chat reflection candidates.

    This is intentionally deterministic and suggest-only. It never calls a
    model and never promotes candidates to memory, skills, or taskpool.
    """
    cfg = post_chat_reflection_config() if config is None else dict(config)
    if not _truthy(cfg.get("enabled"), default=True):
        return {"state": "disabled", "candidates_written": 0}
    ledger_path = cfg.get("ledger_path")
    status_path = _expand_path(cfg.get("status_path") or default_reflection_status_path())
    candidates = build_post_chat_candidates(messages, session_id=session_id)
    if not candidates:
        return {"state": "no_candidates", "session_id": session_id, "candidates_written": 0, "suggest_only": True}
    ledger = PostChatCandidateLedger(ledger_path)
    written = ledger.append_many(candidates)
    status = {
        "state": "completed",
        "session_id": session_id,
        "generated_at": _utc_now_iso(),
        "candidate_ledger_path": str(ledger.path),
        "candidates_written": written,
        "candidate_ids": [c.get("id") for c in candidates],
        "rejected": sum(1 for c in candidates if c.get("status") == "rejected"),
        "suggest_only": True,
    }
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status["status_path"] = str(status_path)
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        status["status_write_error"] = str(exc)
    return status


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes post-chat reflection candidate utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    synth = sub.add_parser("synthesize", help="Build nightly reflection report from candidate ledger")
    synth.add_argument("--ledger", help="Candidate JSONL path")
    synth.add_argument("--json", dest="json_path", help="Output JSON report path")
    synth.add_argument("--html", dest="html_path", help="Output HTML report path")
    review = sub.add_parser("review", help="Build manual promotion review packet")
    review.add_argument("--ledger", help="Candidate JSONL path")
    review.add_argument("--json", dest="json_path", help="Output promotion review JSON path")
    review.add_argument("--markdown", dest="markdown_path", help="Output promotion review Markdown path")
    install = sub.add_parser("install-nightly", help="Install nightly no-agent synthesis cron")
    install.add_argument("--schedule", default="0 3 * * *", help="Cron expression or schedule string")
    install.add_argument("--deliver", default="local", help="Cron delivery target")
    args = parser.parse_args(argv)
    if args.command == "synthesize":
        report = run_nightly_synthesis(ledger_path=args.ledger, json_path=args.json_path, html_path=args.html_path)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "review":
        packet = run_promotion_review(ledger_path=args.ledger, json_path=args.json_path, markdown_path=args.markdown_path)
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "install-nightly":
        result = install_nightly_cron(schedule=args.schedule, deliver=args.deliver)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
