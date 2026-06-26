"""Agent OS Control Plane readback and shadow-routing helpers.

This module intentionally exposes only read-only/advisory runtime surfaces. It
reads the project SSOT/projection artifacts under HERMES_HOME and can compute a
shadow route for an intent, but it never invokes tools, mutates routing, creates
cron jobs, changes task/mission lifecycle, or sends external messages.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ID = "hermes-agent-os-control-plane"

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "communication": ["telegram", "send", "message", "通知", "消息", "提醒", "外发"],
    "devops": ["server", "deploy", "cron", "gateway", "doctor", "monitor", "github", "pr", "release", "merge", "进程", "端口", "合并", "发布", "部署"],
    "research": ["research", "分析", "调研", "evidence", "source", "citation", "搜索"],
    "productivity": ["calendar", "notes", "file", "document", "summary", "文档", "整理"],
    "hermes_self_architecture": [
        "hermes",
        "capability",
        "agent",
        "taskpool",
        "mission",
        "control plane",
        "证据",
        "权限",
        "workflow",
    ],
}

SIDE_EFFECT_WORDS: dict[str, list[str]] = {
    "S7": ["pay", "payment", "purchase", "book", "支付", "下单", "预约", "物理"],
    "S6": ["delete", "destroy", "wipe", "删除", "清空", "销毁"],
    "S5": ["publish", "deploy", "push", "release", "发布", "部署", "推送", "合并"],
    "S4": ["send", "message", "email", "telegram", "cron", "外发", "发", "发消息", "发邮件", "创建pr", "create pr"],
    "S3": ["fetch", "web", "api", "search", "读取外部", "网页"],
    "S2": ["write", "file", "commit", "local", "create", "写", "创建", "生成文件", "本地"],
    "S1": ["read", "inspect", "list", "查看", "读取", "检查"],
}

HIGH_SIDE_EFFECTS = {"S4", "S5", "S6", "S7"}
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class AgentOsPaths:
    home: Path
    project_root: Path
    profile: Path
    projection: Path
    doctor_report: Path
    shadow_report: Path
    shadow_replay: Path


def resolve_home(home: str | Path | None = None) -> Path:
    if home is not None:
        return Path(home).expanduser()
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def paths(home: str | Path | None = None) -> AgentOsPaths:
    root = resolve_home(home)
    project_root = root / "workflows" / "projects" / PROJECT_ID
    return AgentOsPaths(
        home=root,
        project_root=project_root,
        profile=root / "project-profiles" / f"{PROJECT_ID}.json",
        projection=project_root / "06-projection" / "capability_graph_projection.json",
        doctor_report=project_root / "06-projection" / "doctor_report.json",
        shadow_report=project_root / "07-shadow" / "router-shadow-report.json",
        shadow_replay=project_root / "07-shadow" / "router-shadow-replay.json",
    )


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - doctor reports the failure, not callers
        return {"_load_error": str(exc), "_path": str(path)}


def _agent_os_config_status(home: str | Path | None = None) -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        agent_cfg = cfg.get("agent", {}) if isinstance(cfg, dict) else {}
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
        return {
            "agent_os_advisory_guidance": bool(agent_cfg.get("agent_os_advisory_guidance", False)),
            "agent_os_runtime_routing": bool(agent_cfg.get("agent_os_runtime_routing", False)),
            "real_runtime_routing": bool(agent_cfg.get("agent_os_runtime_routing", False)),
            "tool_invocation_from_agent_os": False,
            "external_dispatch": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {"load_error": str(exc)}


def _project_status(profile: dict[str, Any], projection: dict[str, Any], doctor: dict[str, Any], shadow: dict[str, Any]) -> str:
    components = profile.get("current_components") if isinstance(profile, dict) else None
    if isinstance(components, dict):
        runtime = components.get("runtime_implementation")
        capability = components.get("capability_graph_projection")
        intent = components.get("capability_intent")
        if runtime:
            return f"runtime={runtime}; capability={capability}; intent={intent}"
    return (
        f"projection_nodes={len(projection.get('nodes', [])) if isinstance(projection, dict) else 0}; "
        f"doctor_ok={doctor.get('ok') if isinstance(doctor, dict) else None}; "
        f"shadow_records={shadow.get('records') if isinstance(shadow, dict) else None}"
    )


def status(home: str | Path | None = None) -> dict[str, Any]:
    p = paths(home)
    profile = _read_json(p.profile, {})
    projection = _read_json(p.projection, {})
    doctor = _read_json(p.doctor_report, {})
    shadow = _read_json(p.shadow_report, {})
    missing = [
        str(path)
        for path in [p.profile, p.projection, p.doctor_report, p.shadow_report]
        if not path.exists()
    ]
    runtime_mutation = bool(projection.get("runtime_mutation")) if isinstance(projection, dict) else False
    config_status = _agent_os_config_status(home)
    payload = {
        "ok": not missing and not runtime_mutation,
        "project": PROJECT_ID,
        "mode": "advisory_readback_only",
        "home": str(p.home),
        "project_root": str(p.project_root),
        "missing": missing,
        "profile_status": profile.get("status") if isinstance(profile, dict) else None,
        "project_status_summary": _project_status(profile, projection, doctor, shadow),
        "projection": {
            "path": str(p.projection),
            "node_count": len(projection.get("nodes", [])) if isinstance(projection, dict) else 0,
            "runtime_mutation": runtime_mutation,
        },
        "doctor": {
            "path": str(p.doctor_report),
            "ok": doctor.get("ok") if isinstance(doctor, dict) else None,
            "findings": doctor.get("findings", []) if isinstance(doctor, dict) else [],
        },
        "shadow": {
            "path": str(p.shadow_report),
            "ok": shadow.get("ok") if isinstance(shadow, dict) else None,
            "records": shadow.get("records") if isinstance(shadow, dict) else None,
            "blocked_by_policy": shadow.get("blocked_by_policy") if isinstance(shadow, dict) else None,
            "would_execute_anything": shadow.get("would_execute_anything") if isinstance(shadow, dict) else None,
        },
        "config": config_status,
        "runtime_effect": (
            "local route preflight enabled; Agent OS may inform path selection but cannot execute"
            if config_status.get("agent_os_runtime_routing")
            else "none; command reads artifacts and computes shadow advice only"
        ),
    }
    return payload


def doctor(home: str | Path | None = None) -> dict[str, Any]:
    p = paths(home)
    st = status(home)
    findings: list[dict[str, str]] = []
    for missing in st["missing"]:
        findings.append({"level": "error", "check": "artifact_exists", "message": f"missing {missing}"})

    projection = _read_json(p.projection, {})
    if isinstance(projection, dict) and projection.get("_load_error"):
        findings.append({"level": "error", "check": "projection_json", "message": projection["_load_error"]})
    if isinstance(projection, dict) and projection.get("runtime_mutation") is not False:
        findings.append({
            "level": "error",
            "check": "runtime_mutation_default_off",
            "message": "CapabilityGraph projection must be default-off/read-only before advisory runtime readback",
        })
    if isinstance(projection, dict) and not isinstance(projection.get("nodes", []), list):
        findings.append({"level": "error", "check": "projection_nodes", "message": "projection.nodes must be a list"})

    report = _read_json(p.doctor_report, {})
    if isinstance(report, dict) and report.get("ok") is False:
        findings.append({"level": "error", "check": "projection_doctor", "message": "projection doctor report is not ok"})

    shadow = _read_json(p.shadow_report, {})
    if isinstance(shadow, dict) and shadow.get("would_execute_anything") not in {False, None}:
        findings.append({"level": "error", "check": "shadow_no_execute", "message": "shadow router report indicates execution"})

    payload = {
        "ok": not any(f["level"] == "error" for f in findings),
        "project": PROJECT_ID,
        "mode": "advisory_doctor_only",
        "generated_at": _now_iso(),
        "findings": findings,
        "status": st,
    }
    return payload


def _infer_domain(text: str) -> str:
    low = text.lower()
    scores = {domain: sum(1 for kw in kws if kw.lower() in low) for domain, kws in DOMAIN_KEYWORDS.items()}
    best = max(scores, key=lambda domain: scores[domain])
    return best if scores[best] > 0 else "unknown"


def _infer_side_effect(text: str) -> str:
    low = text.lower()
    for level in ["S7", "S6", "S5", "S4", "S3", "S2", "S1"]:
        if any(kw.lower() in low for kw in SIDE_EFFECT_WORDS[level]):
            return level
    return "S0"


def _risk_from_side(side: str) -> str:
    return {"S0": "low", "S1": "low", "S2": "medium", "S3": "medium", "S4": "high", "S5": "critical", "S6": "critical", "S7": "critical"}.get(side, "medium")


def _node_score(node: dict[str, Any], text: str, domain: str, side: str) -> int:
    hay = " ".join(str(node.get(k, "")) for k in ["id", "kind", "responsibility", "source_ref"]).lower()
    score = 0
    for token in re.findall(r"[a-zA-Z0-9_\-]+|[\u4e00-\u9fff]+", text.lower()):
        if len(token) >= 2 and token in hay:
            score += 2
    if domain == "hermes_self_architecture" and any(x in hay for x in ["hermes", "taskpool", "mission", "capability", "workflow"]):
        score += 3
    if node.get("side_effects") == side:
        score += 1
    return score


def route_shadow(text: str, home: str | Path | None = None) -> dict[str, Any]:
    p = paths(home)
    projection = _read_json(p.projection, {})
    nodes = projection.get("nodes", []) if isinstance(projection, dict) else []
    domain = _infer_domain(text)
    side = _infer_side_effect(text)
    risk = _risk_from_side(side)
    ranked = sorted(nodes, key=lambda n: _node_score(n, text, domain, side), reverse=True)
    suggested = [n for n in ranked if _node_score(n, text, domain, side) > 0][:8]
    policy_findings: list[dict[str, str]] = []
    if side in HIGH_SIDE_EFFECTS:
        policy_findings.append({
            "level": "blocked",
            "gate": "default_off_external_side_effects",
            "message": f"{side} requires AuthorityGrant before execution",
        })
    if domain == "unknown":
        policy_findings.append({"level": "warn", "gate": "intent_ambiguity", "message": "No confident domain match"})
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())[:64].strip("-") or "empty"
    return {
        "ok": True,
        "project": PROJECT_ID,
        "mode": "shadow_route_only",
        "id": f"router-shadow:{slug}",
        "generated_at": _now_iso(),
        "intent": {
            "id": f"intent:shadow:{slug}",
            "source_event_id": "cli-advisory",
            "user_goal": text,
            "domain": domain,
            "requested_outcome": "workflow_execution" if side != "S0" else "answer",
            "risk_class": risk,
            "side_effect_expectation": {"requested": [side], "prohibited": sorted(HIGH_SIDE_EFFECTS - {side})},
            "context_refs": [str(p.projection)],
            "ambiguity": {"status": "requires_human" if domain == "unknown" else "acceptable_default", "unresolved_questions": [] if domain != "unknown" else ["domain"]},
        },
        "suggested_capability_nodes": [
            {"id": n.get("id"), "kind": n.get("kind"), "side_effects": n.get("side_effects"), "source_ref": n.get("source_ref")}
            for n in suggested
        ],
        "policy_findings": policy_findings,
        "would_execute": False,
        "runtime_effect": "none; shadow route was computed but no capability was invoked",
        "evidence_refs": [str(p.projection)],
    }


def _redact_intent_text(text: str, max_chars: int = 240) -> str:
    redacted = SENSITIVE_ASSIGNMENT_RE.sub(lambda m: m.group(1) + "=[REDACTED]", text)
    redacted = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", redacted)
    redacted = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[REDACTED_NUMBER]", redacted)
    redacted = re.sub(r"\s+", " ", redacted).strip()
    return redacted[:max_chars]


def replay_recent_user_intents(home: str | Path | None = None, *, limit: int = 20) -> dict[str, Any]:
    """Shadow-route recent user messages from state.db without executing anything.

    The replay is redacted/truncated and read-only. It is a routing quality probe,
    not a transcript export or a durable-memory source.
    """
    p = paths(home)
    db = p.home / "state.db"
    if not db.exists():
        return {
            "ok": False,
            "project": PROJECT_ID,
            "mode": "historical_session_shadow_replay",
            "records": [],
            "findings": [{"level": "error", "check": "state_db", "message": f"missing {db}"}],
            "would_execute_anything": False,
        }
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT m.id, m.session_id, m.content, m.timestamp, s.source
              FROM messages m
              LEFT JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'user'
               AND COALESCE(m.active, 1) = 1
               AND m.content IS NOT NULL
             ORDER BY m.timestamp DESC
             LIMIT ?
            """,
            (max(1, min(int(limit), 50)),),
        ).fetchall()
        con.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "project": PROJECT_ID,
            "mode": "historical_session_shadow_replay",
            "records": [],
            "findings": [{"level": "error", "check": "state_db_read", "message": str(exc)}],
            "would_execute_anything": False,
        }

    records: list[dict[str, Any]] = []
    for row in rows:
        text = _redact_intent_text(str(row["content"] or ""))
        routed = route_shadow(text, p.home)
        records.append({
            "message_id": row["id"],
            "session_id": row["session_id"],
            "source": row["source"],
            "redacted_intent_preview": text,
            "domain": routed["intent"]["domain"],
            "risk_class": routed["intent"]["risk_class"],
            "requested_side_effects": routed["intent"]["side_effect_expectation"]["requested"],
            "policy_findings": routed["policy_findings"],
            "would_execute": False,
        })
    blocked = sum(1 for record in records for f in record["policy_findings"] if f.get("level") == "blocked")
    return {
        "ok": True,
        "project": PROJECT_ID,
        "mode": "historical_session_shadow_replay",
        "generated_at": _now_iso(),
        "record_count": len(records),
        "blocked_by_policy": blocked,
        "would_execute_anything": False,
        "findings": [],
        "records": records,
        "runtime_effect": "none; state.db was opened read-only and no route was executed",
    }


