"""Agent OS Control Plane readback and shadow-routing helpers.

This module intentionally exposes only read-only/advisory runtime surfaces. It
reads the project SSOT/projection artifacts under HERMES_HOME and can compute a
shadow route for an intent, but it never invokes tools, mutates routing, creates
cron jobs, changes task/mission lifecycle, or sends external messages.
"""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ID = "hermes-agent-os-control-plane"

CANONICAL_DOMAIN_SECTIONS = [
    "IntentRecord",
    "CapabilityNode",
    "AgentWrapper",
    "AuthorityGrant",
    "ExecutionLease",
    "EvidenceNode",
    "GateDecision",
    "ProjectionRecord",
    "CompensationRecord",
    "ClosureRecord",
]
CANONICAL_SIDE_EFFECT_LEVELS = [f"S{i}" for i in range(8)]
CANONICAL_DEFAULT_OFF_SIDE_EFFECTS = ["S4", "S5", "S6", "S7"]
AUDIT_SURFACE_TO_EVIDENCE_KIND = {
    "canonical_schema": "schema_status",
    "capability_projection": "capability_projection",
    "cron": "cron_source",
    "gateway_outbox": "gateway_outbox",
    "shadow_router": "shadow_router",
    "taskpool_mission_profile": "project_profile",
}

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
    harness_state: Path
    harness_receipts: Path
    harness_authorizations: Path


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
        harness_state=project_root / "08-executor" / "local-harness-state.json",
        harness_receipts=project_root / "08-executor" / "local-harness-receipts.jsonl",
        harness_authorizations=project_root / "08-executor" / "local-harness-authorizations.json",
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
            "agent_os_multimodel_review": bool(agent_cfg.get("agent_os_multimodel_review", False)),
            "agent_os_local_harness_executor": bool(agent_cfg.get("agent_os_local_harness_executor", False)),
            "agent_os_scheduler_cron_write": bool(agent_cfg.get("agent_os_scheduler_cron_write", False)),
            "agent_os_taskpool_lifecycle_write": bool(agent_cfg.get("agent_os_taskpool_lifecycle_write", False)),
            "agent_os_external_dispatch": bool(agent_cfg.get("agent_os_external_dispatch", False)),
            "agent_os_publish_deploy_push": bool(agent_cfg.get("agent_os_publish_deploy_push", False)),
            "agent_os_destructive_operations": bool(agent_cfg.get("agent_os_destructive_operations", False)),
            "agent_os_payment_credential_entry": bool(agent_cfg.get("agent_os_payment_credential_entry", False)),
            "agent_os_broad_fanout": bool(agent_cfg.get("agent_os_broad_fanout", False)),
            "real_runtime_routing": bool(agent_cfg.get("agent_os_runtime_routing", False)),
            "tool_invocation_from_agent_os": bool(agent_cfg.get("agent_os_local_harness_executor", False)),
            "scheduler_cron_write": bool(agent_cfg.get("agent_os_scheduler_cron_write", False)),
            "taskpool_mission_lifecycle_write": bool(agent_cfg.get("agent_os_taskpool_lifecycle_write", False)),
            "external_dispatch": bool(agent_cfg.get("agent_os_external_dispatch", False)),
            "publish_deploy_push": bool(agent_cfg.get("agent_os_publish_deploy_push", False)),
            "destructive_operations": bool(agent_cfg.get("agent_os_destructive_operations", False)),
            "payment_credential_entry": bool(agent_cfg.get("agent_os_payment_credential_entry", False)),
            "broad_fanout": bool(agent_cfg.get("agent_os_broad_fanout", False)),
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
            "broad fan-out enabled; only exact named channel-set/payload fan-out with receipts may be owned"
            if config_status.get("agent_os_broad_fanout")
            else "payment/credential boundary projected enabled; secrets must not be typed and payment flows require exact human gate"
            if config_status.get("agent_os_payment_credential_entry")
            else "destructive operations enabled; only exact target with backup/rollback/readback may be owned"
            if config_status.get("agent_os_destructive_operations")
            else "publish/deploy/push enabled; only exact scoped target with rollback/readback may be owned"
            if config_status.get("agent_os_publish_deploy_push")
            else "external dispatch enabled; only exact allowlisted target/payload dispatches with readback may be owned"
            if config_status.get("agent_os_external_dispatch")
            else "local taskpool lifecycle write enabled; only exact idempotent local Kanban/taskpool operations with readback may be owned"
            if config_status.get("agent_os_taskpool_lifecycle_write")
            else "local scheduler cron write enabled; only no-agent/silent/local SSOT heartbeat jobs may be owned"
            if config_status.get("agent_os_scheduler_cron_write")
            else "local route preflight enabled; Agent OS may inform path selection but cannot execute"
            if config_status.get("agent_os_runtime_routing")
            else "none; command reads artifacts and computes shadow advice only"
        ),
    }
    return payload


