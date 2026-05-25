import json
from pathlib import Path

import pytest

from tools import browser_tool


def test_attach_browser_replay_redacts_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_tool, "get_hermes_home", lambda: tmp_path)

    response = {
        "success": False,
        "error": "Blocked: URL contains token sk-test-1234567890 and failed",
    }
    enriched = browser_tool._attach_browser_replay(
        response,
        session_key="task-secret",
        command="open",
        action_args=["https://example.com/?token=sk-test-1234567890"],
        url="https://example.com/?token=sk-test-1234567890",
        snapshot_text="RootWebArea Secret sk-test-1234567890",
    )

    bundle_path = Path(enriched["replay_bundle_path"])
    events_path = Path(enriched["replay_events_path"])
    assert bundle_path.exists()
    assert events_path.exists()

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["schema"] == "browser_replay_bundle_v1"
    assert bundle["latest_failure_class"] == "policy_blocked"
    serialized = json.dumps(bundle, ensure_ascii=False)
    assert "sk-test-1234567890" not in serialized
    assert "https://example.com/?token=" in bundle["current_url"]
    assert bundle["last_event"]["dom_snapshot_excerpt"]


def test_browser_navigate_writes_replay_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_tool, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(browser_tool, "_get_session_info", lambda session_key: {"_first_nav": False})

    def fake_run(session_key, command, args, timeout=None, _engine_override=None):
        if command == "open":
            return {"success": True, "data": {"url": "https://example.com/final", "title": "Example Title"}}
        if command == "snapshot":
            return {
                "success": True,
                "data": {
                    "snapshot": "RootWebArea\n  heading Example\n  button Continue",
                    "refs": {"@e1": "button Continue"},
                },
            }
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(browser_tool, "_run_browser_command", fake_run)

    result = json.loads(browser_tool.browser_navigate("https://example.com", task_id="task-nav"))

    assert result["success"] is True
    assert result["replay_failure_class"] == "success"
    bundle = json.loads(Path(result["replay_bundle_path"]).read_text(encoding="utf-8"))
    assert bundle["current_url"] == "https://example.com/final"
    assert bundle["title"] == "Example Title"
    assert "button Continue" in bundle["latest_dom_snapshot_excerpt"]
    assert bundle["action_sequence"][-1]["command"] == "open"


def test_browser_console_records_js_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_tool, "get_hermes_home", lambda: tmp_path)

    def fake_run(session_key, command, args, timeout=None, _engine_override=None):
        if command == "console":
            return {"success": True, "data": {"messages": [{"type": "warn", "text": "warn message"}]}}
        if command == "errors":
            return {"success": True, "data": {"errors": [{"message": "ReferenceError: boom"}]}}
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(browser_tool, "_run_browser_command", fake_run)
    monkeypatch.setattr(browser_tool, "_last_session_key", lambda task_id: task_id)

    result = json.loads(browser_tool.browser_console(task_id="task-console"))

    assert result["success"] is True
    assert result["replay_failure_class"] == "success_with_js_errors"
    bundle = json.loads(Path(result["replay_bundle_path"]).read_text(encoding="utf-8"))
    assert bundle["latest_console_errors"][0]["text"] == "warn message"
    assert bundle["latest_console_errors"][1]["text"] == "ReferenceError: boom"


def _assert_replay_fields(result):
    assert result["replay_failure_class"] == "success"
    bundle = json.loads(Path(result["replay_bundle_path"]).read_text(encoding="utf-8"))
    assert bundle["schema"] == "browser_replay_bundle_v1"
    assert Path(result["replay_events_path"]).exists()
    return bundle


def test_browser_get_images_writes_replay_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_tool, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(browser_tool, "_last_session_key", lambda task_id: task_id)

    def fake_run(session_key, command, args, timeout=None, _engine_override=None):
        assert command == "eval"
        return {
            "success": True,
            "data": {
                "result": json.dumps([
                    {
                        "src": "https://img.test/a.png",
                        "alt": "hero",
                        "width": 120,
                        "height": 40,
                    }
                ])
            },
        }

    monkeypatch.setattr(browser_tool, "_run_browser_command", fake_run)

    result = json.loads(browser_tool.browser_get_images(task_id="task-images"))

    assert result["count"] == 1
    bundle = _assert_replay_fields(result)
    assert bundle["action_sequence"][-1]["command"] == "images"
    assert bundle["last_event"]["action_sequence"][0]["args"] == []


def test_browser_console_expression_writes_replay_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_tool, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(browser_tool, "_last_session_key", lambda task_id: task_id)
    monkeypatch.setattr(
        browser_tool,
        "_run_browser_command",
        lambda session_key, command, args, timeout=None, _engine_override=None: {
            "success": True,
            "data": {"result": "42"},
        },
    )

    result = json.loads(browser_tool.browser_console(expression="21+21", task_id="task-eval"))

    assert result["result"] == 42
    bundle = _assert_replay_fields(result)
    assert bundle["action_sequence"][-1]["command"] == "eval"
    assert bundle["last_event"]["action_sequence"][0]["args"] == ["21+21"]


