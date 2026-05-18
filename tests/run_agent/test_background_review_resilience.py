"""Regression tests for background review resilience and visibility."""

from __future__ import annotations

import json

import run_agent as run_agent_module
from run_agent import AIAgent


class ImmediateThread:
    def __init__(self, *, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


def _bare_agent() -> AIAgent:
    agent = object.__new__(AIAgent)
    agent.model = "parent-model"
    agent.platform = "cli"
    agent.provider = "parent-provider"
    agent.base_url = "https://parent.example/v1"
    agent.api_key = "parent-key"
    agent.api_mode = ""
    agent.session_id = "parent-session"
    agent._parent_session_id = ""
    agent._credential_pool = None
    agent._memory_store = object()
    agent._memory_enabled = True
    agent._user_profile_enabled = True
    agent._cached_system_prompt = "parent-system-prompt"
    agent._MEMORY_REVIEW_PROMPT = "review memory"
    agent._SKILL_REVIEW_PROMPT = "review skills"
    agent._COMBINED_REVIEW_PROMPT = "review both"
    agent.background_review_callback = None
    agent.status_callback = None
    agent._safe_print = lambda *_args, **_kwargs: None
    import datetime as _dt
    agent.session_start = _dt.datetime(2026, 1, 1, 12, 0, 0)
    return agent


def test_background_review_reports_tool_failure_to_callback(monkeypatch):
    """A failed review must not look like 'nothing happened' to the user."""
    captured: list[str] = []

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = [
                {
                    "role": "tool",
                    "tool_call_id": "bad-call",
                    "content": '{"success": false, "error": "Missing required parameter: file_content"}',
                }
            ]

        def run_conversation(self, **kwargs):
            pass

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)

    agent = _bare_agent()
    agent.background_review_callback = captured.append

    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_skills=True,
    )

    assert captured, "background review failure was silent"
    assert "failed" in captured[0].lower() or "未完成" in captured[0]
    assert "file_content" in captured[0]


def test_background_review_compacts_large_snapshot_before_review(monkeypatch):
    """The review fork should receive a bounded recent snapshot, not the whole session."""
    captured = {}

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = []

        def run_conversation(self, **kwargs):
            captured["history"] = kwargs["conversation_history"]

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)

    agent = _bare_agent()
    huge_history = [
        {"role": "user" if idx % 2 == 0 else "assistant", "content": f"message-{idx}-" + ("x" * 2000)}
        for idx in range(80)
    ]

    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=huge_history,
        review_memory=True,
        review_skills=True,
    )

    history = captured.get("history")
    assert history is not None
    assert len(history) < len(huge_history)
    assert history[-1]["content"].startswith("message-79-")
    assert sum(len(str(m.get("content", ""))) for m in history) <= 65_000


def test_background_review_timeout_is_reported(monkeypatch):
    """A timed-out review gets a visible status instead of failing silently."""
    captured: list[str] = []

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = []

        def run_conversation(self, **kwargs):
            raise AssertionError("fake timeout thread should not execute target")

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    class NeverFinishesThread:
        def __init__(self, *, target=None, daemon=None, name=None):
            self._target = target

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr("agent.background_review._Thread", NeverFinishesThread)
    monkeypatch.setattr(
        "agent.background_review.load_config",
        lambda: {"background_review": {"timeout_seconds": 1}},
    )

    agent = _bare_agent()
    agent.background_review_callback = captured.append

    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_memory=True,
    )

    assert captured
    assert "timed out" in captured[0]


def test_background_review_can_use_dedicated_runtime_from_config(monkeypatch):
    """Config can route bg-review to a cheaper/faster model without changing the parent."""
    captured_kwargs = {}

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            self._session_messages = []

        def run_conversation(self, **kwargs):
            pass

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        "agent.background_review.load_config",
        lambda: {
            "background_review": {
                "provider": "xiaomi",
                "model": "mimo-v2.5-pro",
                "api_mode": "openai_compatible",
                "base_url": "https://bg.example/v1",
                "timeout_seconds": 30,
                "max_input_messages": 12,
                "max_input_chars": 12000,
            }
        },
    )

    agent = _bare_agent()

    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_memory=True,
    )

    assert captured_kwargs["provider"] == "xiaomi"
    assert captured_kwargs["model"] == "mimo-v2.5-pro"
    assert captured_kwargs["api_mode"] == "openai_compatible"
    assert captured_kwargs["base_url"] == "https://bg.example/v1"


def test_background_review_writes_status_registry_and_html_report(monkeypatch, tmp_path):
    """Every bg-review run should leave machine-readable status + human HTML."""
    from agent.background_review import get_background_review_status

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = [
                {
                    "role": "tool",
                    "tool_call_id": "call_bg",
                    "content": json.dumps(
                        {"success": True, "message": "Entry added", "target": "memory"}
                    ),
                }
            ]

        def run_conversation(self, **kwargs):
            pass

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    registry_path = tmp_path / "bg-review-status.json"
    html_path = tmp_path / "bg-review-status.html"
    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        "agent.background_review.load_config",
        lambda: {
            "background_review": {
                "provider": "xiaomi",
                "model": "mimo-v2.5-pro",
                "status_path": str(registry_path),
                "html_report_path": str(html_path),
            }
        },
    )

    agent = _bare_agent()
    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "remember this"}],
        review_memory=True,
        review_skills=True,
    )

    status = get_background_review_status(path=registry_path)
    assert status["state"] == "completed"
    assert status["session_id"] == "parent-session"
    assert status["runtime"]["provider"] == "xiaomi"
    assert status["runtime"]["model"] == "mimo-v2.5-pro"
    assert status["role_plan"]["triager"] == "mimo-v2.5-pro"
    assert status["role_plan"]["truth_gate"] == "tool_result_success"
    assert status["actions"] == ["Memory updated"]
    assert status["html_report_path"] == str(html_path)

    html = html_path.read_text(encoding="utf-8")
    assert "Self-improvement review" in html
    assert "mimo-v2.5-pro" in html
    assert "Memory updated" in html
    assert "Conductor roles" in html


