#!/usr/bin/env python3
"""Build a deterministic risk index from Hermes/agent session JSONL.

The audit is deliberately read-only and rule based.  It is intended for
post-run evidence review: every emitted risk event includes a stable event id
and a pointer back to the originating session/message line or artifact path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{16,})\b"),
)

WRITE_TOOL_NAMES = {
    "write_file",
    "patch",
    "edit_file",
    "apply_patch",
    "create_file",
    "delete_file",
}
SHELL_TOOL_NAMES = {"terminal", "shell", "bash", "run_command", "execute_command"}
BROWSER_TOOL_NAMES = {"browser", "browser_action", "browser_navigate", "browser_click"}
SKILL_TOOL_NAMES = {"skill_view", "skill_manage", "skill_list"}
MCP_TOOL_HINT_RE = re.compile(r"(?i)\bmcp[_-]?(?:server|tool|call)\b|^mcp[_:. -]")

WRITE_PATH_RE = re.compile(
    r"(?P<path>(?:~|/Users/[^\s'\"]+|/tmp)/[^\s'\"]+|[\w./-]+\.(?:py|ts|tsx|js|json|ya?ml|toml|md|sh))"
)
CROSS_PROFILE_RE = re.compile(r"(?:~|/Users/[^\s]+)?/\.hermes/profiles/([^/\s]+)/")
SHELL_MUTATION_RE = re.compile(
    r"(?i)\b(?:rm\s+-|mv\s+|cp\s+|chmod\s+|chown\s+|git\s+(?:push|reset|clean|checkout)|"
    r"pip\s+install|npm\s+(?:install|publish)|docker\s+|kubectl\s+|railway\s+|vercel\s+|flyctl\s+|curl\s+[^\n]*\|\s*(?:sh|bash))"
)


@dataclass(frozen=True)
class RiskEvent:
    kind: str
    severity: str
    session_id: str
    message_id: str
    turn_index: int | None
    line: int
    tool_name: str
    target: str
    evidence_ref: str
    snippet: str

    def stable_id(self) -> str:
        payload = {
            "kind": self.kind,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "line": self.line,
            "tool_name": self.tool_name,
            "target": self.target,
            "snippet": self.snippet[:120],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    def to_json(self) -> dict[str, Any]:
        return {
            "event_id": self.stable_id(),
            "kind": self.kind,
            "severity": self.severity,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "turn_index": self.turn_index,
            "line": self.line,
            "tool_name": self.tool_name,
            "target": self.target,
            "evidence_ref": self.evidence_ref,
            "snippet": self.snippet,
        }


def _compact(value: Any, *, limit: int = 260) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def _text_blobs(record: dict[str, Any]) -> list[str]:
    blobs: list[str] = []
    for key in ("content", "text", "message", "reasoning", "output", "result"):
        value = record.get(key)
        if value:
            blobs.append(_compact(value, limit=2000))
    for key in ("arguments", "args", "input", "parameters"):
        value = record.get(key)
        if value:
            blobs.append(_compact(value, limit=2000))
    return blobs


def _tool_name(record: dict[str, Any]) -> str:
    for key in ("tool_name", "name", "tool", "function_name"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    function = record.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    tool_call = record.get("tool_call")
    if isinstance(tool_call, dict):
        return _tool_name(tool_call)
    return ""


def _iter_tool_records(record: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield top-level and nested tool-call records without mutating input.

    Hermes session JSONL has drifted across providers: some rows are flattened
    tool records, while others store OpenAI/Anthropic-style calls under
    ``tool_calls`` or nested message payloads. The audit index should be a
    replay aid, not a schema migration, so it normalizes common nested shapes
    into lightweight pseudo-records that retain the parent session/message ids.
    """

    yield record
    nested_values: list[Any] = []
    for key in ("tool_calls", "tools", "actions"):
        value = record.get(key)
        if isinstance(value, list):
            nested_values.extend(value)
    message = record.get("message")
    if isinstance(message, dict):
        for key in ("tool_calls", "tools", "actions"):
            value = message.get(key)
            if isinstance(value, list):
                nested_values.extend(value)

    for item in nested_values:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        function = item.get("function")
        if isinstance(function, dict):
            if "name" not in normalized and isinstance(function.get("name"), str):
                normalized["tool_name"] = function["name"]
            if "arguments" not in normalized and "arguments" in function:
                args = function["arguments"]
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        pass
                normalized["arguments"] = args
        for key in ("session_id", "conversation_id", "thread_id", "message_id", "turn_index", "turn", "round", "step"):
            normalized.setdefault(key, record.get(key))
        yield normalized