def generate_replay_quality_report(home: str | Path | None = None, *, limit: int = 20, write: bool = False) -> dict[str, Any]:
    replay = replay_recent_user_intents(home, limit=limit)
    if not replay.get("ok"):
        return replay
    p = paths(home)
    records = replay.get("records", [])
    by_domain: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    by_side: dict[str, int] = {}
    unknown = 0
    blocked = 0
    for record in records:
        domain = str(record.get("domain") or "unknown")
        risk = str(record.get("risk_class") or "unknown")
        sides = record.get("requested_side_effects") or ["S0"]
        by_domain[domain] = by_domain.get(domain, 0) + 1
        by_risk[risk] = by_risk.get(risk, 0) + 1
        if domain == "unknown":
            unknown += 1
        for side in sides:
            by_side[str(side)] = by_side.get(str(side), 0) + 1
        blocked += sum(1 for finding in record.get("policy_findings", []) if finding.get("level") == "blocked")
    findings: list[dict[str, str]] = []
    if unknown:
        findings.append({"level": "info", "check": "unknown_domain", "message": f"{unknown} replay records require better intent classification"})
    if blocked:
        findings.append({"level": "info", "check": "policy_block", "message": f"{blocked} high-side-effect intents were blocked in shadow mode"})
    authority_packet = {
        "id": "authority-packet:agent-os-runtime-routing-v1",
        "status": "HUMAN_AUTHORIZATION_REQUIRED",
        "project": PROJECT_ID,
        "generated_at": _now_iso(),
        "candidate_contract": "agent-os advisory CLI + historical replay",
        "preconditions": {
            "agent_os_doctor_ok": doctor(p.home).get("ok"),
            "replay_ok": replay.get("ok"),
            "would_execute_anything": False,
            "runtime_mutation": False,
        },
        "requested_authority": {
            "runtime_advisory_prompt_injection": True,
            "real_runtime_routing": False,
            "tool_invocation": False,
            "scheduler_or_cron_write": False,
            "taskpool_or_mission_lifecycle_write": False,
            "worker_spawn_or_lease": False,
            "external_dispatch": False,
            "publish_or_deploy": False,
            "destructive_action": False,
        },
        "required_human_fields_before_promotion": [
            "exact prompt/routing injection point",
            "kill switch config key",
            "rollback command",
            "focused tests/evaluator command",
            "post-action readback command",
        ],
        "default_safe_decision": "do_not_connect_to_real_runtime_routing; keep shadow-only",
        "rollback": "git revert scoped commit and disable any future agent_os advisory flag",
        "post_action_verification": [
            "agent-os doctor ok",
            "focused pytest pass",
            "live route still reports would_execute false for S5/S6/S7 unless exact authority exists",
        ],
    }
    report = {
        "ok": True,
        "project": PROJECT_ID,
        "mode": "replay_quality_report",
        "generated_at": _now_iso(),
        "record_count": len(records),
        "blocked_by_policy": blocked,
        "would_execute_anything": False,
        "distributions": {"domain": by_domain, "risk": by_risk, "side_effect": by_side},
        "findings": findings,
        "authority_packet": authority_packet,
        "runtime_effect": "none; report generation is local artifact/readback only",
    }
    if write:
        (p.project_root / "07-shadow").mkdir(parents=True, exist_ok=True)
        (p.project_root / "03-governance").mkdir(parents=True, exist_ok=True)
        report_path = p.project_root / "07-shadow" / "replay-quality-report.json"
        packet_path = p.project_root / "03-governance" / "authority-packet-runtime-routing.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        packet_path.write_text(json.dumps(authority_packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["written"] = {"report": str(report_path), "authority_packet": str(packet_path)}
    return report


def format_summary(payload: dict[str, Any]) -> str:
    lines = [f"Agent OS Control Plane: {'OK' if payload.get('ok') else 'ATTENTION'}"]
    lines.append(f"mode: {payload.get('mode')}")
    if "projection" in payload:
        projection = payload["projection"]
        lines.append(f"nodes: {projection.get('node_count')} runtime_mutation={projection.get('runtime_mutation')}")
    if payload.get("missing"):
        lines.append("missing:")
        lines.extend(f"- {m}" for m in payload["missing"])
    findings = payload.get("findings") or payload.get("doctor", {}).get("findings") or []
    if findings:
        lines.append("findings:")
        lines.extend(f"- {f.get('level', 'info').upper()} {f.get('check') or f.get('gate')}: {f.get('message')}" for f in findings)
    if "intent" in payload:
        intent = payload["intent"]
        lines.append(f"intent: {intent.get('domain')} risk={intent.get('risk_class')} side={intent.get('side_effect_expectation', {}).get('requested')}")
        lines.append(f"would_execute: {payload.get('would_execute')}")
    lines.append(str(payload.get("runtime_effect", "runtime_effect: none")))
    return "\n".join(lines)
