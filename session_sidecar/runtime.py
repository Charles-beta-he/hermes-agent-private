from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from session_sidecar import write_sidecar
from session_sidecar.producers import (
    browser_producer,
    cli_producer,
    gateway_producer,
    kanban_worker_producer,
)

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


def _fresh_turn_state(*, emitted_turns: set[str] | None = None) -> dict[str, Any]:
    return {
        "started_at": time.time(),
        "tool_calls": 0,
        "browser": {
            "active": False,
            "url_count": 0,
            "screenshot_count": 0,
            "click_count": 0,
            "screenshot_paths": [],
        },
        "emitted_turns": emitted_turns or set(),
    }


def _runtime_state(agent) -> dict[str, Any]:
    state = getattr(agent, "_session_sidecar_runtime", None)
    if isinstance(state, dict):
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


def observe_tool_call(agent, *, function_name: str, function_args: dict[str, Any] | None, result: Any) -> None:
    state = _runtime_state(agent)
    state["tool_calls"] += 1
    browser = state["browser"]
    args = function_args or {}
    parsed = _coerce_jsonish(result)

    if function_name in _BROWSER_TOOLS:
        browser["active"] = True
    if function_name == "computer_use":
        action = str(args.get("action") or "")
        if action in {"capture", "click", "double_click", "right_click", "middle_click", "scroll", "type", "key"}:
            browser["active"] = True

    if function_name == "browser_navigate":
        browser["url_count"] += 1
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
            _append_unique(browser["screenshot_paths"], os.path.abspath(screenshot_path))


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
    status = "succeeded" if completed and not interrupted else ("failed" if interrupted else "degraded")
    error_class = "InterruptedTurn" if interrupted else ""
    error_message = reason if interrupted and reason else ""
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
            "screenshot_paths": list(browser.get("screenshot_paths") or []),
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
    else:
        sc = cli_producer(**producer_kwargs)

    sc.evidence_refs = list(sc.evidence_refs or [])
    _append_unique(sc.evidence_refs, f"proof:session-sidecar:run:{sc.run_id}")
    if producer == "kanban":
        proof_path = Path(os.environ.get("HERMES_KANBAN_WORKSPACE", "")) / "proof.md"
        if proof_path.exists():
            _append_unique(sc.evidence_refs, f"artifact:{proof_path}")

    write_sidecar(sc)
    emitted_turns.add(emit_key)
    _reset_runtime_state_after_emit(agent, emitted_turns)
    return sc.run_id
