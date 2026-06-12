from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from session_sidecar import write_sidecar
from session_sidecar.producers import (
    acp_producer,
    browser_producer,
    cli_producer,
    gateway_producer,
    kanban_worker_producer,
    script_producer,
)

logger = logging.getLogger(__name__)

_BROWSER_TOOLS = {
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_press",
    "browser_type",
    "browser_scroll",
    "browser_console",
    "browser_get_images",
    "browser_vision",
    "browser_back",
}
_CLICK_TOOLS = {
    "browser_click",
}
_GATEWAY_PLATFORMS = {
    "telegram",
    "discord",
    "slack",
    "signal",
    "matrix",
    "ntfy",
    "feishu",
    "yuanbao",
    "sms",
}
_SCRIPT_TOOLS = {"terminal", "execute_code"}


def _fresh_turn_state(*, emitted_turns: set[str] | None = None) -> dict[str, Any]:
    return {
        "started_at": time.time(),
        "tool_calls": 0,
        "tool_call_summaries": [],
        "browser": {
            "active": False,
            "url_count": 0,
            "screenshot_count": 0,
            "click_count": 0,
            "last_url": "",
        },
        "script": {
            "active": False,
            "script_path": "",
            "cwd": "",
            "exit_code": None,
            "interpreter": "",
            "last_output": "",
        },
        "emitted_turns": emitted_turns or set(),
    }


def _runtime_state(agent) -> dict[str, Any]:
    state = getattr(agent, "_session_sidecar_runtime", None)
    if isinstance(state, dict):
        state.setdefault("tool_call_summaries", [])
        state.setdefault("script", {"active": False, "script_path": "", "cwd": "", "exit_code": None, "interpreter": "", "last_output": ""})
        state.setdefault("browser", {}).setdefault("last_url", "")
        return state
    state = _fresh_turn_state()
    setattr(agent, "_session_sidecar_runtime", state)
    return state


def _reset_runtime_state_after_emit(agent, emitted_turns: set[str]) -> None:
    setattr(agent, "_session_sidecar_runtime", _fresh_turn_state(emitted_turns=emitted_turns))


def _coerce_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _text_summary(value: Any, *, limit: int = 500) -> str:
    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return " ".join(text.split())[:limit]


def _proof_status(status: str) -> str:
    if status == "degraded":
        return "partial"
    if status in {"started", "succeeded", "failed", "blocked", "cancelled", "timed_out", "partial", "redacted"}:
        return status
    return "failed" if status else "partial"


