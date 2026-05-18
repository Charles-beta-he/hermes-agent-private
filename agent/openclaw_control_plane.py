#!/usr/bin/env python3
"""OpenClaw-informed Hermes control-plane helpers.

This module is intentionally lightweight and optional. It does not connect to
OpenClaw and does not import OpenClaw runtime code. It consumes Hermes-local
artifacts generated from the OpenClaw OSS repository by:

    ~/.hermes/scripts/openclaw_to_hermes_gap.py analyze

Capabilities:
- activation hints for routing/tool planning
- schema-versioned JSONL record validation
- scoped AGENTS.md policy lookup for external repos
- explicit memory contract reference
- regression benchmark for generated control-plane artifacts
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

HOME = Path.home()
DEFAULT_GAP_DIR = HOME / ".hermes" / "openclaw-gap"
DEFAULT_OPENCLAW_REPO = HOME / ".hermes" / "research" / "openclaw"
DEFAULT_CONFIG = HOME / ".hermes" / "config.yaml"

SENSITIVE_RE = re.compile(r"(api[_-]?key|token|secret|password|authorization|cookie|bearer\s+[a-z0-9._-]+)", re.I)

REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    "trace": ("schema_version", "trace_id", "session_id", "timestamp", "event_type", "status"),
    "case": ("id", "source", "summary", "severity", "root_cause"),
    "benchmark": ("id", "case_id", "kind", "prompt", "pass_criteria"),
    "replay": ("id", "case_id", "expected_status", "oracle"),
    "review": ("id", "created_at", "round", "overall_verdict", "verdicts"),
    "final_gate": ("id", "created_at", "goal", "checks", "blocked", "verdict"),
}

FIELD_TYPES: Dict[str, Dict[str, type | Tuple[type, ...]]] = {
    "trace": {"schema_version": str, "trace_id": str, "session_id": str, "timestamp": str, "event_type": str, "status": str},
    "case": {"id": str, "source": str, "summary": str, "severity": str, "root_cause": str},
    "benchmark": {"id": str, "case_id": str, "kind": str, "prompt": str, "pass_criteria": (str, list, dict)},
    "replay": {"id": str, "case_id": str, "expected_status": str, "oracle": (str, dict)},
    "review": {"id": str, "created_at": str, "round": str, "overall_verdict": str, "verdicts": list},
    "final_gate": {"id": str, "created_at": str, "goal": str, "checks": dict, "blocked": list, "verdict": str},
}

ALLOWED_VALUES: Dict[str, Dict[str, set[str]]] = {
    "trace": {"status": {"ok", "passed", "failed", "error", "skipped", "blocked"}},
    "case": {"severity": {"P0", "P1", "P2", "P3", "P4"}},
    "review": {"overall_verdict": {"accept", "accept_with_conditions", "reject"}},
    "final_gate": {"verdict": {"pass", "blocked", "fail"}},
}

ALLOWED_FIELDS: Dict[str, set[str]] = {
    "trace": set(REQUIRED_FIELDS["trace"]) | {"project", "environment", "agent_role", "user_goal", "task_id", "plan_id", "step_id", "memory_reads", "memory_write_attempts", "tool_call", "tool_result", "error_class", "user_feedback", "verification", "evidence", "redaction", "parent_event_id", "privacy", "observation_summary", "tool_name", "tool_args_redacted"},
    "case": set(REQUIRED_FIELDS["case"]) | {"case_id", "created_at", "source_trace_ids", "goal", "expected_behavior", "actual_behavior", "failure", "failure_stage", "root_cause_guess", "evidence", "evidence_bundle", "privacy_status", "reproduction_hint", "benchmark_seed", "dashboard"},
    "benchmark": set(REQUIRED_FIELDS["benchmark"]) | {"created_at", "split", "task_prompt", "expected_behavior", "oracle_type", "forbidden_behavior", "required_evidence", "timeout", "risk_level", "metadata"},
    "replay": set(REQUIRED_FIELDS["replay"]) | {"created_at", "benchmark_id", "required_tools", "mocked_external_services", "fixture_paths", "expected_trace_pattern", "fail_conditions", "result", "passed", "dashboard"},
    "review": set(REQUIRED_FIELDS["review"]) | {"evidence", "dashboard", "model", "participants", "notes"},
    "final_gate": set(REQUIRED_FIELDS["final_gate"]) | {"latest_p10", "latest_benchmark", "latest_multimodel_review", "dashboard", "evidence"},
}



@dataclass
class ValidationResult:
    ok: bool
    kind: str
    errors: List[str]
    warnings: List[str]


@dataclass
class ActivationHint:
    family: str
    candidates: List[Dict[str, Any]]


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_scalar(value: str) -> Any:
    value = value.strip().strip("'\"")
    if value.lower() in {"true", "yes", "on"}:
        return True
    if value.lower() in {"false", "no", "off"}:
        return False
    if value.lower() in {"null", "none", "~"}:
        return None
    return value


def load_experimental_config(config_path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    """Read Hermes config for optional OpenClaw-informed control-plane flags.

    Productized behavior: prefer PyYAML when available (same ecosystem used by
    Hermes CLI config loading), then fall back to a tiny YAML subset so the
    helper remains dependency-light inside post-update checks.

    Supported shapes:
      openclaw_activation_hints: true
      openclaw_control_plane:
        activation_hints: true
        scoped_policy_context: true
        memory_write_gate: true
    """
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8", errors="ignore")
    try:
        import yaml  # type: ignore
        loaded = yaml.safe_load(text) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    result: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, result)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, value = raw.strip().split(":", 1)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else result
        if value.strip() == "":
            node: Dict[str, Any] = {}
            parent[key] = node
            stack.append((indent, node))
        else:
            parent[key] = _parse_scalar(value)
    return result


def openclaw_activation_hints_enabled(config: Dict[str, Any] | None = None, config_path: Path = DEFAULT_CONFIG) -> bool:
    cfg = dict(load_experimental_config(config_path))
    if config:
        cfg.update(config)
    nested = cfg.get("openclaw_control_plane") if isinstance(cfg.get("openclaw_control_plane"), dict) else {}
    return bool(cfg.get("openclaw_activation_hints") or nested.get("activation_hints"))


def load_activation_plan(gap_dir: Path = DEFAULT_GAP_DIR) -> Dict[str, Any]:
    return _read_json(gap_dir / "activation_plan.json")


def activation_hints_for_text(text: str, gap_dir: Path = DEFAULT_GAP_DIR, limit: int = 8) -> List[Dict[str, Any]]:
    """Return manifest-first capability hints without loading runtime code.

    The output is deliberately advisory. Callers must still use normal Hermes
    permission/tool selection paths before execution.
    """
    plan = load_activation_plan(gap_dir)
    families = plan.get("families") or {}
    lowered = text.lower()
    aliases = {
        "model": ["model", "模型", "llm", "provider"],
        "provider": ["provider", "供应商", "模型", "openai", "anthropic", "gemini"],
        "channel": ["channel", "渠道", "telegram", "discord", "slack", "email"],
        "tool": ["tool", "工具", "browser", "search", "file"],
        "memory": ["memory", "记忆", "recall", "session"],
        "gateway": ["gateway", "网关", "websocket", "rpc"],
        "webhook": ["webhook", "hook", "回调"],
        "browser": ["browser", "网页", "页面", "harness"],
        "search": ["search", "搜索", "research", "检索"],
        "storage": ["storage", "存储", "file", "文件"],
        "auth": ["auth", "oauth", "login", "认证", "授权"],
    }
    matched: List[ActivationHint] = []
    for family, candidates in sorted(families.items()):
        terms = aliases.get(family, [family])
        if family in lowered or any(term.lower() in lowered for term in terms):
            matched.append(ActivationHint(family=family, candidates=list(candidates)[:limit]))
    return [asdict(x) for x in matched]


def _walk_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def validate_record(kind: str, record: Dict[str, Any]) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []
    required = REQUIRED_FIELDS.get(kind)
    if not required:
        errors.append(f"unknown kind: {kind}")
        return ValidationResult(False, kind, errors, warnings)
    for field in required:
        if field not in record or record.get(field) in (None, ""):
            errors.append(f"missing required field: {field}")
    allowed_fields = ALLOWED_FIELDS.get(kind, set(required))
    for field in sorted(set(record) - allowed_fields):
        errors.append(f"unknown field: {field}")
    for field, expected in FIELD_TYPES.get(kind, {}).items():
        if field in record and record.get(field) is not None and not isinstance(record.get(field), expected):
            name = "|".join(t.__name__ for t in expected) if isinstance(expected, tuple) else expected.__name__
            errors.append(f"field {field} must be {name}")
    for field, allowed in ALLOWED_VALUES.get(kind, {}).items():
        value = record.get(field)
        if value not in (None, "") and str(value) not in allowed:
            errors.append(f"field {field} has unsupported value: {value}")
    schema_version = str(record.get("schema_version", ""))
    if kind == "trace" and not schema_version.startswith("openclaw.trace."):
        warnings.append("trace schema_version should start with openclaw.trace.")
    if any(SENSITIVE_RE.search(s) and "[REDACTED]" not in s for s in _walk_strings(record)):
        errors.append("possible unredacted secret/token detected")
    if kind == "final_gate":
        checks = record.get("checks") or {}
        if record.get("verdict") == "pass" and record.get("blocked"):
            errors.append("final_gate verdict pass cannot have blocked items")
        if record.get("verdict") == "pass" and any(v is False for v in checks.values()):
            errors.append("final_gate pass cannot contain failed checks")
    if kind == "review":
        for idx, item in enumerate(record.get("verdicts") or []):
            if not isinstance(item, dict) or item.get("verdict") not in {"accept", "accept_with_conditions", "reject"}:
                errors.append(f"review verdicts[{idx}] must contain valid verdict")
    return ValidationResult(not errors, kind, errors, warnings)

def validate_jsonl(path: Path, kind: str) -> Dict[str, Any]:
    total = passed = 0
    failures: List[Dict[str, Any]] = []
    if not path.exists():
        return {"path": str(path), "kind": kind, "total": 0, "passed": 0, "ok": False, "failures": [{"line": 0, "errors": ["file not found"]}]}
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if not line.strip():
            continue
        total += 1
        try:
            record = json.loads(line)
        except Exception as exc:
            failures.append({"line": line_no, "errors": [f"invalid json: {exc}"]})
            continue
        res = validate_record(kind, record)
        if res.ok:
            passed += 1
        else:
            failures.append({"line": line_no, "errors": res.errors, "warnings": res.warnings})
    return {"path": str(path), "kind": kind, "total": total, "passed": passed, "ok": total > 0 and not failures, "failures": failures[:20]}


def scoped_policy_for_path(repo: Path, target: Path | None = None) -> Dict[str, Any]:
    """Return root-to-leaf AGENTS.md policies applying to target."""
    repo = repo.resolve()
    target = (target or repo).resolve()
    if target.is_file():
        target = target.parent
    try:
        target.relative_to(repo)
    except Exception:
        raise ValueError(f"target is outside repo: {target}")
    scopes = []
    cur = repo
    parts = target.relative_to(repo).parts
    for i in range(0, len(parts) + 1):
        scope_dir = repo.joinpath(*parts[:i]) if i else repo
        agent_file = scope_dir / "AGENTS.md"
        if agent_file.exists():
            text = agent_file.read_text(encoding="utf-8", errors="ignore")
            title = next((line.strip("# ") for line in text.splitlines() if line.startswith("#")), scope_dir.name)
            rules = [line.strip()[2:] for line in text.splitlines() if line.strip().startswith("- ")][:30]
            scopes.append({"scope": str(scope_dir.relative_to(repo) or "."), "file": str(agent_file), "title": title, "rules": rules})
    return {"repo": str(repo), "target": str(target), "scopes": scopes, "count": len(scopes)}


def render_scoped_policy_context(repo: Path, target: Path | None = None, max_rules_per_scope: int = 12) -> str:
    policy = scoped_policy_for_path(repo, target)
    lines = ["# Scoped Repository Policy", "", f"Repo: {policy['repo']}", f"Target: {policy['target']}", ""]
    for scope in policy["scopes"]:
        lines.append(f"## {scope['scope']} — {scope['title']}")
        for rule in scope.get("rules", [])[:max_rules_per_scope]:
            lines.append(f"- {rule}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def memory_write_gate(source: str, content: str, evidence: str = "", durable_days: int = 0, contains_secret: bool = False) -> Dict[str, Any]:
    """Evaluate whether a proposed memory write matches Hermes contract."""
    errors: List[str] = []
    warnings: List[str] = []
    allowed_sources = set(memory_contract()["sources"])
    if source not in allowed_sources:
        errors.append(f"unknown memory source: {source}")
    if contains_secret or (SENSITIVE_RE.search(content) and "[REDACTED]" not in content):
        errors.append("memory content appears to contain an unredacted secret")
    if source == "semantic_memory":
        if durable_days and durable_days < 7:
            errors.append("semantic memory must be durable beyond 7 days")
        if any(term in content.lower() for term in ["todo", "wip", "in progress", "临时", "待办"]):
            errors.append("semantic memory cannot store temporary WIP/task state")
    if source == "procedural_skills" and not evidence:
        errors.append("procedural skill promotion requires verification evidence")
    if source == "case_memory" and not evidence:
        warnings.append("case memory should include evidence bundle or reproduction hint")
    if source == "session_recall":
        errors.append("session_recall is query-only; do not write durable memory")
    return {"ok": not errors, "source": source, "errors": errors, "warnings": warnings, "contract": memory_contract()["sources"].get(source, {})}


def memory_contract() -> Dict[str, Any]:
    return {
        "schema_version": "hermes.memory_contract.v1",
        "principle": "separate durable semantic facts, procedural skills, case memory, and session recall; write gates before persistence",
        "sources": {
            "semantic_memory": {"purpose": "durable user/environment facts", "write_gate": "stable beyond 7 days, compact, non-WIP"},
            "procedural_skills": {"purpose": "repeatable workflows and commands", "write_gate": "non-trivial workflow, verified, reusable"},
            "case_memory": {"purpose": "bad cases, root causes, evidence bundles, regression seeds", "write_gate": "has failure/evidence/root-cause candidate"},
            "session_recall": {"purpose": "past transcript search and temporary context", "write_gate": "no durable write; query only"},
        },
        "operations": ["query", "read", "list", "write_with_gate", "promote_case_to_skill", "promote_case_to_benchmark"],
        "forbidden": ["raw secrets", "temporary WIP as durable memory", "unverified production claims"],
    }


def benchmark(gap_dir: Path = DEFAULT_GAP_DIR, openclaw_repo: Path = DEFAULT_OPENCLAW_REPO) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    required = [
        "manifest_index.json",
        "activation_plan.json",
        "scoped_rules.json",
        "protocol_inventory.json",
        "memory_contract.json",
        "hermes_gap_matrix.json",
        "hermes_missing_functions.md",
        "dashboard.md",
    ]
    for name in required:
        path = gap_dir / name
        checks.append({"name": f"artifact:{name}", "pass": path.exists() and path.stat().st_size > 0, "path": str(path)})
    checks.append({"name": "activation_hints", "pass": bool(activation_hints_for_text("需要 provider tool memory gateway", gap_dir)), "details": activation_hints_for_text("需要 provider tool memory gateway", gap_dir)[:3]})
    try:
        policy = scoped_policy_for_path(openclaw_repo, openclaw_repo / "src" / "plugins")
        checks.append({"name": "scoped_policy", "pass": policy["count"] >= 1, "count": policy["count"]})
    except Exception as exc:
        checks.append({"name": "scoped_policy", "pass": False, "error": str(exc)})
    contract = memory_contract()
    checks.append({"name": "memory_contract", "pass": set(contract["sources"]) == {"semantic_memory", "procedural_skills", "case_memory", "session_recall"}})
    sample_trace = {
        "schema_version": "openclaw.trace.v1",
        "trace_id": "trace_bench",
        "session_id": "sess_bench",
        "timestamp": "2026-05-14T00:00:00Z",
        "event_type": "tool_error",
        "status": "failed",
        "evidence": {"token": "[REDACTED]"},
    }
    checks.append({"name": "schema_validator", "pass": validate_record("trace", sample_trace).ok})
    bad_secret = dict(sample_trace)
    bad_secret["evidence"] = {"authorization": "Bearer abcdef123456"}
    checks.append({"name": "schema_validator_rejects_secret", "pass": not validate_record("trace", bad_secret).ok})
    unknown_field = dict(sample_trace)
    unknown_field["extra"] = "not allowed"
    checks.append({"name": "schema_validator_rejects_unknown_field", "pass": not validate_record("trace", unknown_field).ok})
    checks.append({"name": "config_flag_default_off", "pass": openclaw_activation_hints_enabled({}, Path("/tmp/nonexistent-hermes-config.yaml")) is False})
    checks.append({"name": "scoped_policy_context_render", "pass": "Scoped Repository Policy" in render_scoped_policy_context(openclaw_repo, openclaw_repo / "src" / "plugins")})
    checks.append({"name": "memory_write_gate_accepts_verified_skill", "pass": memory_write_gate("procedural_skills", "repeatable verified workflow", evidence="test passed")["ok"]})
    checks.append({"name": "memory_write_gate_rejects_wip_semantic", "pass": not memory_write_gate("semantic_memory", "temporary TODO WIP", durable_days=1)["ok"]})
    passed = sum(1 for c in checks if c.get("pass"))
    return {"total": len(checks), "passed": passed, "pass_rate": passed / len(checks), "verdict": "pass" if passed == len(checks) else "fail", "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw-informed Hermes control-plane helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)
    hint = sub.add_parser("hints")
    hint.add_argument("text")
    val = sub.add_parser("validate-jsonl")
    val.add_argument("path")
    val.add_argument("--kind", required=True, choices=sorted(REQUIRED_FIELDS))
    pol = sub.add_parser("policy")
    pol.add_argument("--repo", default=str(DEFAULT_OPENCLAW_REPO))
    pol.add_argument("--target", default=None)
    ctx = sub.add_parser("policy-context")
    ctx.add_argument("--repo", default=str(DEFAULT_OPENCLAW_REPO))
    ctx.add_argument("--target", default=None)
    mwg = sub.add_parser("memory-write-gate")
    mwg.add_argument("--source", required=True)
    mwg.add_argument("--content", required=True)
    mwg.add_argument("--evidence", default="")
    mwg.add_argument("--durable-days", type=int, default=0)
    sub.add_parser("memory-contract")
    cfg = sub.add_parser("config")
    cfg.add_argument("--config", default=str(DEFAULT_CONFIG))
    bench = sub.add_parser("benchmark")
    bench.add_argument("--gap-dir", default=str(DEFAULT_GAP_DIR))
    bench.add_argument("--openclaw-repo", default=str(DEFAULT_OPENCLAW_REPO))
    args = parser.parse_args()
    if args.cmd == "hints":
        print(json.dumps(activation_hints_for_text(args.text), ensure_ascii=False, indent=2))
    elif args.cmd == "validate-jsonl":
        print(json.dumps(validate_jsonl(Path(args.path), args.kind), ensure_ascii=False, indent=2))
    elif args.cmd == "policy":
        target = Path(args.target) if args.target else None
        print(json.dumps(scoped_policy_for_path(Path(args.repo), target), ensure_ascii=False, indent=2))
    elif args.cmd == "policy-context":
        target = Path(args.target) if args.target else None
        print(render_scoped_policy_context(Path(args.repo), target))
    elif args.cmd == "memory-write-gate":
        print(json.dumps(memory_write_gate(args.source, args.content, args.evidence, args.durable_days), ensure_ascii=False, indent=2))
    elif args.cmd == "memory-contract":
        print(json.dumps(memory_contract(), ensure_ascii=False, indent=2))
    elif args.cmd == "config":
        print(json.dumps({"config": load_experimental_config(Path(args.config)), "openclaw_activation_hints_enabled": openclaw_activation_hints_enabled(config_path=Path(args.config))}, ensure_ascii=False, indent=2))
    elif args.cmd == "benchmark":
        print(json.dumps(benchmark(Path(args.gap_dir), Path(args.openclaw_repo)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