def test_background_review_status_registry_records_failure(monkeypatch, tmp_path):
    """Failure records should preserve the reason for later CLI/HTML inspection."""
    from agent.background_review import get_background_review_status

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = [
                {
                    "role": "tool",
                    "tool_call_id": "bad-call",
                    "content": json.dumps(
                        {"success": False, "error": "Missing required parameter: file_content"}
                    ),
                }
            ]

        def run_conversation(self, **kwargs):
            pass

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    registry_path = tmp_path / "bg-review-status.json"
    html_path = tmp_path / "bg-review-status.html"
    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        "agent.background_review.load_config",
        lambda: {
            "background_review": {
                "status_path": str(registry_path),
                "html_report_path": str(html_path),
            }
        },
    )

    agent = _bare_agent()
    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_skills=True,
    )

    status = get_background_review_status(path=registry_path)
    assert status["state"] == "failed"
    assert "file_content" in status["failures"][0]
    assert "file_content" in html_path.read_text(encoding="utf-8")


def test_background_review_suggests_taskpool_candidates_without_enqueue(monkeypatch, tmp_path):
    """Taskpool integration defaults to safe suggestion mode; it must not mutate the pool."""
    from agent.background_review import get_background_review_status

    calls = []

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = [
                {
                    "role": "assistant",
                    "content": (
                        "BACKGROUND_REVIEW_TASKS_JSON:\n"
                        + json.dumps(
                            {
                                "tasks": [
                                    {
                                        "title": "Harden bg-review taskpool routing",
                                        "objective": "Design taskpool routing for bg-review findings",
                                        "priority": "P2",
                                        "lane": "evolution",
                                        "requires_multimodel": True,
                                    }
                                ]
                            }
                        )
                    ),
                }
            ]

        def run_conversation(self, **kwargs):
            pass

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr("agent.background_review.subprocess.run", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(
        "agent.background_review.load_config",
        lambda: {
            "background_review": {
                "taskpool": {"enabled": True, "mode": "suggest"},
                "status_path": str(tmp_path / "status.json"),
                "html_report_path": str(tmp_path / "status.html"),
            }
        },
    )

    agent = _bare_agent()
    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "全盘推进"}],
        review_skills=True,
    )

    assert calls == []
    status = get_background_review_status(tmp_path / "status.json")
    assert status["taskpool"]["mode"] == "suggest"
    assert status["taskpool"]["candidates"][0]["title"] == "Harden bg-review taskpool routing"
    assert status["taskpool"]["candidates"][0]["priority_layer"] == "P2"
    assert status["taskpool"]["candidates"][0]["multimodel_triage_result"] == "P2"
    assert status["taskpool"]["candidates"][0]["truth_gate"] == "evidence_bundle"
    assert "Taskpool" in (tmp_path / "status.html").read_text(encoding="utf-8")


def test_background_review_can_enqueue_taskpool_candidates_with_smart_contract(monkeypatch, tmp_path):
    """Explicit enqueue mode should call brain_task_orchestrator add with smart-routing metadata."""
    from agent.background_review import get_background_review_status

    calls = []

    class Result:
        returncode = 0
        stdout = json.dumps({"created": True, "id": "task-bg-1"})
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = [
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "background_review_tasks": [
                                {
                                    "title": "Split bg-review findings into taskpool tasks",
                                    "objective": "Route complex self-improvement findings into local-brain taskpool",
                                    "priority": "P1",
                                    "lane": "evolution",
                                    "architecture_target": "local-brain",
                                    "capability_delta": "bg-review can hand off complex findings as governed tasks",
                                    "anti_drift_check": "taskpool JSON status and add result recorded",
                                    "acceptance_evidence": "pytest bg-review taskpool enqueue tests pass",
                                    "requires_multimodel": True,
                                }
                            ]
                        }
                    ),
                }
            ]

        def run_conversation(self, **kwargs):
            pass

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr("agent.background_review.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent.background_review.load_config",
        lambda: {
            "background_review": {
                "taskpool": {
                    "enabled": True,
                    "mode": "enqueue",
                    "orchestrator_path": "/tmp/brain_task_orchestrator.py",
                },
                "status_path": str(tmp_path / "status.json"),
                "html_report_path": str(tmp_path / "status.html"),
            }
        },
    )

    agent = _bare_agent()
    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "全盘推进"}],
        review_skills=True,
    )

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:3] == ["python3", "/tmp/brain_task_orchestrator.py", "add"]
    assert "Split bg-review findings into taskpool tasks" in cmd
    assert "--priority-layer" in cmd and cmd[cmd.index("--priority-layer") + 1] == "P1"
    assert "--multimodel-triage-result" in cmd and cmd[cmd.index("--multimodel-triage-result") + 1] == "P1"
    assert "--requires-multimodel" in cmd
    assert "--truth-gate" in cmd and cmd[cmd.index("--truth-gate") + 1] == "evidence_bundle"

    status = get_background_review_status(tmp_path / "status.json")
    assert status["taskpool"]["mode"] == "enqueue"
    assert status["taskpool"]["enqueued"][0]["id"] == "task-bg-1"
    assert status["taskpool"]["enqueued"][0]["created"] is True