def _emit_proof_log_entry(
    *,
    agent: Any,
    producer: str,
    run_id: str,
    status: str,
    duration_s: float,
    reason: str,
    state: dict[str, Any],
    producer_kwargs: dict[str, Any],
) -> None:
    """Best-effort Proof Log emission; never let sidecar logic crash a run."""
    try:
        from proof_log import proof_log_enabled, write_proof_log

        if not proof_log_enabled():
            return

        if producer == "browser":
            browser = state.get("browser") or {}
            link_type = "browser"
            payload = {
                "kind": "browser_session",
                "redaction": {"state": "raw", "policy": "none", "fields": [], "payload_hash": None},
                "data": {
                    "url": browser.get("last_url") or None,
                    "title": None,
                    "status_code": None,
                    "screenshot_ref": None,
                    "dom_snapshot_ref": None,
                    "browser_session_id": getattr(agent, "session_id", "") or None,
                    "action_count": int(state.get("tool_calls") or 0),
                },
            }
            input_summary = f"browser automation session {getattr(agent, 'session_id', '')}".strip()
        elif producer == "cron":
            link_type = "cron"
            payload = {
                "kind": "cron_job",
                "redaction": {"state": "raw", "policy": "none", "fields": [], "payload_hash": None},
                "data": {
                    "job_id": producer_kwargs.get("job_id") or producer_kwargs.get("task_id"),
                    "schedule": producer_kwargs.get("schedule"),
                    "scheduled_for": producer_kwargs.get("scheduled_for"),
                    "fired_at": producer_kwargs.get("fired_at"),
                    "exit_code": producer_kwargs.get("exit_code"),
                    "no_agent": producer_kwargs.get("no_agent"),
                    "delivery_target": producer_kwargs.get("delivery_target"),
                },
            }
            input_summary = f"cron job {payload['data'].get('job_id') or ''}".strip()
        elif producer == "script":
            script = state.get("script") or {}
            link_type = "script"
            payload = {
                "kind": "script_run",
                "redaction": {"state": "raw", "policy": "none", "fields": [], "payload_hash": None},
                "data": {
                    "script_path": script.get("script_path") or producer_kwargs.get("script_path"),
                    "argv_summary": producer_kwargs.get("args"),
                    "cwd": script.get("cwd") or producer_kwargs.get("cwd"),
                    "exit_code": script.get("exit_code") if script.get("exit_code") is not None else producer_kwargs.get("exit_code"),
                    "stdout_ref": None,
                    "stderr_ref": None,
                    "interpreter": script.get("interpreter") or producer_kwargs.get("interpreter"),
                },
            }
            input_summary = f"script run {payload['data'].get('script_path') or ''}".strip()
        elif producer == "acp":
            link_type = "script"
            payload = {
                "kind": "acp_session",
                "redaction": {"state": "raw", "policy": "none", "fields": [], "payload_hash": None},
                "data": {
                    "session_id": getattr(agent, "session_id", "") or producer_kwargs.get("session_id"),
                    "client_name": producer_kwargs.get("client_name"),
                    "command": producer_kwargs.get("command"),
                    "cwd": getattr(agent, "_cwd", None),
                },
            }
            input_summary = f"acp session {payload['data'].get('session_id') or ''}".strip()
        else:
            return

        error_message = str(producer_kwargs.get("error_message") or reason or "")
        error_class = str(producer_kwargs.get("error_class") or ("RunError" if status in {"failed", "cancelled", "timed_out"} else ""))
        errors = []
        if error_message or status in {"failed", "cancelled", "timed_out"}:
            errors.append({"code": error_class or "RunError", "message": error_message or status, "recoverable": True, "where": link_type})

        write_proof_log({
            "agent_id": os.environ.get("HERMES_AGENT_ID") or "hermes-agent",
            "profile": os.environ.get("HERMES_PROFILE") or "default",
            "session_id": getattr(agent, "session_id", "") or producer_kwargs.get("session_id"),
            "run_id": run_id,
            "link_type": link_type,
            "link_ref": producer_kwargs.get("task_id") or producer_kwargs.get("job_id") or producer_kwargs.get("script_path") or None,
            "status": _proof_status(status),
            "duration_ms": int(duration_s * 1000),
            "input_summary": input_summary,
            "output_summary": error_message or _text_summary((state.get("script") or {}).get("last_output") or producer_kwargs.get("output") or producer_kwargs.get("final_response") or status),
            "tool_calls": list(state.get("tool_call_summaries") or []),
            "errors": errors,
            "payload": payload,
            "metadata": {"source": "session_sidecar.runtime", "producer": producer},
        })
    except Exception as exc:
        logger.debug("proof log emission failed for %s run %s: %s", producer, run_id, exc)


