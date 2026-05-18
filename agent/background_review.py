"""Background memory/skill review — fork the agent to evaluate the turn.

After every turn, ``AIAgent.run_conversation`` may call
:func:`spawn_background_review` to fire off a daemon thread that replays
the conversation snapshot in a forked :class:`AIAgent` and asks itself
"should any skill/memory be saved or updated?".  Writes go straight to
the memory + skill stores.  Main conversation and prompt cache are never
touched.

The fork inherits the parent's live runtime (provider, model, base_url,
credentials, cached system prompt) so it hits the same prefix cache and
uses the same auth.  It runs with a tool whitelist limited to memory and
skill management tools; everything else is denied at runtime.

See the ``hermes-agent-dev`` skill (``references/self-improvement-loop.md``)
for invariants and PR review criteria.
"""

from __future__ import annotations

import copy
import contextlib
import html
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread as _Thread
from typing import Any, Dict, List, Optional, Union

from hermes_cli.config import load_config
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_DEFAULT_MAX_INPUT_MESSAGES = 32
_DEFAULT_MAX_INPUT_CHARS = 60_000
_DEFAULT_TIMEOUT_SECONDS = 180
_STATUS_LOCK = Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_status_path() -> Path:
    return get_hermes_home() / "state" / "background_review_status.json"


def _default_html_report_path() -> Path:
    return get_hermes_home() / "reports" / "background_review_status.html"


def _path_from_config(value: Any, default: Path) -> Path:
    if not value:
        return default
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def _status_paths(bg_cfg: Dict[str, Any]) -> tuple[Path, Path]:
    status_path = _path_from_config(bg_cfg.get("status_path"), _default_status_path())
    html_path = _path_from_config(bg_cfg.get("html_report_path"), _default_html_report_path())
    return status_path, html_path


def _build_role_plan(*, provider: str, model: str) -> Dict[str, Any]:
    """Document the local multi-model/conductor roles used by bg-review."""
    return {
        "triager": model,
        "runtime_provider": provider,
        "critic": "tool_failure_extractor",
        "arbiter": "background_review_status_registry",
        "truth_gate": "tool_result_success",
        "evidence": ["review_agent._session_messages", "tool_result.success"],
    }




def _taskpool_config(bg_cfg: Dict[str, Any]) -> Dict[str, Any]:
    taskpool_cfg = bg_cfg.get("taskpool", {}) if isinstance(bg_cfg, dict) else {}
    return taskpool_cfg if isinstance(taskpool_cfg, dict) else {}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _coerce_priority(value: Any) -> str:
    value_s = str(value or "P2").upper().strip()
    return value_s if value_s in {"P0", "P1", "P2", "P3"} else "P2"


def _coerce_lane(value: Any) -> str:
    value_s = str(value or "evolution").strip().lower()
    return value_s if value_s in {"evolution", "delivery", "research", "ops", "advisory"} else "evolution"