def test_browser_console_expression_camofox_writes_replay_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_tool, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
    monkeypatch.setattr(browser_tool, "_last_session_key", lambda task_id: task_id)
    monkeypatch.setattr(browser_tool, "_camofox_eval", lambda expression, task_id=None: json.dumps({
        "success": True,
        "result": 42,
        "result_type": "int",
    }))

    result = json.loads(browser_tool.browser_console(expression="21+21", task_id="task-camo-eval"))

    assert result["result"] == 42
    bundle = _assert_replay_fields(result)
    assert bundle["action_sequence"][-1]["command"] == "eval"
    assert bundle["last_event"]["action_sequence"][0]["args"] == ["21+21"]


@pytest.mark.parametrize(
    ("name", "runner", "expected_command", "expected_url", "expected_snapshot", "expected_screenshot"),
    [
        (
            "navigate",
            lambda: json.loads(browser_tool.browser_navigate("https://example.com", task_id="camo-nav")),
            "open",
            "https://example.com",
            "RootWebArea\n  button Continue",
            None,
        ),
        (
            "snapshot",
            lambda: json.loads(browser_tool.browser_snapshot(task_id="camo-snapshot")),
            "snapshot",
            None,
            "RootWebArea\n  button Continue",
            None,
        ),
        (
            "click",
            lambda: json.loads(browser_tool.browser_click("@e1", task_id="camo-click")),
            "click",
            "https://example.com/after-click",
            None,
            None,
        ),
        (
            "type",
            lambda: json.loads(browser_tool.browser_type("@e2", "secret value", task_id="camo-type")),
            "fill",
            None,
            None,
            None,
        ),
        (
            "scroll",
            lambda: json.loads(browser_tool.browser_scroll("down", task_id="camo-scroll")),
            "scroll",
            None,
            None,
            None,
        ),
        (
            "back",
            lambda: json.loads(browser_tool.browser_back(task_id="camo-back")),
            "back",
            "https://example.com/back",
            None,
            None,
        ),
        (
            "press",
            lambda: json.loads(browser_tool.browser_press("Enter", task_id="camo-press")),
            "press",
            None,
            None,
            None,
        ),
        (
            "console",
            lambda: json.loads(browser_tool.browser_console(task_id="camo-console")),
            "console",
            None,
            None,
            None,
        ),
        (
            "images",
            lambda: json.loads(browser_tool.browser_get_images(task_id="camo-images")),
            "images",
            None,
            None,
            None,
        ),
        (
            "vision",
            lambda: json.loads(browser_tool.browser_vision("Summarize", task_id="camo-vision")),
            "vision",
            None,
            None,
            "/tmp/camo-shot.png",
        ),
    ],
)
def test_camofox_browser_paths_write_replay_bundle(
    tmp_path,
    monkeypatch,
    name,
    runner,
    expected_command,
    expected_url,
    expected_snapshot,
    expected_screenshot,
):
    monkeypatch.setattr(browser_tool, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: True)
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
    monkeypatch.setattr(browser_tool, "_last_session_key", lambda task_id: task_id)

    monkeypatch.setattr(
        "tools.browser_camofox.camofox_navigate",
        lambda url, task_id=None: json.dumps({
            "success": True,
            "url": url,
            "title": "Example",
            "snapshot": "RootWebArea\n  button Continue",
        }),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_snapshot",
        lambda full=False, task_id=None, user_task=None: json.dumps({
            "success": True,
            "snapshot": "RootWebArea\n  button Continue",
            "element_count": 1,
        }),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_click",
        lambda ref, task_id=None: json.dumps({
            "success": True,
            "clicked": ref.lstrip("@"),
            "url": "https://example.com/after-click",
        }),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_type",
        lambda ref, text, task_id=None: json.dumps({
            "success": True,
            "typed": text,
            "element": ref.lstrip("@"),
        }),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_scroll",
        lambda direction, task_id=None: json.dumps({"success": True, "scrolled": direction}),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_back",
        lambda task_id=None: json.dumps({"success": True, "url": "https://example.com/back"}),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_press",
        lambda key, task_id=None: json.dumps({"success": True, "pressed": key}),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_console",
        lambda clear=False, task_id=None: json.dumps({
            "success": True,
            "console_messages": [],
            "js_errors": [],
            "total_messages": 0,
            "total_errors": 0,
        }),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_get_images",
        lambda task_id=None: json.dumps({
            "success": True,
            "images": [{"src": "https://img.test/a.png", "alt": "hero"}],
            "count": 1,
        }),
    )
    monkeypatch.setattr(
        "tools.browser_camofox.camofox_vision",
        lambda question, annotate=False, task_id=None: json.dumps({
            "success": True,
            "analysis": "Looks good",
            "screenshot_path": "/tmp/camo-shot.png",
        }),
    )

    result = runner()

    bundle = _assert_replay_fields(result)
    assert bundle["action_sequence"][-1]["command"] == expected_command
    assert bundle["current_url"] == expected_url
    if expected_snapshot is None:
        assert bundle["latest_dom_snapshot_excerpt"] == expected_snapshot
    else:
        assert "RootWebArea" in bundle["latest_dom_snapshot_excerpt"]
        assert "button Continue" in bundle["latest_dom_snapshot_excerpt"]
    assert bundle["latest_screenshot_path"] == expected_screenshot