def _profile_canonical_path(profile: dict[str, Any], key: str, *, home: Path, fallback: str) -> Path:
    docs = profile.get("canonical_docs") if isinstance(profile, dict) else None
    if isinstance(docs, dict) and isinstance(docs.get(key), str):
        return Path(docs[key]).expanduser()
    return home / fallback


def _read_text_or_error(path: Path) -> tuple[str, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except Exception as exc:  # noqa: BLE001 - schema status reports the error
        return "", str(exc)


def canonical_schema_status(home: str | Path | None = None) -> dict[str, Any]:
    """Validate the canonical ARG schema projection without mutating state.

    This is the P1 read-only bridge from the documentation takeover into an
    executable schema gate. It checks that the ARG domain model sections exist,
    that the Agent OS schema source still covers S0-S7, and that high side
    effects remain default-off. It never writes reports or changes runtime
    routing/lifecycle state.
    """
    p = paths(home)
    profile = _read_json(p.profile, {})
    projection = _read_json(p.projection, {})
    domain_model_path = _profile_canonical_path(
        profile,
        "autonomous_runtime_governance_domain_model",
        home=p.home,
        fallback="hermes-agent/docs/private/autonomous-runtime-governance/04-core-domain-model.md",
    )
    authority_path = _profile_canonical_path(
        profile,
        "autonomous_runtime_governance_authority",
        home=p.home,
        fallback="hermes-agent/docs/private/autonomous-runtime-governance/07-authority-and-side-effect-governance.md",
    )
    schema_path = _profile_canonical_path(
        profile,
        "schema",
        home=p.home,
        fallback=f"workflows/projects/{PROJECT_ID}/01-architecture/agent-os-schemas.json",
    )

    domain_text, domain_error = _read_text_or_error(domain_model_path)
    authority_text, authority_error = _read_text_or_error(authority_path)
    schema = _read_json(schema_path, {})
    findings: list[dict[str, Any]] = []

    missing_sections = [section for section in CANONICAL_DOMAIN_SECTIONS if f"## {section}" not in domain_text]
    if domain_error or missing_sections:
        findings.append({
            "level": "error",
            "check": "canonical_domain_model_sections",
            "message": domain_error or "missing canonical domain model sections",
            "missing": missing_sections,
        })

    authority_missing_levels = [level for level in CANONICAL_SIDE_EFFECT_LEVELS if level not in authority_text]
    if authority_error or authority_missing_levels:
        findings.append({
            "level": "error",
            "check": "canonical_authority_side_effect_levels",
            "message": authority_error or "authority doc does not mention all side-effect levels",
            "missing": authority_missing_levels,
        })

    side_effect_levels = schema.get("side_effect_levels", {}) if isinstance(schema, dict) else {}
    missing_side_effect_levels = [level for level in CANONICAL_SIDE_EFFECT_LEVELS if level not in side_effect_levels]
    default_off_side_effects = schema.get("default_off_side_effects", []) if isinstance(schema, dict) else []
    if missing_side_effect_levels:
        findings.append({
            "level": "error",
            "check": "agent_os_schema_side_effect_levels",
            "message": "Agent OS schema source is missing canonical S0-S7 levels",
            "missing": missing_side_effect_levels,
        })
    if default_off_side_effects != CANONICAL_DEFAULT_OFF_SIDE_EFFECTS:
        findings.append({
            "level": "error",
            "check": "agent_os_schema_default_off_levels",
            "message": "S4-S7 must stay default-off in the schema source",
            "expected": CANONICAL_DEFAULT_OFF_SIDE_EFFECTS,
            "actual": default_off_side_effects,
        })

    runtime_mutation = bool(projection.get("runtime_mutation")) if isinstance(projection, dict) else False
    if runtime_mutation:
        findings.append({
            "level": "error",
            "check": "schema_projection_runtime_mutation_default_off",
            "message": "canonical schema status must remain read-only",
        })

    return {
        "ok": not any(f["level"] == "error" for f in findings),
        "project": PROJECT_ID,
        "mode": "canonical_schema_read_only",
        "generated_at": _now_iso(),
        "runtime_mutation": runtime_mutation,
        "domain_model": {
            "path": str(domain_model_path),
            "required_sections": CANONICAL_DOMAIN_SECTIONS,
            "missing_sections": missing_sections,
        },
        "authority": {
            "path": str(authority_path),
            "required_side_effect_levels": CANONICAL_SIDE_EFFECT_LEVELS,
            "missing_side_effect_levels": authority_missing_levels,
        },
        "agent_os_schema": {
            "path": str(schema_path),
            "missing_side_effect_levels": missing_side_effect_levels,
            "default_off_side_effects": default_off_side_effects,
        },
        "findings": findings,
        "scope": "read-only P1 canonical schema/projection status; no domain store mutation",
    }


def _append_audit(findings: list[dict[str, Any]], *, surface: str, status: str, divergence_class: str, evidence_ref: str, next_safe_action: str, detail: Any = None) -> None:
    item: dict[str, Any] = {
        "surface": surface,
        "status": status,
        "divergence_class": divergence_class,
        "evidence_ref": evidence_ref,
        "next_safe_action": next_safe_action,
    }
    if detail is not None:
        item["detail"] = detail
    findings.append(item)


def _jsonl_status(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.exists():
        return "warn", {"reason": "missing", "records": 0}
    records = 0
    statuses: dict[str, int] = {}
    try:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            obj = json.loads(line)
            records += 1
            status_value = str(obj.get("status", "unknown")) if isinstance(obj, dict) else "non_object"
            statuses[status_value] = statuses.get(status_value, 0) + 1
    except Exception as exc:  # noqa: BLE001 - audit reports fail-closed parse errors
        return "fail", {"reason": "parse_error", "error": str(exc), "records_before_error": records, "path": str(path)}
    return "pass", {"records": records, "status_counts": statuses, "path": str(path)}


def read_only_runtime_audit(home: str | Path | None = None) -> dict[str, Any]:
    """Classify runtime divergence sources without mutating any domain store."""
    p = paths(home)
    findings: list[dict[str, Any]] = []

    schema_payload = canonical_schema_status(home)
    _append_audit(
        findings,
        surface="canonical_schema",
        status="pass" if schema_payload.get("ok") else "fail",
        divergence_class="schema_ready" if schema_payload.get("ok") else "schema_blocked",
        evidence_ref=str(p.profile),
        next_safe_action="continue_to_evidence_projection" if schema_payload.get("ok") else "repair_schema_findings_before_runtime_migration",
        detail={"findings": schema_payload.get("findings", [])},
    )

    projection = _read_json(p.projection, {})
    projection_status = "pass" if isinstance(projection, dict) and projection.get("runtime_mutation") is False else "fail"
    _append_audit(
        findings,
        surface="capability_projection",
        status=projection_status,
        divergence_class="projection_read_only" if projection_status == "pass" else "projection_mutation_or_unreadable",
        evidence_ref=str(p.projection),
        next_safe_action="use_as_mapping_source" if projection_status == "pass" else "restore_runtime_mutation_false",
        detail={"node_count": len(projection.get("nodes", [])) if isinstance(projection, dict) else 0},
    )

    cron_path = p.home / "cron" / "jobs.json"
    cron_payload = _read_json(cron_path, None)
    if cron_payload is None:
        _append_audit(findings, surface="cron", status="warn", divergence_class="source_missing", evidence_ref=str(cron_path), next_safe_action="skip_cron_mapping_until_source_exists")
    elif isinstance(cron_payload, dict) and cron_payload.get("_load_error"):
        _append_audit(findings, surface="cron", status="fail", divergence_class="source_unreadable", evidence_ref=str(cron_path), next_safe_action="repair_cron_json_before_audit", detail=cron_payload)
    else:
        jobs = cron_payload.get("jobs", cron_payload) if isinstance(cron_payload, dict) else cron_payload
        _append_audit(findings, surface="cron", status="pass", divergence_class="source_observed", evidence_ref=str(cron_path), next_safe_action="map_cron_jobs_to_intent_records", detail={"job_count": len(jobs) if isinstance(jobs, list) else 0})

    outbox_path = p.home / "state" / "gateway_delivery_outbox.jsonl"
    outbox_status, outbox_detail = _jsonl_status(outbox_path)
    _append_audit(
        findings,
        surface="gateway_outbox",
        status=outbox_status,
        divergence_class="source_observed" if outbox_status == "pass" else "source_missing" if outbox_status == "warn" else "source_unreadable",
        evidence_ref=str(outbox_path),
        next_safe_action="map_outbox_receipts_to_evidence_nodes" if outbox_status == "pass" else "repair_or_wait_for_gateway_outbox_source",
        detail=outbox_detail,
    )

    shadow = _read_json(p.shadow_report, {})
    shadow_status = "pass" if isinstance(shadow, dict) and shadow.get("would_execute_anything") in {False, None} else "fail"
    _append_audit(
        findings,
        surface="shadow_router",
        status=shadow_status,
        divergence_class="shadow_no_execute" if shadow_status == "pass" else "shadow_execution_leak",
        evidence_ref=str(p.shadow_report),
        next_safe_action="continue_shadow_replay" if shadow_status == "pass" else "disable_shadow_execution_before_migration",
        detail={"would_execute_anything": shadow.get("would_execute_anything") if isinstance(shadow, dict) else None},
    )

    profile_exists = p.profile.exists()
    _append_audit(
        findings,
        surface="taskpool_mission_profile",
        status="pass" if profile_exists else "warn",
        divergence_class="source_observed" if profile_exists else "source_missing",
        evidence_ref=str(p.profile),
        next_safe_action="use_profile_as_scoped_local_brain" if profile_exists else "create_project_profile_before_runtime_migration",
    )

    summary = {status_name: sum(1 for f in findings if f["status"] == status_name) for status_name in ["pass", "warn", "fail"]}
    return {
        "ok": summary["fail"] == 0,
        "project": PROJECT_ID,
        "mode": "runtime_audit_read_only",
        "generated_at": _now_iso(),
        "runtime_mutation": False,
        "summary": summary,
        "findings": findings,
        "scope": "read-only P2 runtime audit over local sources; no compensation or domain-store mutation",
    }


def _stable_evidence_id(surface: str, evidence_ref: str) -> str:
    digest = hashlib.sha256(f"{surface}\n{evidence_ref}".encode("utf-8")).hexdigest()[:12]
    return f"ev_{surface}_{digest}"


def _audit_finding_to_evidence_node(finding: dict[str, Any]) -> dict[str, Any]:
    surface = str(finding.get("surface", "unknown"))
    status_value = str(finding.get("status", "fail"))
    evidence_ref = str(finding.get("evidence_ref", ""))
    return {
        "id": _stable_evidence_id(surface, evidence_ref),
        "kind": AUDIT_SURFACE_TO_EVIDENCE_KIND.get(surface, surface),
        "claim_ids": [f"erg-runtime.p2.audit.{surface}"],
        "trust_level": "local-readback" if status_value == "pass" else "local-warning" if status_value == "warn" else "blocked",
        "freshness": "current-run",
        "verification_status": status_value,
        "source_ref": evidence_ref,
        "surface": surface,
        "divergence_class": finding.get("divergence_class"),
        "next_safe_action": finding.get("next_safe_action"),
        "detail": finding.get("detail", {}),
    }


def evidence_projection_status(home: str | Path | None = None) -> dict[str, Any]:
    """Project read-only audit findings into canonical EvidenceNode-shaped records."""
    audit = read_only_runtime_audit(home)
    nodes = [_audit_finding_to_evidence_node(finding) for finding in audit.get("findings", [])]
    summary = {
        "node_count": len(nodes),
        "pass": sum(1 for node in nodes if node["verification_status"] == "pass"),
        "warn": sum(1 for node in nodes if node["verification_status"] == "warn"),
        "fail": sum(1 for node in nodes if node["verification_status"] == "fail"),
    }
    return {
        "ok": summary["fail"] == 0 and bool(audit.get("ok")),
        "project": PROJECT_ID,
        "mode": "evidence_projection_read_only",
        "generated_at": _now_iso(),
        "runtime_mutation": False,
        "domain_store_mutation": False,
        "summary": summary,
        "evidence_nodes": nodes,
        "source_audit": {
            "mode": audit.get("mode"),
            "summary": audit.get("summary"),
            "ok": audit.get("ok"),
        },
        "scope": "read-only P3 EvidenceNode projection; no ledger write, compensation, or adapter execution",
    }


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(payload))


def _expected_p4_targets(p: AgentOsPaths) -> dict[str, Path]:
    runtime_dir = p.project_root / "08-runtime"
    return {
        "state": runtime_dir / "erg_runtime_state.json",
        "receipts": runtime_dir / "erg_runtime_receipts.jsonl",
        "plan": runtime_dir / "reconciliation_plan.json",
    }


def _default_p4_gate_path(p: AgentOsPaths) -> Path:
    return p.home / "hermes-agent" / "docs" / "private" / "autonomous-runtime-governance" / "19-runtime-promotion-authorization-gate.json"


def _validate_p4_gate(gate: dict[str, Any], expected: dict[str, Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if gate.get("id") != "authority-packet:erg-runtime-p4-runtime-promotion-v1":
        findings.append({"level": "error", "check": "p4_gate_id", "message": "unexpected P4 authority packet id"})
    if gate.get("status") != "HUMAN_AUTHORIZATION_REQUIRED":
        findings.append({"level": "error", "check": "p4_gate_status", "message": "P4 authority packet must be HUMAN_AUTHORIZATION_REQUIRED before explicit user acceptance"})
    allowed = gate.get("requested_authority_for_next_slice", {}).get("allowed_targets", []) if isinstance(gate, dict) else []
    expected_set = {str(path) for path in expected.values()}
    if set(allowed) != expected_set:
        findings.append({
            "level": "error",
            "check": "p4_allowed_targets_exact",
            "message": "P4 allowed targets drifted from exact runtime state/receipt/plan files",
            "expected": sorted(expected_set),
            "actual": sorted(str(item) for item in allowed),
        })
    return findings


def bootstrap_erg_runtime_state(home: str | Path | None = None, *, authority_gate: str | Path | None = None) -> dict[str, Any]:
    """Execute the exact P4 local runtime bootstrap boundary.

    Writes only the allowed local runtime state, receipt, and execute=false plan
    named by the P4 authority packet. Does not mutate scheduler/taskpool/gateway,
    does not execute compensation, and does not call external adapters.
    """
    p = paths(home)
    expected = _expected_p4_targets(p)
    gate_path = Path(authority_gate).expanduser() if authority_gate else _default_p4_gate_path(p)
    gate = _read_json(gate_path, {})
    findings = _validate_p4_gate(gate if isinstance(gate, dict) else {}, expected)
    evidence = evidence_projection_status(home)
    if not evidence.get("ok"):
        findings.append({"level": "error", "check": "p3_evidence_projection_ok", "message": "P3 evidence projection must be ok before P4 bootstrap"})
    if findings:
        return {
            "ok": False,
            "project": PROJECT_ID,
            "mode": "erg_runtime_p4_bootstrap",
            "generated_at": _now_iso(),
            "findings": findings,
            "runtime_mutation": False,
            "external_side_effects_executed": False,
            "compensation_executed": False,
        }

    before_hashes = {name: _file_sha256(path) for name, path in expected.items()}
    stable_evidence = dict(evidence)
    stable_evidence.pop("generated_at", None)
    source_report_hash = hashlib.sha256(_canonical_json_bytes(stable_evidence)).hexdigest()
    request_digest = hashlib.sha256(
        _canonical_json_bytes({
            "gate_id": gate.get("id"),
            "allowed_targets": sorted(str(path) for path in expected.values()),
            "source_report_hash": source_report_hash,
            "idempotency_key": "erg-runtime-p4-bootstrap-v1",
        })
    ).hexdigest()
    state_payload = {
        "schema_version": "erg-runtime-state/v1",
        "project": PROJECT_ID,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "authority_gate": str(gate_path),
        "request_digest": request_digest,
        "source_report_hash": source_report_hash,
        "evidence_node_count": evidence["summary"]["node_count"],
        "evidence_summary": evidence["summary"],
        "evidence_nodes": evidence["evidence_nodes"],
        "execute": False,
        "runtime_mutation_scope": "local_state_bootstrap_only",
        "external_side_effects_executed": False,
        "compensation_executed": False,
    }
    plan_actions = []
    for node in evidence["evidence_nodes"]:
        plan_actions.append({
            "id": f"plan_{node['id']}",
            "source_evidence_node": node["id"],
            "surface": node["surface"],
            "kind": node["kind"],
            "proposed_action": node.get("next_safe_action"),
            "execute": False,
            "adapter_enabled": False,
            "compensation_enabled": False,
            "requires_future_authority": True,
        })
    plan_payload = {
        "schema_version": "erg-reconciliation-plan/v1",
        "project": PROJECT_ID,
        "created_at": _now_iso(),
        "authority_gate": str(gate_path),
        "request_digest": request_digest,
        "source_report_hash": source_report_hash,
        "execute": False,
        "adapter_targets_enabled": [],
        "actions": plan_actions,
    }

    expected["state"].parent.mkdir(parents=True, exist_ok=True)
    receipts_path = expected["receipts"]
    existing_receipts = receipts_path.read_text(encoding="utf-8").splitlines() if receipts_path.exists() else []
    parsed_receipts: list[dict[str, Any]] = []
    for line in existing_receipts:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            parsed_receipts.append(parsed)
    idempotent_replay = any(
        record.get("request_digest") == request_digest or record.get("idempotency_key") == "erg-runtime-p4-bootstrap-v1"
        for record in parsed_receipts
    )
    if not idempotent_replay:
        _write_json_file(expected["state"], state_payload)
        _write_json_file(expected["plan"], plan_payload)
        after_state_hash = _file_sha256(expected["state"])
        after_plan_hash = _file_sha256(expected["plan"])
        receipt = {
            "schema_version": "erg-runtime-receipt/v1",
            "idempotency_key": "erg-runtime-p4-bootstrap-v1",
            "request_digest": request_digest,
            "created_at": _now_iso(),
            "authority_gate": str(gate_path),
            "runtime_boundary": "p4_local_domain_store_bootstrap",
            "before_hashes": before_hashes,
            "after_hashes": {"state": after_state_hash, "plan": after_plan_hash, "receipts_before": before_hashes["receipts"]},
            "external_side_effects_executed": False,
            "compensation_executed": False,
            "readback": {"evidence_node_count": evidence["summary"]["node_count"], "plan_action_count": len(plan_actions), "execute": False},
        }
        receipts_path.parent.mkdir(parents=True, exist_ok=True)
        with receipts_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    after_hashes = {name: _file_sha256(path) for name, path in expected.items()}
    return {
        "ok": True,
        "project": PROJECT_ID,
        "mode": "erg_runtime_p4_bootstrap",
        "generated_at": _now_iso(),
        "runtime_mutation": True,
        "domain_store_mutation": True,
        "external_side_effects_executed": False,
        "compensation_executed": False,
        "idempotent_replay": idempotent_replay,
        "request_digest": request_digest,
        "source_report_hash": source_report_hash,
        "written_targets": {name: str(path) for name, path in expected.items()},
        "before_hashes": before_hashes,
        "after_hashes": after_hashes,
        "readback": {
            "evidence_node_count": evidence["summary"]["node_count"],
            "plan_action_count": len(plan_actions),
            "execute": False,
            "receipt_count": len(receipts_path.read_text(encoding="utf-8").splitlines()) if receipts_path.exists() else 0,
        },
        "not_executed": [
            "scheduler_or_cron_mutation",
            "taskpool_or_mission_lifecycle_write",
            "gateway_outbox_mutation_or_external_send",
            "worker_spawn_or_lease",
            "compensation_execution",
            "publish_deploy_push",
            "destructive_action_outside_exact_targets",
            "payment_or_credential_entry",
            "broad_fanout",
        ],
    }


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


def _path_within(path: Path, root: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    return resolved_root == resolved_path or resolved_root in resolved_path.parents


def _safe_executor_path(p: AgentOsPaths, requested: str | Path | None, *, default: Path, suffix: str) -> Path:
    """Return a direct-child executor file path under 08-executor.

    For the first executor rung we deliberately disallow subdirectories. This
    removes parent-directory symlink ambiguity and keeps O_NOFOLLOW protection on
    the only mutable path component: the final file.
    """
    allowed_root = p.project_root / "08-executor"
    if allowed_root.exists() and allowed_root.is_symlink():
        raise ValueError("Agent OS 08-executor must not be a symlink")
    if requested is None:
        target = default
    else:
        raw = Path(requested).expanduser()
        if any(part in {".", ".."} for part in raw.parts):
            raise ValueError("executor path must not contain dot or parent traversal components")
        if raw.is_absolute():
            target = raw
            if target.parent != allowed_root:
                raise ValueError("absolute executor path must be a direct child of Agent OS 08-executor")
        else:
            if len(raw.parts) != 1:
                raise ValueError("relative executor path must be a single filename")
            target = allowed_root / raw.name
    if target.suffix != suffix:
        raise ValueError(f"executor path must be a {suffix} file")
    if target.name in {"", ".", ".."}:
        raise ValueError("executor path must name a file")
    # Direct-child only: no caller-controlled parent directory components.
    if target.parent.resolve(strict=False) != allowed_root.resolve(strict=False):
        raise ValueError("executor path must be a direct child of Agent OS 08-executor")
    if not _path_within(target, allowed_root):
        raise ValueError("executor path must stay under Agent OS 08-executor")
    if target.exists() and not target.is_file():
        raise ValueError("executor path must be a regular file")
    if target.is_symlink():
        raise ValueError("executor path must not be a symlink")
    return target

def _safe_harness_state_path(p: AgentOsPaths, requested: str | Path | None) -> Path:
    return _safe_executor_path(p, requested, default=p.harness_state, suffix=".json")


def _safe_receipts_path(p: AgentOsPaths) -> Path:
    return _safe_executor_path(p, None, default=p.harness_receipts, suffix=".jsonl")


def _safe_authorizations_path(p: AgentOsPaths) -> Path:
    return _safe_executor_path(p, None, default=p.harness_authorizations, suffix=".json")


def _open_executor_dir(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path.parent, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EPERM}:
            raise ValueError("executor parent must be a real directory, not a symlink") from None
        raise


def _safe_read_text(path: Path, default: str | None = None) -> str | None:
    if not path.exists():
        return default
    dir_fd = _open_executor_dir(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path.name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return default
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EPERM}:
            raise ValueError("executor read path must not be a symlink") from None
        raise
    finally:
        os.close(dir_fd)
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        return fh.read()


def _safe_read_json(path: Path, default: Any) -> Any:
    try:
        text = _safe_read_text(path, None)
        if text is None:
            return default
        return json.loads(text)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"_load_error": str(exc), "_path": str(path)}


def _safe_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dir_fd = _open_executor_dir(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path.name, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EPERM}:
            raise ValueError("executor path must not be a symlink") from None
        raise
    finally:
        os.close(dir_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dir_fd = _open_executor_dir(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path.name, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EPERM}:
            raise ValueError("receipt path must not be a symlink") from None
        raise
    finally:
        os.close(dir_fd)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = _safe_read_text(path, None)
    if text is None:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _evidence_key(evidence_refs: list[str]) -> list[str]:
    return sorted(x.strip() for x in evidence_refs if isinstance(x, str) and x.strip())


def _authorization_matches(record: dict[str, Any], *, authorization_id: str, intent_id: str, target_ref: str, target_status: str, evidence_refs: list[str], adapter_state_path: Path) -> bool:
    return (
        record.get("authorization_id") == authorization_id
        and record.get("intent_id") == intent_id
        and record.get("adapter") == "local-file-harness"
        and record.get("target_ref") == target_ref
        and record.get("target_status") == target_status
        and _evidence_key(record.get("evidence_refs", [])) == _evidence_key(evidence_refs)
        and record.get("adapter_state_path") == str(adapter_state_path)
        and record.get("execution_authorized") is True
        and record.get("external_side_effects_authorized") is False
    )


def authorize_local_harness(
    *,
    authorization_id: str,
    intent_id: str,
    target_ref: str,
    target_status: str,
    evidence_refs: list[str],
    adapter_state_path: str | Path | None = None,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Record a non-executing local harness authorization tuple."""
    p = paths(home)
    cfg = _agent_os_config_status(home)
    base = {"project": PROJECT_ID, "mode": "local_file_harness_authorization", "adapter": "local-file-harness", "executed": False, "external_side_effects_executed": False}
    if not cfg.get("agent_os_local_harness_executor"):
        return {**base, "ok": False, "blocked_reason": "agent_os_local_harness_executor_disabled"}
    if not authorization_id.startswith("auth:") or not intent_id.startswith("intent:") or not target_ref.startswith("task:"):
        return {**base, "ok": False, "blocked_reason": "invalid_authorization_tuple"}
    if target_status not in {"pending", "review", "done", "blocked"}:
        return {**base, "ok": False, "blocked_reason": "invalid_target_status"}
    if not evidence_refs or not _evidence_key(evidence_refs):
        return {**base, "ok": False, "blocked_reason": "missing_evidence_refs"}
    try:
        state_path = _safe_harness_state_path(p, adapter_state_path)
        auth_path = _safe_authorizations_path(p)
    except ValueError as exc:
        return {**base, "ok": False, "blocked_reason": "invalid_executor_path", "error": str(exc)}
    store = _safe_read_json(auth_path, {"authorizations": []})
    if not isinstance(store, dict) or not isinstance(store.get("authorizations"), list):
        store = {"authorizations": []}
    record = {
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "adapter": "local-file-harness",
        "target_ref": target_ref,
        "target_status": target_status,
        "evidence_refs": _evidence_key(evidence_refs),
        "adapter_state_path": str(state_path),
        "execution_authorized": True,
        "external_side_effects_authorized": False,
        "real_taskpool_write_authorized": False,
        "created_at": _now_iso(),
    }
    for existing in store["authorizations"]:
        if _authorization_matches(existing, authorization_id=authorization_id, intent_id=intent_id, target_ref=target_ref, target_status=target_status, evidence_refs=evidence_refs, adapter_state_path=state_path):
            return {**base, "ok": True, "authorization_id": authorization_id, "idempotent": True, "authorization_path": str(auth_path), "adapter_state_path": str(state_path)}
    store["authorizations"].append(record)
    try:
        _safe_write_json(auth_path, store)
    except ValueError as exc:
        return {**base, "ok": False, "blocked_reason": "invalid_authorization_path", "error": str(exc)}
    return {**base, "ok": True, "authorization_id": authorization_id, "idempotent": False, "authorization_path": str(auth_path), "adapter_state_path": str(state_path)}

def execute_local_harness(
    *,
    authorization_id: str,
    intent_id: str,
    target_ref: str,
    target_status: str,
    evidence_refs: list[str],
    adapter_state_path: str | Path | None = None,
    expected_previous_status: str | None = None,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Execute the first Agent OS executor rung against a local JSON harness only.

    This mutates only a local harness state file under the Agent OS SSOT. It is
    deliberately not taskpool/Kanban/Lark/Telegram write-through.
    """
    p = paths(home)
    cfg = _agent_os_config_status(home)
    base = {
        "project": PROJECT_ID,
        "mode": "local_file_harness_executor",
        "adapter": "local-file-harness",
        "external_side_effects_executed": False,
        "real_taskpool_write_executed": False,
    }
    if not cfg.get("agent_os_local_harness_executor"):
        return {**base, "ok": False, "executed": False, "blocked_reason": "agent_os_local_harness_executor_disabled"}
    if not authorization_id.startswith("auth:"):
        return {**base, "ok": False, "executed": False, "blocked_reason": "invalid_authorization_id"}
    if not intent_id.startswith("intent:"):
        return {**base, "ok": False, "executed": False, "blocked_reason": "invalid_intent_id"}
    if not target_ref.startswith("task:"):
        return {**base, "ok": False, "executed": False, "blocked_reason": "invalid_target_ref"}
    if target_status not in {"pending", "review", "done", "blocked"}:
        return {**base, "ok": False, "executed": False, "blocked_reason": "invalid_target_status"}
    if not evidence_refs or not _evidence_key(evidence_refs):
        return {**base, "ok": False, "executed": False, "blocked_reason": "missing_evidence_refs"}
    try:
        state_path = _safe_harness_state_path(p, adapter_state_path)
        receipt_path = _safe_receipts_path(p)
        auth_path = _safe_authorizations_path(p)
    except ValueError as exc:
        return {**base, "ok": False, "executed": False, "blocked_reason": "invalid_executor_path", "error": str(exc)}

    store = _safe_read_json(auth_path, {"authorizations": []})
    records = store.get("authorizations") if isinstance(store, dict) else None
    if not isinstance(records, list) or not any(
        isinstance(record, dict)
        and _authorization_matches(
            record,
            authorization_id=authorization_id,
            intent_id=intent_id,
            target_ref=target_ref,
            target_status=target_status,
            evidence_refs=evidence_refs,
            adapter_state_path=state_path,
        )
        for record in records
    ):
        return {**base, "ok": False, "executed": False, "blocked_reason": "authorization_record_missing_or_mismatched", "authorization_path": str(auth_path), "adapter_state_path": str(state_path)}

    receipts = _read_jsonl(receipt_path)
    evidence_tuple = _evidence_key(evidence_refs)
    for receipt in receipts:
        if (
            receipt.get("authorization_id") == authorization_id
            and receipt.get("intent_id") == intent_id
            and receipt.get("target_ref") == target_ref
            and receipt.get("target_status") == target_status
            and _evidence_key(receipt.get("evidence_refs", [])) == evidence_tuple
            and receipt.get("adapter_state_path") == str(state_path)
            and receipt.get("verified") is True
        ):
            readback = _safe_read_json(state_path, {})
            already_verified = bool(
                isinstance(readback, dict)
                and isinstance(readback.get("tasks"), dict)
                and isinstance(readback["tasks"].get(target_ref), dict)
                and readback["tasks"][target_ref].get("status") == target_status
            )
            if not already_verified:
                return {**base, "ok": False, "executed": False, "blocked_reason": "idempotent_readback_mismatch", "receipt_id": receipt.get("receipt_id"), "adapter_state_path": str(state_path)}
            return {
                **base,
                "ok": True,
                "executed": False,
                "idempotent": True,
                "blocked_reason": "duplicate_already_verified",
                "receipt_id": receipt.get("receipt_id"),
                "adapter_state_path": str(state_path),
                "external_side_effects_executed": False,
                "real_taskpool_write_executed": False,
            }

    original_state = _safe_read_json(state_path, {"tasks": {}})
    state = original_state if isinstance(original_state, dict) else {"tasks": {}}
    if not isinstance(state.get("tasks", {}), dict):
        state = {"tasks": {}}
    state_before_mutation = json.loads(json.dumps(state))
    tasks = state.setdefault("tasks", {})
    current = tasks.get(target_ref, {})
    if not isinstance(current, dict):
        current = {}
    previous_status = current.get("status")
    if expected_previous_status is not None and previous_status != expected_previous_status:
        return {
            **base,
            "ok": False,
            "executed": False,
            "blocked_reason": "expected_previous_status_mismatch",
            "previous_status": previous_status,
            "expected_previous_status": expected_previous_status,
            "adapter_state_path": str(state_path),
        }

    current["status"] = target_status
    current["updated_at"] = _now_iso()
    tasks[target_ref] = current
    try:
        _safe_write_json(state_path, state)
    except ValueError as exc:
        return {**base, "ok": False, "executed": False, "blocked_reason": "invalid_adapter_state_path", "error": str(exc)}
    readback = _safe_read_json(state_path, {})
    verified = bool(
        isinstance(readback, dict)
        and isinstance(readback.get("tasks"), dict)
        and isinstance(readback["tasks"].get(target_ref), dict)
        and readback["tasks"][target_ref].get("status") == target_status
    )
    receipt_id = f"receipt:agent-os-local-harness:{len(receipts) + 1}"
    receipt = {
        "receipt_id": receipt_id,
        "generated_at": _now_iso(),
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "adapter": "local-file-harness",
        "target_ref": target_ref,
        "target_status": target_status,
        "previous_status": previous_status,
        "evidence_refs": evidence_tuple,
        "adapter_state_path": str(state_path),
        "executed": True,
        "verified": verified,
        "external_side_effects_executed": False,
        "real_taskpool_write_executed": False,
    }
    try:
        _append_jsonl(receipt_path, receipt)
    except Exception as exc:  # noqa: BLE001 - rollback state on any receipt failure
        rollback_failed = False
        try:
            _safe_write_json(state_path, state_before_mutation)
        except Exception:  # noqa: BLE001 - report that rollback did not complete
            rollback_failed = True
        return {
            **base,
            "ok": False,
            "executed": bool(rollback_failed),
            "verified": False,
            "blocked_reason": "receipt_append_failed_rollback_failed" if rollback_failed else "receipt_append_failed",
            "error": str(exc),
            "rollback_attempted": True,
            "rollback_failed": rollback_failed,
        }
    return {
        **base,
        "ok": verified,
        "executed": True,
        "idempotent": False,
        "receipt_id": receipt_id,
        "verified": verified,
        "target_ref": target_ref,
        "target_status": target_status,
        "previous_status": previous_status,
        "adapter_state_path": str(state_path),
        "receipt_path": str(receipt_path),
        "external_side_effects_executed": False,
        "real_taskpool_write_executed": False,
    }


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
