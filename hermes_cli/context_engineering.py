from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REQUIRED_TOP_LEVEL = {
    "schema",
    "objective",
    "sources",
    "constraints",
    "skills",
    "authority",
    "validation",
    "evidence_map",
}
_LEGACY_REQUIRED_TOP_LEVEL = {
    "objective",
    "non_goals",
    "constraints",
    "sources",
    "selected_context",
    "authority",
    "execution_plan",
    "validation_plan",
    "negative_cases",
    "learning_boundary",
}
_CLOSED_AUTHORITY_KEYS = ("external_dispatch", "publish_deploy_push", "destructive", "credential_entry")
_LEGACY_SOURCE_TYPES = {"file", "url", "session", "tool_output", "screenshot", "api", "log", "profile", "skill"}


class ContextPacketError(ValueError):
    """Raised when a Context Packet fails closed validation."""


def _load_packet(path: str | Path) -> dict[str, Any]:
    packet_path = Path(path)
    try:
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContextPacketError(f"file not found: {packet_path}") from exc
    except json.JSONDecodeError as exc:
        raise ContextPacketError(f"invalid json: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(payload, dict):
        raise ContextPacketError("packet must be a JSON object")
    return payload


def _validate_legacy_context_packet(packet: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(_LEGACY_REQUIRED_TOP_LEVEL - set(packet))
    if missing:
        raise ContextPacketError(f"missing required fields: {', '.join(missing)}")
    if len(str(packet.get("objective", "")).strip()) < 8:
        raise ContextPacketError("objective too short")

    sources = packet.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ContextPacketError("sources must be a non-empty list")
    source_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ContextPacketError(f"source[{index}] must be object")
        missing_source = [key for key in ("id", "type", "locator", "claims") if key not in source]
        if missing_source:
            raise ContextPacketError(f"source[{index}] missing {missing_source}")
        if source["type"] not in _LEGACY_SOURCE_TYPES:
            raise ContextPacketError(f"source[{index}] invalid type {source['type']}")
        if not source.get("claims"):
            raise ContextPacketError(f"source[{index}] has no claims")
        if str(source["id"]) in source_ids:
            raise ContextPacketError(f"duplicate source id {source['id']}")
        source_ids.add(str(source["id"]))

    selected = packet.get("selected_context")
    if not isinstance(selected, dict):
        raise ContextPacketError("selected_context must be object")
    evidence_ids = {str(item) for item in selected.get("evidence_ids", [])}
    unknown = sorted(evidence_ids - source_ids)
    if unknown:
        raise ContextPacketError(f"selected_context.evidence_ids not in sources: {unknown}")

    authority = packet.get("authority")
    if not isinstance(authority, dict):
        raise ContextPacketError("authority must be object")
    missing_auth = [key for key in ("allowed", "closed", "readback_required") if key not in authority]
    if missing_auth:
        raise ContextPacketError(f"authority missing {missing_auth}")
    if not authority.get("readback_required"):
        raise ContextPacketError("authority.readback_required must not be empty")

    boundary = packet.get("learning_boundary")
    if not isinstance(boundary, dict):
        raise ContextPacketError("learning_boundary must be object")
    missing_boundary = [key for key in ("durable_memory_allowed", "skill_update_allowed", "ssot_update_allowed", "do_not_save") if key not in boundary]
    if missing_boundary:
        raise ContextPacketError(f"learning_boundary missing {missing_boundary}")
    if not boundary.get("do_not_save"):
        raise ContextPacketError("learning_boundary.do_not_save must not be empty")

    return {
        "sources": len(sources),
        "evidence_ids": len(evidence_ids),
        "checks": len(packet.get("validation_plan") or []),
        "closed_authority": len(authority.get("closed") or []),
    }


def validate_context_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if packet.get("schema") != "hermes_context_packet_v1":
        return _validate_legacy_context_packet(packet)

    missing = sorted(_REQUIRED_TOP_LEVEL - set(packet))
    if missing:
        raise ContextPacketError(f"missing required fields: {', '.join(missing)}")
    if packet.get("schema") != "hermes_context_packet_v1":
        raise ContextPacketError("schema must be hermes_context_packet_v1")

    sources = packet.get("sources")
    evidence_map = packet.get("evidence_map")
    if not isinstance(sources, list) or not sources:
        raise ContextPacketError("sources must be a non-empty list")
    if not isinstance(evidence_map, list) or not evidence_map:
        raise ContextPacketError("evidence_map must be a non-empty list")

    source_ids = set()
    for source in sources:
        if not isinstance(source, dict) or not source.get("id"):
            raise ContextPacketError("each source must be an object with id")
        source_ids.add(str(source["id"]))

    evidence_ids = set()
    for item in evidence_map:
        if not isinstance(item, dict) or not item.get("id") or not item.get("source_id"):
            raise ContextPacketError("each evidence_map entry must include id and source_id")
        evidence_ids.add(str(item["id"]))
        if str(item["source_id"]) not in source_ids:
            raise ContextPacketError(f"evidence source_id not found: {item['source_id']}")

    validation = packet.get("validation")
    required_checks = validation.get("required_checks") if isinstance(validation, dict) else None
    if not isinstance(required_checks, list) or not required_checks:
        raise ContextPacketError("validation.required_checks must be a non-empty list")
    unknown_refs: list[str] = []
    missing_readback: list[str] = []
    for check in required_checks:
        if not isinstance(check, dict):
            raise ContextPacketError("each validation check must be an object")
        refs = check.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            missing_readback.append(str(check.get("id") or "<unnamed>"))
            continue
        for ref in refs:
            if str(ref) not in evidence_ids:
                unknown_refs.append(str(ref))
    if missing_readback:
        raise ContextPacketError(f"validation checks missing evidence_refs: {', '.join(missing_readback)}")
    if unknown_refs:
        raise ContextPacketError(f"unknown evidence_refs: {', '.join(sorted(set(unknown_refs)))}")

    constraints = packet.get("constraints")
    if not isinstance(constraints, dict):
        raise ContextPacketError("constraints must be an object")
    if constraints.get("memory_policy") != "do_not_save_wip":
        raise ContextPacketError("constraints.memory_policy must be do_not_save_wip")
    if not constraints.get("negative_cases"):
        raise ContextPacketError("constraints.negative_cases must not be empty")

    authority = packet.get("authority")
    if not isinstance(authority, dict):
        raise ContextPacketError("authority must be an object")
    closed_authority = 0
    for key in _CLOSED_AUTHORITY_KEYS:
        if authority.get(key) is False:
            closed_authority += 1
        else:
            raise ContextPacketError(f"authority.{key} must be false for edge lint")

    return {
        "sources": len(sources),
        "evidence_ids": len(evidence_ids),
        "checks": len(required_checks),
        "closed_authority": closed_authority,
    }


def lint_packet_path(path: str | Path) -> tuple[int, dict[str, Any] | str]:
    try:
        summary = validate_context_packet(_load_packet(path))
    except ContextPacketError as exc:
        return 1, str(exc)
    return 0, summary


def _print_lint_result(path: str | Path, *, as_json: bool = False) -> int:
    rc, result = lint_packet_path(path)
    if as_json:
        payload = {"path": str(path), "ok": rc == 0, "result": result}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    elif rc == 0:
        assert isinstance(result, dict)
        print(
            f"PASS: {path} sources={result['sources']} evidence_ids={result['evidence_ids']} "
            f"checks={result['checks']} closed_authority={result['closed_authority']}"
        )
    else:
        print(f"FAIL: {path}: {result}", file=sys.stderr)
    return rc


def _run_eval(root: str | Path, *, as_json: bool = False) -> int:
    root_path = Path(root)
    cases: list[tuple[str, Path, int]] = []
    for kind, expected in (("pass", 0), ("fail", 1)):
        fixture_dir = root_path / "eval" / "fixtures" / kind
        if fixture_dir.exists():
            cases.extend((kind, path, expected) for path in sorted(fixture_dir.glob("*.json")))

    failures: list[str] = []
    rows: list[dict[str, Any]] = []
    for kind, path, expected in cases:
        rc, result = lint_packet_path(path)
        label = "PASS_EXPECT" if kind == "pass" else "FAIL_EXPECT"
        row = {"case": path.name, "kind": kind, "exit": rc, "expected": expected, "result": result}
        rows.append(row)
        line = f"{label} {path.name}: exit={rc}"
        if rc != expected:
            failures.append(line)
            if not as_json:
                print(line, file=sys.stderr)
        elif not as_json:
            print(line)

    if as_json:
        print(json.dumps({"ok": not failures, "checked": len(rows), "failures": failures, "rows": rows}, ensure_ascii=False, sort_keys=True, indent=2))
    elif failures:
        print(f"REGRESSION FAIL checked={len(rows)} failures={len(failures)}", file=sys.stderr)
    else:
        print(f"\nREGRESSION PASS checked={len(rows)}")
    return 1 if failures else 0


def cmd_context(args) -> int:  # noqa: ANN001
    sub = getattr(args, "context_command", None)
    if sub == "packet" and getattr(args, "packet_command", None) == "lint":
        return _print_lint_result(getattr(args, "packet_path"), as_json=getattr(args, "json", False))
    if sub == "eval" and getattr(args, "eval_command", None) == "run":
        return _run_eval(getattr(args, "root", "."), as_json=getattr(args, "json", False))
    print("usage: hermes context {packet,eval} ...", file=sys.stderr)
    return 1