def observe_tool_call(agent, *, function_name: str, function_args: dict[str, Any] | None, result: Any) -> None:
    state = _runtime_state(agent)
    state["tool_calls"] += 1
    browser = state["browser"]
    script = state["script"]
    args = function_args or {}
    parsed = _coerce_jsonish(result)
    output_summary = _text_summary(result)

    if function_name in _BROWSER_TOOLS:
        browser["active"] = True
    if function_name == "computer_use":
        action = str(args.get("action") or "")
        if action in {"capture", "click", "double_click", "right_click", "middle_click", "scroll", "type", "key"}:
            browser["active"] = True

    if function_name == "browser_navigate":
        browser["url_count"] += 1
        browser["last_url"] = str(args.get("url") or (parsed.get("url") if isinstance(parsed, dict) else "") or "")
    elif function_name == "computer_use" and str(args.get("action") or "") == "capture":
        browser["screenshot_count"] += 1
    elif function_name in _CLICK_TOOLS:
        browser["click_count"] += 1
    elif function_name == "computer_use" and str(args.get("action") or "") in {"click", "double_click", "right_click", "middle_click"}:
        browser["click_count"] += 1

    if function_name == "browser_vision":
        browser["screenshot_count"] += 1
    if isinstance(parsed, dict):
        screenshot_path = parsed.get("screenshot_path")
        if isinstance(screenshot_path, str) and screenshot_path.strip():
            browser["screenshot_count"] += 1

    if function_name in _SCRIPT_TOOLS:
        script["active"] = True
        if function_name == "terminal":
            command = str(args.get("command") or "")
            script["script_path"] = command.split()[0] if command else "terminal"
            script["cwd"] = str(args.get("workdir") or "")
            script["interpreter"] = "shell"
        else:
            script["script_path"] = "execute_code"
            script["interpreter"] = "python"
        if isinstance(parsed, dict):
            if "exit_code" in parsed:
                script["exit_code"] = parsed.get("exit_code")
            script["last_output"] = str(parsed.get("output") or parsed.get("error") or output_summary)
        else:
            script["last_output"] = output_summary

    status = "succeeded"
    error_code = None
    if isinstance(parsed, dict):
        exit_code = parsed.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            status = "failed"
            error_code = f"exit_{exit_code}"
        elif parsed.get("error"):
            status = "failed"
            error_code = "tool_error"
    state["tool_call_summaries"].append({
        "name": function_name,
        "status": status,
        "input_summary": _text_summary(args),
        "output_summary": output_summary,
        "error_code": error_code,
    })