def _session_id(record: dict[str, Any], fallback: str) -> str:
    for key in ("session_id", "conversation_id", "thread_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _message_id(record: dict[str, Any], line_no: int) -> str:
    for key in ("message_id", "id", "uuid"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return f"line:{line_no}"


def _turn_index(record: dict[str, Any], line_no: int) -> int | None:
    for key in ("turn_index", "turn", "round", "step"):
        value = record.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return line_no


def _target_from_text(text: str) -> str:
    match = WRITE_PATH_RE.search(text)
    return match.group("path") if match else ""


def _artifact_ref(record: dict[str, Any], path: Path, line_no: int, message_id: str, session_id: str) -> str:
    for key in ("artifact_path", "proof_artifact", "path"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith("/"):
            return f"artifact:{value}"
    return f"jsonl:{path}:line:{line_no}:session:{session_id}:message:{message_id}"


def _emit(record: dict[str, Any], path: Path, line_no: int, fallback_session: str) -> Iterable[RiskEvent]:
    session_id = _session_id(record, fallback_session)
    message_id = _message_id(record, line_no)
    turn_index = _turn_index(record, line_no)
    tool_name = _tool_name(record)
    tool_l = tool_name.lower()
    evidence_ref = _artifact_ref(record, path, line_no, message_id, session_id)
    text = "\n".join(_text_blobs(record))
    lowered = tool_l or text.lower()

    if tool_l in WRITE_TOOL_NAMES or any(name in lowered for name in WRITE_TOOL_NAMES):
        target = _target_from_text(text)
        severity = "high" if target.startswith(("~/.hermes", "/Users/")) or CROSS_PROFILE_RE.search(text) else "medium"
        yield RiskEvent("file_write", severity, session_id, message_id, turn_index, line_no, tool_name, target, evidence_ref, _compact(text))
        if CROSS_PROFILE_RE.search(text):
            yield RiskEvent("cross_profile_write", "critical", session_id, message_id, turn_index, line_no, tool_name, target, evidence_ref, _compact(text))

    if tool_l in SHELL_TOOL_NAMES or any(name in lowered for name in SHELL_TOOL_NAMES):
        if SHELL_MUTATION_RE.search(text) or tool_l in SHELL_TOOL_NAMES:
            yield RiskEvent("shell_command", "high" if SHELL_MUTATION_RE.search(text) else "medium", session_id, message_id, turn_index, line_no, tool_name, _target_from_text(text), evidence_ref, _compact(text))

    if tool_l in BROWSER_TOOL_NAMES:
        yield RiskEvent("browser_action", "medium", session_id, message_id, turn_index, line_no, tool_name, "", evidence_ref, _compact(text))

    if tool_l in SKILL_TOOL_NAMES:
        yield RiskEvent("skill_action", "medium", session_id, message_id, turn_index, line_no, tool_name, "", evidence_ref, _compact(text))

    if MCP_TOOL_HINT_RE.search(tool_l):
        yield RiskEvent("mcp_action", "medium", session_id, message_id, turn_index, line_no, tool_name, "", evidence_ref, _compact(text))

    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            yield RiskEvent("secret_like_text", "critical", session_id, message_id, turn_index, line_no, tool_name, "", evidence_ref, _compact(match.group(0)))
            break


def audit_jsonl(path: str | Path) -> dict[str, Any]:
    jsonl_path = Path(path).expanduser().resolve()
    events: list[RiskEvent] = []
    parse_errors: list[dict[str, Any]] = []
    fallback_session = jsonl_path.stem
    line_count = 0

    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            line_count = line_no
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                parse_errors.append({"line": line_no, "error": str(exc)})
                continue
            if isinstance(record, dict):
                for tool_record in _iter_tool_records(record):
                    events.extend(_emit(tool_record, jsonl_path, line_no, fallback_session))

    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for event in events:
        key = (event.kind, event.session_id, event.message_id, event.tool_name, event.target or event.snippet[:120])
        if key in seen:
            continue
        seen.add(key)
        unique.append(event.to_json())

    counts: dict[str, int] = {}
    for event in unique:
        counts[event["kind"]] = counts.get(event["kind"], 0) + 1

    return {
        "schema": "hermes_session_jsonl_risk_index_v1",
        "source": str(jsonl_path),
        "line_count": line_count,
        "risk_count": len(unique),
        "risk_counts_by_kind": dict(sorted(counts.items())),
        "summary": {
            "tool_events": sum(1 for event in unique if event["tool_name"]),
            "skill_events": counts.get("skill_action", 0),
            "mcp_events": counts.get("mcp_action", 0),
            "critical_events": sum(1 for event in unique if event["severity"] == "critical"),
            "sessions": sorted({event["session_id"] for event in unique}),
        },
        "events": sorted(unique, key=lambda item: (item["line"], item["kind"], item["event_id"])),
        "parse_errors": parse_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic Hermes session JSONL risk index")
    parser.add_argument("jsonl", help="Hermes/session JSONL file to audit")
    parser.add_argument("--out", help="Write JSON index to this path instead of stdout")
    args = parser.parse_args(argv)

    result = audit_jsonl(args.jsonl)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).expanduser().write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