def _extract_json_objects_from_text(text: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if not text:
        return candidates
    marker_match = re.search(r"BACKGROUND_REVIEW_TASKS_JSON:\s*(\{.*\})", text, re.S)
    snippets = [marker_match.group(1)] if marker_match else []
    if not snippets:
        snippets.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        snippets.append(stripped)
    for snippet in snippets:
        try:
            parsed = json.loads(snippet)
        except Exception:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    return candidates


def _extract_taskpool_candidates(review_messages: List[Dict]) -> List[Dict[str, Any]]:
    raw_tasks: List[Dict[str, Any]] = []
    for msg in review_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        for parsed in _extract_json_objects_from_text(content):
            tasks = parsed.get("background_review_tasks") or parsed.get("tasks") or []
            if isinstance(tasks, dict):
                tasks = [tasks]
            if isinstance(tasks, list):
                raw_tasks.extend(t for t in tasks if isinstance(t, dict))
    normalized: List[Dict[str, Any]] = []
    seen_titles: set[str] = set()
    for raw in raw_tasks:
        title = str(raw.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        priority = _coerce_priority(raw.get("priority") or raw.get("priority_layer"))
        lane = _coerce_lane(raw.get("lane"))
        requires_mm = bool(raw.get("requires_multimodel")) or priority in {"P0", "P1"} or lane == "evolution"
        normalized.append(
            {
                "title": title[:160],
                "objective": str(raw.get("objective") or title).strip()[:800],
                "priority": priority,
                "priority_layer": priority,
                "dependency_order": str(raw.get("dependency_order") or "1"),
                "multimodel_triage_result": str(raw.get("multimodel_triage_result") or priority),
                "lane": lane,
                "project": str(raw.get("project") or "hermes").strip() or "hermes",
                "concepts": str(raw.get("concepts") or "background-review,self-improvement,taskpool").strip(),
                "architecture_target": str(raw.get("architecture_target") or "local-brain").strip(),
                "capability_delta": str(raw.get("capability_delta") or "bg-review finding is tracked as governed taskpool work").strip(),
                "anti_drift_check": str(raw.get("anti_drift_check") or "status registry records candidate and taskpool add result").strip(),
                "acceptance_evidence": str(raw.get("acceptance_evidence") or "taskpool add result or suggestion appears in bg-review status JSON/HTML").strip(),
                "requires_multimodel": requires_mm,
                "multimodel_reason": str(raw.get("multimodel_reason") or "background_review_taskpool_candidate").strip(),
                "expected_participants": str(raw.get("expected_participants") or "claude-opus-4-7,gpt-5.5,deepseek-v4-pro,mimo-v2.5-pro").strip(),
                "truth_gate": str(raw.get("truth_gate") or "evidence_bundle").strip(),
                "evidence_bundle": str(raw.get("evidence_bundle") or "background_review_status_json,html_report,tool_results").strip(),
            }
        )
    return normalized[:5]


def _taskpool_orchestrator_path(taskpool_cfg: Dict[str, Any]) -> str:
    return str(taskpool_cfg.get("orchestrator_path") or (get_hermes_home() / "scripts" / "brain_task_orchestrator.py"))


def _enqueue_taskpool_candidates(candidates: List[Dict[str, Any]], taskpool_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    orchestrator_path = _taskpool_orchestrator_path(taskpool_cfg)
    results: List[Dict[str, Any]] = []
    for candidate in candidates:
        cmd = [
            "python3",
            orchestrator_path,
            "add",
            candidate["title"],
            "--priority", candidate["priority"],
            "--project", candidate["project"],
            "--lane", candidate["lane"],
            "--concepts", candidate["concepts"],
            "--architecture-target", candidate["architecture_target"],
            "--capability-delta", candidate["capability_delta"],
            "--anti-drift-check", candidate["anti_drift_check"],
            "--acceptance-evidence", candidate["acceptance_evidence"],
            "--priority-layer", candidate["priority_layer"],
            "--dependency-order", candidate["dependency_order"],
            "--multimodel-triage-result", candidate["multimodel_triage_result"],
            "--multimodel-reason", candidate["multimodel_reason"],
            "--expected-participants", candidate["expected_participants"],
            "--truth-gate", candidate["truth_gate"],
            "--evidence-bundle", candidate["evidence_bundle"],
            "--objective", candidate["objective"],
            "--json",
        ]
        if candidate.get("requires_multimodel"):
            cmd.insert(cmd.index("--multimodel-reason"), "--requires-multimodel")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            try:
                payload = json.loads(proc.stdout or "{}")
            except Exception:
                payload = {"stdout": (proc.stdout or "")[-1000:]}
            payload.update({"returncode": proc.returncode, "title": candidate["title"]})
            if proc.stderr:
                payload["stderr"] = proc.stderr[-1000:]
            results.append(payload)
        except Exception as exc:
            results.append({"returncode": -1, "title": candidate["title"], "error": str(exc)})
    return results


def _build_taskpool_status(review_messages: List[Dict], bg_cfg: Dict[str, Any]) -> Dict[str, Any]:
    taskpool_cfg = _taskpool_config(bg_cfg)
    enabled = _truthy(taskpool_cfg.get("enabled"))
    mode = str(taskpool_cfg.get("mode") or "suggest").strip().lower()
    candidates = _extract_taskpool_candidates(review_messages) if enabled else []
    status: Dict[str, Any] = {
        "enabled": enabled,
        "mode": mode if mode in {"suggest", "enqueue"} else "suggest",
        "candidates": candidates,
        "enqueued": [],
    }
    if enabled and status["mode"] == "enqueue" and candidates:
        status["enqueued"] = _enqueue_taskpool_candidates(candidates, taskpool_cfg)
    return status


def _augment_prompt_for_taskpool(prompt: str, bg_cfg: Dict[str, Any]) -> str:
    taskpool_cfg = _taskpool_config(bg_cfg)
    if not _truthy(taskpool_cfg.get("enabled")):
        return prompt
    return (
        prompt
        + "\n\nTaskpool candidate mode is enabled. If this review discovers follow-up work "
        "too complex, risky, cross-cutting, or long-running for a direct memory/skill "
        "update, DO NOT call taskpool tools. Instead, include a final assistant-only JSON "
        "block prefixed with BACKGROUND_REVIEW_TASKS_JSON:. Shape: "
        "{\"tasks\":[{\"title\":\"...\",\"objective\":\"...\",\"priority\":\"P2\","
        "\"lane\":\"evolution\",\"requires_multimodel\":true,"
        "\"capability_delta\":\"...\",\"anti_drift_check\":\"...\","
        "\"acceptance_evidence\":\"...\"}]}. Keep at most 5 tasks. "
        "Use P1/P2/P3 priority layers, dependency_order, multimodel_triage_result, "
        "truth_gate=evidence_bundle when useful."
    )


def _write_background_review_status(
    status: Dict[str, Any],
    *,
    status_path: Path,
    html_path: Path,
) -> Dict[str, Any]:
    """Persist JSON status and a human-readable HTML dashboard."""
    status = dict(status)
    status["updated_at"] = _utc_now_iso()
    status["status_path"] = str(status_path)
    status["html_report_path"] = str(html_path)
    with _STATUS_LOCK:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(render_background_review_status_html(status), encoding="utf-8")
    return status


def get_background_review_status(path: Optional[Union[str, os.PathLike[str]]] = None) -> Dict[str, Any]:
    """Read the latest bg-review status registry."""
    status_path = Path(path) if path is not None else _default_status_path()
    html_path = _default_html_report_path()
    if not status_path.exists():
        return {
            "state": "never_run",
            "status_path": str(status_path),
            "html_report_path": str(html_path),
        }
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "state": "unreadable",
            "status_path": str(status_path),
            "html_report_path": str(html_path),
            "error": str(exc),
        }
    return data if isinstance(data, dict) else {
        "state": "invalid",
        "status_path": str(status_path),
        "html_report_path": str(html_path),
    }


def render_background_review_status_html(status: Dict[str, Any]) -> str:
    """Render a compact standalone HTML status report for humans."""
    def esc(value: Any) -> str:
        return html.escape(str(value))

    runtime = status.get("runtime") if isinstance(status.get("runtime"), dict) else {}
    role_plan = status.get("role_plan") if isinstance(status.get("role_plan"), dict) else {}
    actions = status.get("actions") if isinstance(status.get("actions"), list) else []
    failures = status.get("failures") if isinstance(status.get("failures"), list) else []
    taskpool_raw = status.get("taskpool")
    taskpool: Dict[str, Any] = taskpool_raw if isinstance(taskpool_raw, dict) else {}
    taskpool_candidates_raw = taskpool.get("candidates")
    taskpool_enqueued_raw = taskpool.get("enqueued")
    taskpool_candidates = taskpool_candidates_raw if isinstance(taskpool_candidates_raw, list) else []
    taskpool_enqueued = taskpool_enqueued_raw if isinstance(taskpool_enqueued_raw, list) else []
    state = esc(status.get("state", "unknown"))
    rows = "".join(
        f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>"
        for k, v in {
            "state": status.get("state", "unknown"),
            "session": status.get("session_id", ""),
            "provider": runtime.get("provider", ""),
            "model": runtime.get("model", ""),
            "messages": status.get("snapshot_messages", ""),
            "chars": status.get("snapshot_chars", ""),
            "updated": status.get("updated_at", ""),
        }.items()
    )
    roles = "".join(f"<li><b>{esc(k)}</b>: {esc(v)}</li>" for k, v in role_plan.items())
    action_items = "".join(f"<li>{esc(item)}</li>" for item in actions) or "<li>None</li>"
    failure_items = "".join(f"<li>{esc(item)}</li>" for item in failures) or "<li>None</li>"
    taskpool_candidate_items = "".join(
        f"<li><b>{esc(item.get('priority', ''))}</b> {esc(item.get('title', ''))} <code>{esc(item.get('lane', ''))}</code></li>"
        for item in taskpool_candidates
        if isinstance(item, dict)
    ) or "<li>None</li>"
    taskpool_enqueued_items = "".join(
        f"<li>{esc(item.get('id') or item.get('title') or item)}</li>"
        for item in taskpool_enqueued
        if isinstance(item, dict)
    ) or "<li>None</li>"
    taskpool_summary = (
        f"mode={esc(taskpool.get('mode', 'disabled'))}, "
        f"enabled={esc(taskpool.get('enabled', False))}, "
        f"candidates={len(taskpool_candidates)}, enqueued={len(taskpool_enqueued)}"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hermes background review status</title>
  <style>
    :root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; padding: 24px; background: Canvas; color: CanvasText; }}
    main {{ max-width: 920px; margin: 0 auto; }}
    .card {{ border: 1px solid color-mix(in srgb, CanvasText 16%, transparent); border-radius: 14px; padding: 18px; margin: 16px 0; box-shadow: 0 1px 2px color-mix(in srgb, CanvasText 10%, transparent); }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; background: color-mix(in srgb, CanvasText 10%, transparent); font-variant-numeric: tabular-nums; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; width: 180px; color: color-mix(in srgb, CanvasText 72%, transparent); }}
    th, td {{ padding: 8px 0; border-bottom: 1px solid color-mix(in srgb, CanvasText 10%, transparent); vertical-align: top; }}
    ul {{ margin: 8px 0 0; padding-left: 20px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  </style>
</head>
<body>
<main>
  <h1>Self-improvement review <span class="badge">{state}</span></h1>
  <section class="card"><h2>Runtime</h2><table>{rows}</table></section>
  <section class="card"><h2>Conductor roles</h2><ul>{roles}</ul></section>
  <section class="card"><h2>Actions</h2><ul>{action_items}</ul></section>
  <section class="card"><h2>Failures</h2><ul>{failure_items}</ul></section>
  <section class="card"><h2>Taskpool</h2><p>{taskpool_summary}</p><h3>Candidates</h3><ul>{taskpool_candidate_items}</ul><h3>Enqueued</h3><ul>{taskpool_enqueued_items}</ul></section>
  <p><small>Generated by Hermes bg-review status registry.</small></p>
</main>
</body>
</html>"""


def _background_review_config() -> Dict[str, Any]:
    """Load optional background-review runtime knobs from config.yaml.

    The feature must be fail-soft: a malformed config should never break the
    foreground turn's post-response cleanup path.
    """
    try:
        cfg = load_config() or {}
    except Exception:
        return {}
    bg_cfg = cfg.get("background_review", {}) if isinstance(cfg, dict) else {}
    return bg_cfg if isinstance(bg_cfg, dict) else {}


def _as_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _compact_review_snapshot(
    messages_snapshot: List[Dict],
    *,
    max_messages: int = _DEFAULT_MAX_INPUT_MESSAGES,
    max_chars: int = _DEFAULT_MAX_INPUT_CHARS,
) -> List[Dict]:
    """Return a bounded recent-history snapshot for the review fork.

    Background review is a learning pass, not a full replay. Keeping the recent
    tail prevents 100k+ token review calls while preserving the latest user
    correction / tool outcome that usually caused the nudge.
    """
    if not messages_snapshot:
        return []
    tail = list(messages_snapshot[-max_messages:]) if max_messages else list(messages_snapshot)
    compacted: List[Dict] = []
    used_chars = 0
    for msg in reversed(tail):
        if not isinstance(msg, dict):
            continue
        cloned = copy.deepcopy(msg)
        content = cloned.get("content", "")
        content_len = len(content) if isinstance(content, str) else len(str(content))
        if used_chars + content_len > max_chars:
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            if isinstance(content, str):
                cloned["content"] = content[-remaining:]
            else:
                cloned["content"] = str(content)[-remaining:]
            used_chars = max_chars
        else:
            used_chars += content_len
        compacted.append(cloned)
        if used_chars >= max_chars:
            break
    compacted.reverse()
    return compacted


def _snapshot_char_count(messages: List[Dict]) -> int:
    total = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        total += len(content) if isinstance(content, str) else len(str(content))
    return total


def _emit_background_review_status(agent: Any, message: str) -> None:
    """Surface bg-review status through both CLI and gateway callback paths."""
    try:
        safe_print = getattr(agent, "_safe_print", None)
        if callable(safe_print):
            safe_print(f"  {message}")
    except Exception:
        pass
    callback = getattr(agent, "background_review_callback", None)
    if callback:
        try:
            callback(message)
        except Exception:
            pass


def _summarize_background_review_failures(
    review_messages: List[Dict],
    prior_snapshot: Optional[List[Dict]] = None,
) -> List[str]:
    """Extract compact failure reasons from genuinely new review tool messages.

    The review agent inherits the parent conversation snapshot. Main-turn tool
    failures may therefore be present in ``review_agent._session_messages`` even
    when bg-review itself completed cleanly. Treat only tool results introduced
    by the review pass as bg-review failures; otherwise a resolved foreground
    edit mistake can be re-announced as a false self-improvement failure.
    """
    prior_tool_ids = {
        str(msg.get("tool_call_id"))
        for msg in (prior_snapshot or [])
        if isinstance(msg, dict) and msg.get("role") == "tool" and msg.get("tool_call_id")
    }
    prior_tool_contents = {
        str(msg.get("content", ""))
        for msg in (prior_snapshot or [])
        if isinstance(msg, dict) and msg.get("role") == "tool" and not msg.get("tool_call_id")
    }
    failures: List[str] = []
    for msg in review_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        tool_call_id = msg.get("tool_call_id")
        if tool_call_id and str(tool_call_id) in prior_tool_ids:
            continue
        content = str(msg.get("content", ""))
        if not tool_call_id and content in prior_tool_contents:
            continue
        try:
            data = json.loads(msg.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("success") is not False:
            continue
        reason = data.get("error") or data.get("message") or "tool call failed"
        reason = str(reason).strip()
        if reason and reason not in failures:
            failures.append(reason)
    return failures[:3]


def _run_conversation_with_timeout(
    review_agent: Any,
    *,
    timeout_seconds: int,
    approval_callback: Any = None,
    **kwargs: Any,
) -> bool:
    """Run review_agent.run_conversation with a wall-clock timeout.

    Python cannot safely kill a running thread, so the timeout path returns to
    the caller and leaves the inner daemon to unwind independently. This still
    solves the user-visible failure mode: the foreground session receives a
    bounded status instead of waiting silently for an unbounded review pass.
    """
    if timeout_seconds <= 0:
        review_agent.run_conversation(**kwargs)
        return True

    result: Dict[str, Any] = {}

    def _target() -> None:
        try:
            if approval_callback is not None:
                try:
                    from tools.terminal_tool import set_approval_callback as _set_cb
                    _set_cb(approval_callback)
                except Exception:
                    pass
            review_agent.run_conversation(**kwargs)
            result["ok"] = True
        except BaseException as exc:  # re-raised in caller thread
            result["exc"] = exc
        finally:
            if approval_callback is not None:
                try:
                    from tools.terminal_tool import set_approval_callback as _set_cb
                    _set_cb(None)
                except Exception:
                    pass

    worker = _Thread(target=_target, daemon=True, name="bg-review-call")
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        return False
    if "exc" in result:
        raise result["exc"]
    return True


# Review-prompt strings — used by ``spawn_background_review_thread`` to build
# the user-message that the forked review agent receives.  AIAgent exposes
# them as class attributes (``_MEMORY_REVIEW_PROMPT`` etc.) for back-compat;
# the actual text lives here so future edits are one-place.
_MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — their persona, desires, "
    "preferences, or personal details worth remembering?\n"
    "2. Has the user expressed expectations about how you should behave, their work "
    "style, or ways they want you to operate?\n\n"
    "If something stands out, save it using the memory tool. "
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)

_SKILL_REVIEW_PROMPT = (
    "Review the conversation above and update the skill library. Be "
    "ACTIVE — most sessions produce at least one skill update, even if "
    "small. A pass that does nothing is a missed learning opportunity, "
    "not a neutral outcome.\n\n"
    "Target shape of the library: CLASS-LEVEL skills, each with a rich "
    "SKILL.md and a `references/` directory for session-specific detail. "
    "Not a long flat list of narrow one-session-one-skill entries. This "
    "shapes HOW you update, not WHETHER you update.\n\n"
    "Signals to look for (any one of these warrants action):\n"
    "  • User corrected your style, tone, format, legibility, or "
    "verbosity. Frustration signals like 'stop doing X', 'this is too "
    "verbose', 'don't format like this', 'why are you explaining', "
    "'just give me the answer', 'you always do Y and I hate it', or an "
    "explicit 'remember this' are FIRST-CLASS skill signals, not just "
    "memory signals. Update the relevant skill(s) to embed the "
    "preference so the next session starts already knowing.\n"
    "  • User corrected your workflow, approach, or sequence of steps. "
    "Encode the correction as a pitfall or explicit step in the skill "
    "that governs that class of task.\n"
    "  • Non-trivial technique, fix, workaround, debugging path, or "
    "tool-usage pattern emerged that a future session would benefit "
    "from. Capture it.\n"
    "  • A skill that got loaded or consulted this session turned out "
    "to be wrong, missing a step, or outdated. Patch it NOW.\n\n"
    "Preference order — prefer the earliest action that fits, but do "
    "pick one when a signal above fired:\n"
    "  1. UPDATE A CURRENTLY-LOADED SKILL. Look back through the "
    "conversation for skills the user loaded via /skill-name or you "
    "read via skill_view. If any of them covers the territory of the "
    "new learning, PATCH that one first. It is the skill that was in "
    "play, so it's the right one to extend.\n"
    "  2. UPDATE AN EXISTING UMBRELLA (via skills_list + skill_view). "
    "If no loaded skill fits but an existing class-level skill does, "
    "patch it. Add a subsection, a pitfall, or broaden a trigger.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella. Skills can be "
    "packaged with three kinds of support files — use the right "
    "directory per kind:\n"
    "     • `references/<topic>.md` — session-specific detail (error "
    "transcripts, reproduction recipes, provider quirks) AND "
    "condensed knowledge banks: quoted research, API docs, external "
    "authoritative excerpts, or domain notes you found while working "
    "on the problem. Write it concise and for the value of the task, "
    "not as a full mirror of upstream docs.\n"
    "     • `templates/<name>.<ext>` — starter files meant to be "
    "copied and modified (boilerplate configs, scaffolding, a "
    "known-good example the agent can `reproduce with modifications`).\n"
    "     • `scripts/<name>.<ext>` — statically re-runnable actions "
    "the skill can invoke directly (verification scripts, fixture "
    "generators, deterministic probes, anything the agent should run "
    "rather than hand-type each time).\n"
    "     Add support files via skill_manage action=write_file with "
    "file_path starting 'references/', 'templates/', or 'scripts/'. "
    "The umbrella's SKILL.md should gain a one-line pointer to any "
    "new support file so future agents know it exists.\n"
    "  4. CREATE A NEW CLASS-LEVEL UMBRELLA SKILL when no existing "
    "skill covers the class. The name MUST be at the class level. "
    "The name MUST NOT be a specific PR number, error string, feature "
    "codename, library-alone name, or 'fix-X / debug-Y / audit-Z-today' "
    "session artifact. If the proposed name only makes sense for "
    "today's task, it's wrong — fall back to (1), (2), or (3).\n\n"
    "User-preference embedding (important): when the user expressed a "
    "style/format/workflow preference, the update belongs in the "
    "SKILL.md body, not just in memory. Memory captures 'who the user "
    "is and what the current situation and state of your operations "
    "are'; skills capture 'how to do this class of task for this "
    "user'. When they complain about how you handled a task, the "
    "skill that governs that task needs to carry the lesson.\n\n"
    "If you notice two existing skills that overlap, note it in your "
    "reply — the background curator handles consolidation at scale.\n\n"
    "Do NOT capture (these become persistent self-imposed constraints "
    "that bite you later when the environment changes):\n"
    "  • Environment-dependent failures: missing binaries, fresh-install "
    "errors, post-migration path mismatches, 'command not found', "
    "unconfigured credentials, uninstalled packages. The user can fix "
    "these — they are not durable rules.\n"
    "  • Negative claims about tools or features ('browser tools do not "
    "work', 'X tool is broken', 'cannot use Y from execute_code'). These "
    "harden into refusals the agent cites against itself for months "
    "after the actual problem was fixed.\n"
    "  • Session-specific transient errors that resolved before the "
    "conversation ended. If retrying worked, the lesson is the retry "
    "pattern, not the original failure.\n"
    "  • One-off task narratives. A user asking 'summarize today's "
    "market' or 'analyze this PR' is not a class of work that warrants "
    "a skill.\n\n"
    "If a tool failed because of setup state, capture the FIX (install "
    "command, config step, env var to set) under an existing setup or "
    "troubleshooting skill — never 'this tool does not work' as a "
    "standalone constraint.\n\n"
    "'Nothing to save.' is a real option but should NOT be the "
    "default. If the session ran smoothly with no corrections and "
    "produced no new technique, just say 'Nothing to save.' and stop. "
    "Otherwise, act."
)

_COMBINED_REVIEW_PROMPT = (
    "Review the conversation above and update two things:\n\n"
    "**Memory**: who the user is. Did the user reveal persona, "
    "desires, preferences, personal details, or expectations about "
    "how you should behave? Save facts about the user and durable "
    "preferences with the memory tool.\n\n"
    "**Skills**: how to do this class of task. Be ACTIVE — most "
    "sessions produce at least one skill update. A pass that does "
    "nothing is a missed learning opportunity, not a neutral outcome.\n\n"
    "Target shape of the skill library: CLASS-LEVEL skills with a rich "
    "SKILL.md and a `references/` directory for session-specific detail. "
    "Not a long flat list of narrow one-session-one-skill entries.\n\n"
    "Signals that warrant a skill update (any one is enough):\n"
    "  • User corrected your style, tone, format, legibility, "
    "verbosity, or approach. Frustration is a FIRST-CLASS skill "
    "signal, not just a memory signal. 'stop doing X', 'don't format "
    "like this', 'I hate when you Y' — embed the lesson in the skill "
    "that governs that task so the next session starts fixed.\n"
    "  • Non-trivial technique, fix, workaround, or debugging path "
    "emerged.\n"
    "  • A skill that was loaded or consulted turned out wrong, "
    "missing, or outdated — patch it now.\n\n"
    "Preference order for skills — pick the earliest that fits:\n"
    "  1. UPDATE A CURRENTLY-LOADED SKILL. Check what skills were "
    "loaded via /skill-name or skill_view in the conversation. If one "
    "of them covers the learning, PATCH it first. It was in play; "
    "it's the right place.\n"
    "  2. UPDATE AN EXISTING UMBRELLA (skills_list + skill_view to "
    "find the right one). Patch it.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella via "
    "skill_manage action=write_file. Three kinds: "
    "`references/<topic>.md` for session-specific detail OR condensed "
    "knowledge banks (quoted research, API docs excerpts, domain "
    "notes) written concise and task-focused; `templates/<name>.<ext>` "
    "for starter files meant to be copied and modified; "
    "`scripts/<name>.<ext>` for statically re-runnable actions "
    "(verification, fixture generators, probes). Add a one-line "
    "pointer in SKILL.md so future agents find them.\n"
    "  4. CREATE A NEW CLASS-LEVEL UMBRELLA when nothing exists. "
    "Name at the class level — NOT a PR number, error string, "
    "codename, library-alone name, or 'fix-X / debug-Y' session "
    "artifact. If the name only fits today's task, fall back to (1), "
    "(2), or (3).\n\n"
    "User-preference embedding: when the user complains about how "
    "you handled a task, update the skill that governs that task — "
    "memory alone isn't enough. Memory says 'who the user is and "
    "what the current situation and state of your operations are'; "
    "skills say 'how to do this class of task for this user'. Both "
    "should carry user-preference lessons when relevant.\n\n"
    "If you notice overlapping existing skills, mention it — the "
    "background curator handles consolidation.\n\n"
    "Do NOT capture as skills (these become persistent self-imposed "
    "constraints that bite you later when the environment changes):\n"
    "  • Environment-dependent failures: missing binaries, fresh-install "
    "errors, post-migration path mismatches, 'command not found', "
    "unconfigured credentials, uninstalled packages. The user can fix "
    "these — they are not durable rules.\n"
    "  • Negative claims about tools or features ('browser tools do not "
    "work', 'X tool is broken', 'cannot use Y from execute_code'). These "
    "harden into refusals the agent cites against itself for months "
    "after the actual problem was fixed.\n"
    "  • Session-specific transient errors that resolved before the "
    "conversation ended. If retrying worked, the lesson is the retry "
    "pattern, not the original failure.\n"
    "  • One-off task narratives. A user asking 'summarize today's "
    "market' or 'analyze this PR' is not a class of work that warrants "
    "a skill.\n\n"
    "If a tool failed because of setup state, capture the FIX (install "
    "command, config step, env var to set) under an existing setup or "
    "troubleshooting skill — never 'this tool does not work' as a "
    "standalone constraint.\n\n"
    "Act on whichever of the two dimensions has real signal. If "
    "genuinely nothing stands out on either, say 'Nothing to save.' "
    "and stop — but don't reach for that conclusion as a default."
)



def summarize_background_review_actions(
    review_messages: List[Dict],
    prior_snapshot: List[Dict],
) -> List[str]:
    """Build the human-facing action summary for a background review pass.

    Walks the review agent's session messages and collects "successful tool
    action" descriptions to surface to the user (e.g. "Memory updated").
    Tool messages already present in ``prior_snapshot`` are skipped so we
    don't re-surface stale results from the prior conversation that the
    review agent inherited via ``conversation_history`` (issue #14944).

    Matching is by ``tool_call_id`` when available, with a content-equality
    fallback for tool messages that lack one.
    """
    existing_tool_call_ids = set()
    existing_tool_contents = set()
    for prior in prior_snapshot or []:
        if not isinstance(prior, dict) or prior.get("role") != "tool":
            continue
        tcid = prior.get("tool_call_id")
        if tcid:
            existing_tool_call_ids.add(tcid)
        else:
            content = prior.get("content")
            if isinstance(content, str):
                existing_tool_contents.add(content)

    actions: List[str] = []
    for msg in review_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        tcid = msg.get("tool_call_id")
        if tcid and tcid in existing_tool_call_ids:
            continue
        if not tcid:
            content_str = msg.get("content")
            if isinstance(content_str, str) and content_str in existing_tool_contents:
                continue
        try:
            data = json.loads(msg.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or not data.get("success"):
            continue
        message = data.get("message", "")
        target = data.get("target", "")
        if "created" in message.lower():
            actions.append(message)
        elif "updated" in message.lower():
            actions.append(message)
        elif "added" in message.lower() or (target and "add" in message.lower()):
            label = "Memory" if target == "memory" else "User profile" if target == "user" else target
            actions.append(f"{label} updated")
        elif "Entry added" in message:
            label = "Memory" if target == "memory" else "User profile" if target == "user" else target
            actions.append(f"{label} updated")
        elif "removed" in message.lower() or "replaced" in message.lower():
            label = "Memory" if target == "memory" else "User profile" if target == "user" else target
            actions.append(f"{label} updated")
    return actions


def build_memory_write_metadata(
    agent: Any,
    *,
    write_origin: Optional[str] = None,
    execution_context: Optional[str] = None,
    task_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build provenance metadata for external memory-provider mirrors."""
    metadata: Dict[str, Any] = {
        "write_origin": write_origin or getattr(agent, "_memory_write_origin", "assistant_tool"),
        "execution_context": (
            execution_context
            or getattr(agent, "_memory_write_context", "foreground")
        ),
        "session_id": agent.session_id or "",
        "parent_session_id": agent._parent_session_id or "",
        "platform": agent.platform or os.environ.get("HERMES_SESSION_SOURCE", "cli"),
        "tool_name": "memory",
    }
    if task_id:
        metadata["task_id"] = task_id
    if tool_call_id:
        metadata["tool_call_id"] = tool_call_id
    return {k: v for k, v in metadata.items() if v not in {None, ""}}


def _run_review_in_thread(
    agent: Any,
    messages_snapshot: List[Dict],
    prompt: str,
    *,
    review_memory: bool = False,
    review_skills: bool = False,
) -> None:
    """Worker function executed in the background-review daemon thread.

    Spawns a forked ``AIAgent`` inheriting the parent's runtime, runs the
    review prompt, and surfaces a compact action summary back to the user
    via ``agent._safe_print`` and ``agent.background_review_callback``.
    """
    # Local import to avoid a hard circular dep at module load.
    from run_agent import AIAgent
    from tools.terminal_tool import set_approval_callback as _set_approval_callback

    # Install a non-interactive approval callback on this worker
    # thread so any dangerous-command guard the review agent trips
    # resolves to "deny" instead of falling back to input() -- which
    # deadlocks against the parent's prompt_toolkit TUI (#15216).
    # Same pattern as _subagent_auto_deny in tools/delegate_tool.py.
    def _bg_review_auto_deny(command, description, **kwargs):
        logger.warning(
            "Background review auto-denied dangerous command: %s (%s)",
            command, description,
        )
        return "deny"
    try:
        _set_approval_callback(_bg_review_auto_deny)
    except Exception:
        pass

    bg_cfg = _background_review_config()
    max_input_messages = _as_positive_int(
        bg_cfg.get("max_input_messages"),
        _DEFAULT_MAX_INPUT_MESSAGES,
    )
    max_input_chars = _as_positive_int(
        bg_cfg.get("max_input_chars"),
        _DEFAULT_MAX_INPUT_CHARS,
    )
    timeout_seconds = _as_positive_int(
        bg_cfg.get("timeout_seconds"),
        _DEFAULT_TIMEOUT_SECONDS,
    )
    review_history = _compact_review_snapshot(
        messages_snapshot,
        max_messages=max_input_messages,
        max_chars=max_input_chars,
    )
    status_path, html_path = _status_paths(bg_cfg)
    review_provider = bg_cfg.get("provider") or getattr(agent, "provider", "")
    review_model = bg_cfg.get("model") or getattr(agent, "model", "")
    runtime_status = {
        "provider": review_provider,
        "model": review_model,
        "api_mode": bg_cfg.get("api_mode") or getattr(agent, "api_mode", ""),
    }
    base_status = {
        "state": "running",
        "session_id": getattr(agent, "session_id", ""),
        "runtime": runtime_status,
        "role_plan": _build_role_plan(provider=review_provider, model=review_model),
        "review_memory": bool(review_memory),
        "review_skills": bool(review_skills),
        "snapshot_messages": len(review_history),
        "snapshot_chars": _snapshot_char_count(review_history),
        "actions": [],
        "failures": [],
        "taskpool": _build_taskpool_status([], bg_cfg),
    }
    _write_background_review_status(base_status, status_path=status_path, html_path=html_path)

    review_agent = None
    review_messages: List[Dict] = []
    try:
        with open(os.devnull, "w", encoding="utf-8") as _devnull, \
             contextlib.redirect_stdout(_devnull), \
             contextlib.redirect_stderr(_devnull):
            # Inherit the parent agent's live runtime (provider, model,
            # base_url, api_key, api_mode) so the fork uses the exact
            # same credentials the main turn is using.  Without this,
            # AIAgent.__init__ re-runs auto-resolution from env vars,
            # which fails for OAuth-only providers, session-scoped
            # creds, or credential-pool setups where the resolver can't
            # reconstruct auth from scratch -- producing the spurious
            # "No LLM provider configured" warning at end of turn.
            _parent_runtime = agent._current_main_runtime()
            _parent_api_mode = _parent_runtime.get("api_mode") or None
            # The review fork needs to call agent-loop tools (memory,
            # skill_manage). Those tools require Hermes' own dispatch,
            # which the codex_app_server runtime bypasses entirely
            # (it runs the turn inside codex's subprocess). So when
            # the parent is on codex_app_server, downgrade the review
            # fork to codex_responses — same auth/credentials, but
            # talks to the OpenAI Responses API directly so Hermes
            # owns the loop and the agent-loop tools dispatch.
            if _parent_api_mode == "codex_app_server":
                _parent_api_mode = "codex_responses"

            review_provider = bg_cfg.get("provider") or agent.provider
            review_model = bg_cfg.get("model") or agent.model
            review_api_mode = bg_cfg.get("api_mode") or _parent_api_mode or ""
            provider_changed = bool(bg_cfg.get("provider") and bg_cfg.get("provider") != agent.provider)
            review_base_url = (
                bg_cfg.get("base_url")
                or ("" if provider_changed else _parent_runtime.get("base_url"))
                or ""
            )
            review_api_key = (
                bg_cfg.get("api_key")
                or ("" if provider_changed else _parent_runtime.get("api_key"))
                or ""
            )
            runtime_status.update(
                {
                    "provider": review_provider,
                    "model": review_model,
                    "api_mode": review_api_mode,
                    "base_url_configured": bool(review_base_url),
                    "api_key_configured": bool(review_api_key),
                }
            )
            # skip_memory=True keeps the review fork from
            # touching external memory plugins (honcho, mem0,
            # supermemory, etc.).  Without it, the fork's
            # __init__ rebuilds its own _memory_manager from
            # config, scoped to the parent's session_id, and
            # run_conversation() then leaks the harness prompt
            # into the user's real memory namespace via three
            # ingestion sites: on_turn_start (cadence + turn
            # message), prefetch_all (recall query), and
            # sync_all (harness prompt + review output recorded
            # as a (user, assistant) turn pair).  Built-in
            # MEMORY.md / USER.md state is re-bound from the
            # parent below so memory(action="add") writes from
            # the review still land on disk; the review just
            # has zero side effects on external providers.
            review_agent = AIAgent(
                model=review_model,
                max_iterations=_as_positive_int(bg_cfg.get("max_iterations"), 8),
                quiet_mode=True,
                platform=agent.platform,
                provider=review_provider,
                api_mode=review_api_mode,
                base_url=review_base_url,
                api_key=review_api_key,
                credential_pool=getattr(agent, "_credential_pool", None),
                parent_session_id=agent.session_id,
                skip_memory=True,
            )
            review_agent._memory_write_origin = "background_review"
            review_agent._memory_write_context = "background_review"
            review_agent._memory_store = agent._memory_store
            review_agent._memory_enabled = agent._memory_enabled
            review_agent._user_profile_enabled = agent._user_profile_enabled
            review_agent._memory_nudge_interval = 0
            review_agent._skill_nudge_interval = 0
            # Suppress all status/warning emits from the fork so the
            # user only sees the final successful-action summary.
            # Without this, mid-review "Iteration budget exhausted",
            # rate-limit retries, compression warnings, and other
            # lifecycle messages bubble up through _emit_status ->
            # _vprint and leak past the stdout redirect (they go via
            # _print_fn/status_callback, which bypass sys.stdout).
            review_agent.suppress_status_output = True
            # Inherit the parent's cached system prompt verbatim so
            # the review fork's outbound HTTP request hits the same
            # Anthropic/OpenRouter prefix cache the parent warmed.
            # Without this, the fork rebuilds the system prompt from
            # scratch (fresh _hermes_now() timestamp, fresh
            # session_id, narrower toolset → different skills_prompt)
            # and the byte-exact prefix-cache key misses. See
            # issue #25322 and PR #17276 for the full analysis +
            # measured impact (~26% end-to-end cost reduction on
            # Sonnet 4.5).
            review_agent._cached_system_prompt = agent._cached_system_prompt
            # Defensive: pin session_start + session_id to the
            # parent's so any code path that re-renders parts of
            # the system prompt (compression, plugin hooks) still
            # produces byte-identical output. The cached-prompt
            # assignment above already short-circuits the normal
            # rebuild path, but these pins guarantee parity even
            # if a future code path bypasses the cache.
            review_agent.session_start = agent.session_start
            review_agent.session_id = agent.session_id

            from model_tools import get_tool_definitions
            from hermes_cli.plugins import (
                set_thread_tool_whitelist,
                clear_thread_tool_whitelist,
            )

            review_whitelist = {
                t["function"]["name"]
                for t in get_tool_definitions(
                    enabled_toolsets=["memory", "skills"],
                    quiet_mode=True,
                )
            }
            set_thread_tool_whitelist(
                review_whitelist,
                deny_msg_fmt=(
                    "Background review denied non-whitelisted tool: "
                    "{tool_name}. Only memory/skill tools are allowed."
                ),
            )
            try:
                completed = _run_conversation_with_timeout(
                    review_agent,
                    timeout_seconds=timeout_seconds,
                    approval_callback=_bg_review_auto_deny,
                    user_message=(
                        _augment_prompt_for_taskpool(prompt, bg_cfg)
                        + "\n\nYou can only call memory and skill "
                        "management tools. Other tools will be denied "
                        "at runtime — do not attempt them."
                    ),
                    conversation_history=review_history,
                )
                if not completed:
                    msg = (
                        "⚠️ Self-improvement review failed: timed out "
                        f"after {timeout_seconds}s"
                    )
                    logger.warning("Background memory/skill review timed out after %ss", timeout_seconds)
                    timeout_status = dict(base_status)
                    timeout_status.update(
                        {
                            "state": "timeout",
                            "runtime": runtime_status,
                            "role_plan": _build_role_plan(provider=review_provider, model=review_model),
                            "failures": [f"timed out after {timeout_seconds}s"],
                        }
                    )
                    _write_background_review_status(
                        timeout_status,
                        status_path=status_path,
                        html_path=html_path,
                    )
                    _emit_background_review_status(agent, msg)
                    return
            finally:
                clear_thread_tool_whitelist()

            # Tear down memory providers while stdout is still
            # redirected so background thread teardown (Honcho flush,
            # Hindsight sync, etc.) stays silent.  The finally block
            # below is a safety net for the exception path.
            try:
                review_agent.shutdown_memory_provider()
            except Exception:
                pass
            try:
                review_agent.close()
            except Exception:
                pass
            review_messages = list(getattr(review_agent, "_session_messages", []))
            review_agent = None

        # Scan the review agent's messages for successful tool actions
        # and surface a compact summary to the user. Tool messages
        # already present in messages_snapshot must be skipped, since
        # the review agent inherits that history and would otherwise
        # re-surface stale "created"/"updated" messages from the prior
        # conversation as if they just happened (issue #14944).
        actions = summarize_background_review_actions(
            review_messages,
            messages_snapshot,
        )
        taskpool_status = _build_taskpool_status(review_messages, bg_cfg)

        status_update = dict(base_status)
        status_update.update(
            {
                "runtime": runtime_status,
                "role_plan": _build_role_plan(provider=review_provider, model=review_model),
                "taskpool": taskpool_status,
            }
        )
        if actions:
            unique_actions = list(dict.fromkeys(actions))
            status_update.update({"state": "completed", "actions": unique_actions, "failures": []})
            _write_background_review_status(
                status_update,
                status_path=status_path,
                html_path=html_path,
            )
            summary = " · ".join(unique_actions)
            _emit_background_review_status(
                agent,
                f"💾 Self-improvement review: {summary}",
            )
        else:
            failures = _summarize_background_review_failures(review_messages, messages_snapshot)
            if failures:
                status_update.update({"state": "failed", "actions": [], "failures": failures})
                _write_background_review_status(
                    status_update,
                    status_path=status_path,
                    html_path=html_path,
                )
                summary = " · ".join(failures)
                _emit_background_review_status(
                    agent,
                    f"⚠️ Self-improvement review failed: {summary}",
                )
            else:
                status_update.update({"state": "completed", "actions": [], "failures": []})
                _write_background_review_status(
                    status_update,
                    status_path=status_path,
                    html_path=html_path,
                )

    except Exception as e:
        logger.warning("Background memory/skill review failed: %s", e)
        try:
            error_status = dict(base_status)
            error_status.update(
                {
                    "state": "failed",
                    "runtime": runtime_status,
                    "role_plan": _build_role_plan(provider=review_provider, model=review_model),
                    "actions": [],
                    "failures": [str(e)],
                }
            )
            _write_background_review_status(
                error_status,
                status_path=status_path,
                html_path=html_path,
            )
        except Exception:
            pass
        _emit_background_review_status(
            agent,
            f"⚠️ Self-improvement review failed: {e}",
        )
        agent._emit_auxiliary_failure("background review", e)
    finally:
        # Safety-net cleanup for the exception path.  Normal
        # completion already shut down inside redirect_stdout above.
        # Re-open devnull here so any teardown output (Honcho flush,
        # Hindsight sync, background thread joins) stays silent even
        # on the exception path where redirect_stdout already exited.
        if review_agent is not None:
            try:
                with open(os.devnull, "w", encoding="utf-8") as _fn, \
                     contextlib.redirect_stdout(_fn), \
                     contextlib.redirect_stderr(_fn):
                    try:
                        review_agent.shutdown_memory_provider()
                    except Exception:
                        pass
                    try:
                        review_agent.close()
                    except Exception:
                        pass
            except Exception:
                pass
        # Clear the approval callback on this bg-review thread so a
        # recycled thread-id doesn't inherit a stale reference.
        try:
            _set_approval_callback(None)
        except Exception:
            pass


def spawn_background_review_thread(
    agent: Any,
    messages_snapshot: List[Dict],
    review_memory: bool = False,
    review_skills: bool = False,
):
    """Build the review thread target and prompt for a background review.

    Returns a ``(target, prompt)`` tuple.  The caller (``AIAgent._spawn_background_review``)
    owns the actual ``threading.Thread`` construction so test-level patches
    of ``run_agent.threading.Thread`` keep working.
    """
    # Pick the right prompt based on which triggers fired.  Allow per-agent
    # override (the prompts moved to module-level constants but old code paths
    # that set agent._MEMORY_REVIEW_PROMPT etc. directly keep working).
    if review_memory and review_skills:
        prompt = getattr(agent, "_COMBINED_REVIEW_PROMPT", _COMBINED_REVIEW_PROMPT)
    elif review_memory:
        prompt = getattr(agent, "_MEMORY_REVIEW_PROMPT", _MEMORY_REVIEW_PROMPT)
    else:
        prompt = getattr(agent, "_SKILL_REVIEW_PROMPT", _SKILL_REVIEW_PROMPT)

    def _target() -> None:
        _run_review_in_thread(
            agent,
            messages_snapshot,
            prompt,
            review_memory=review_memory,
            review_skills=review_skills,
        )

    return _target, prompt


__all__ = [
    "_MEMORY_REVIEW_PROMPT",
    "_SKILL_REVIEW_PROMPT",
    "_COMBINED_REVIEW_PROMPT",
    "spawn_background_review_thread",
    "summarize_background_review_actions",
    "build_memory_write_metadata",
    "get_background_review_status",
    "render_background_review_status_html",
]