def emit_turn_sidecar(
    agent,
    *,
    task_id: str = "",
    turn_id: str = "",
    completed: bool = False,
    interrupted: bool = False,
    model: Any = "",
    platform: str = "",
    reason: str = "",
    status_override: str = "",
    error_class_override: str = "",
    error_message_override: str = "",
) -> str | None:
    platform_key = str(platform or "").strip().lower()
    if platform_key == "cron":
        return None

    state = _runtime_state(agent)
    emit_key = turn_id or f"session:{getattr(agent, 'session_id', '')}:{reason or ('interrupted' if interrupted else 'completed') }"
    emitted_turns = state.setdefault("emitted_turns", set())
    if emit_key in emitted_turns:
        return None

    duration_s = max(0.0, time.time() - float(state.get("started_at") or time.time()))
    status = str(status_override or "").strip().lower() or (
        "succeeded" if completed and not interrupted else ("failed" if interrupted else "degraded")
    )
    error_class = str(error_class_override or "").strip()
    if not error_class and interrupted:
        error_class = "InterruptedTurn"
    error_message = str(error_message_override or "").strip()
    if not error_message and interrupted and reason:
        error_message = reason
    evidence_refs = []
    session_id = getattr(agent, "session_id", "") or ""
    if session_id:
        evidence_refs.append(f"session:{session_id}")
    if task_id:
        evidence_refs.append(f"task:{task_id}")
    if turn_id:
        evidence_refs.append(f"turn:{turn_id}")

    browser = state.get("browser") or {}
    producer = "cli"
    producer_kwargs: dict[str, Any] = {
        "session_id": session_id,
        "task_id": task_id,
        "model": str(model or getattr(agent, "model", "") or ""),
        "provider": str(getattr(agent, "provider", "") or ""),
        "duration_s": duration_s,
        "status": status,
        "error_class": error_class,
        "error_message": error_message,
        "evidence_refs": list(evidence_refs),
    }

    env_task_id = os.environ.get("HERMES_KANBAN_TASK", "")
    if env_task_id and task_id and task_id == env_task_id:
        producer = "kanban"
        producer_kwargs = {
            "task_id": task_id,
            "session_id": session_id,
            "workspace_path": os.environ.get("HERMES_KANBAN_WORKSPACE", ""),
            "assignee": os.environ.get("HERMES_PROFILE", "") or os.environ.get("HERMES_ASSIGNEE", ""),
            "model": str(model or getattr(agent, "model", "") or ""),
            "provider": str(getattr(agent, "provider", "") or ""),
            "duration_s": duration_s,
            "status": status,
            "error_class": error_class,
            "error_message": error_message,
            "evidence_refs": list(evidence_refs),
        }
    elif browser.get("active"):
        producer = "browser"
        producer_kwargs = {
            "session_key": session_id,
            "task_id": task_id,
            "url_count": int(browser.get("url_count") or 0),
            "screenshot_count": int(browser.get("screenshot_count") or 0),
            "click_count": int(browser.get("click_count") or 0),
            "duration_s": duration_s,
            "status": status,
            "error_class": error_class,
            "error_message": error_message,
            "model": str(model or getattr(agent, "model", "") or ""),
            "provider": str(getattr(agent, "provider", "") or ""),
            "screenshot_paths": [],
            "evidence_refs": list(evidence_refs),
        }
    elif (state.get("script") or {}).get("active"):
        script = state.get("script") or {}
        producer = "script"
        producer_kwargs = {
            "session_id": session_id,
            "script_path": script.get("script_path") or "tool-script",
            "cwd": script.get("cwd") or "",
            "exit_code": script.get("exit_code"),
            "interpreter": script.get("interpreter") or "",
            "args": [],
            "duration_s": duration_s,
            "status": status,
            "error_class": error_class,
            "error_message": error_message,
            "evidence_refs": list(evidence_refs),
        }
    elif platform_key == "acp":
        producer = "acp"
        producer_kwargs = {
            "session_id": session_id,
            "client_name": str(getattr(agent, "_session_sidecar_client_name", "") or ""),
            "command": str(getattr(agent, "_session_sidecar_command", "") or ""),
            "model": str(model or getattr(agent, "model", "") or ""),
            "provider": str(getattr(agent, "provider", "") or ""),
            "duration_s": duration_s,
            "status": status,
            "error_class": error_class,
            "error_message": error_message,
            "evidence_refs": list(evidence_refs),
        }
    elif platform_key in _GATEWAY_PLATFORMS:
        producer = "gateway"
        producer_kwargs = {
            "session_id": session_id,
            "platform": platform_key,
            "model": str(model or getattr(agent, "model", "") or ""),
            "message_count": 1,
            "duration_s": duration_s,
            "status": status,
            "error_class": error_class,
            "error_message": error_message,
            "evidence_refs": list(evidence_refs),
        }

    if producer == "kanban":
        sc = kanban_worker_producer(**producer_kwargs)
    elif producer == "browser":
        sc = browser_producer(**producer_kwargs)
    elif producer == "gateway":
        sc = gateway_producer(**producer_kwargs)
    elif producer == "script":
        sc = script_producer(
            script_path=str(producer_kwargs.get("script_path") or ""),
            args=list(producer_kwargs.get("args") or []),
            duration_s=duration_s,
            status=status,
            error_class=error_class,
            error_message=error_message,
            evidence_refs=list(evidence_refs),
        )
    elif producer == "acp":
        sc = acp_producer(**producer_kwargs)
    else:
        sc = cli_producer(**producer_kwargs)

    sc.evidence_refs = list(sc.evidence_refs or [])
    _append_unique(sc.evidence_refs, f"proof:session-sidecar:run:{sc.run_id}")
    if producer == "kanban":
        proof_path = Path(os.environ.get("HERMES_KANBAN_WORKSPACE", "")) / "proof.md"
        if proof_path.exists():
            _append_unique(sc.evidence_refs, f"artifact:{proof_path}")

    write_sidecar(sc)
    _emit_proof_log_entry(
        agent=agent,
        producer=producer,
        run_id=sc.run_id,
        status=status,
        duration_s=duration_s,
        reason=reason,
        state=state,
        producer_kwargs=producer_kwargs,
    )
    emitted_turns.add(emit_key)
    _reset_runtime_state_after_emit(agent, emitted_turns)
    return sc.run_id
