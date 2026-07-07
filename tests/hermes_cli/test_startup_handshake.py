from __future__ import annotations

import json
from types import SimpleNamespace


def test_startup_handshake_collects_read_only_projection_and_writes_selfcheck_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import startup_handshake as mod

    monkeypatch.setattr(mod, "load_config", lambda: {"model": {"default": "gpt-test", "provider": "openai-codex"}})
    monkeypatch.setattr(mod, "resolve_requested_provider", lambda requested=None: "openai-codex")
    monkeypatch.setattr(mod, "resolve_provider", lambda requested=None, **kwargs: "openai-codex")
    monkeypatch.setattr(mod, "provider_label", lambda provider: "OpenAI Codex")
    monkeypatch.setattr(mod, "read_health_summary", lambda limit=20: {"status": "ok", "record_count": 0, "degraded_count": 0})

    receipt = mod.collect_startup_handshake(write_receipt=True, observed_at=123.0)

    assert receipt["schema"] == "hermes_startup_handshake_v1"
    assert receipt["side_effects"] == {"external": False, "llm": False, "writes": ["selfcheck_receipt", "health_record"]}
    assert receipt["status"] == "ok"
    assert receipt["summary"]["model"] == "gpt-test"
    assert receipt["summary"]["provider"] == "OpenAI Codex"
    assert any(p["id"] == "provider_model" and p["status"] == "ok" for p in receipt["probes"])

    receipt_path = tmp_path / "state" / "selfcheck" / "startup_handshake_last.json"
    assert receipt_path.exists()
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted["schema"] == "hermes_startup_handshake_v1"


def test_startup_handshake_warns_for_degraded_dynamic_selfcheck_without_action(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import startup_handshake as mod

    monkeypatch.setattr(mod, "load_config", lambda: {"model": "gpt-test"})
    monkeypatch.setattr(mod, "resolve_requested_provider", lambda requested=None: "auto")
    monkeypatch.setattr(mod, "resolve_provider", lambda requested=None, **kwargs: "openrouter")
    monkeypatch.setattr(mod, "provider_label", lambda provider: "OpenRouter")
    monkeypatch.setattr(
        mod,
        "read_health_summary",
        lambda limit=20: {"status": "degraded", "record_count": 2, "degraded_count": 1},
    )

    receipt = mod.collect_startup_handshake(write_receipt=False, observed_at=123.0)

    assert receipt["status"] == "warn"
    dynamic_probe = next(p for p in receipt["probes"] if p["id"] == "dynamic_selfcheck")
    assert dynamic_probe["status"] == "warn"
    assert dynamic_probe["suggested_command"] == "hermes selfcheck health --json"
    assert dynamic_probe["action_taken"] is False


def test_cmd_selfcheck_startup_dispatches_startup_handshake(monkeypatch):
    import hermes_cli.main as main_mod

    called = {}

    def fake_render(args):
        called["args"] = args
        return 0

    monkeypatch.setattr("hermes_cli.startup_handshake.render_startup_handshake", fake_render)

    rc = main_mod.cmd_selfcheck(SimpleNamespace(selfcheck_command="startup", json=True, no_write=True))

    assert rc == 0
    assert called["args"].selfcheck_command == "startup"


def test_render_startup_handshake_json_and_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import startup_handshake as mod

    monkeypatch.setattr(mod, "collect_startup_handshake", lambda **kwargs: {
        "schema": "hermes_startup_handshake_v1",
        "status": "warn",
        "summary": {"profile": "default", "model": "gpt-test", "provider": "OpenAI Codex"},
        "probes": [
            {"id": "provider_model", "status": "ok", "message": "gpt-test via OpenAI Codex", "action_taken": False},
            {"id": "dynamic_selfcheck", "status": "warn", "message": "1 degraded health record", "suggested_command": "hermes selfcheck health --json", "action_taken": False},
        ],
        "side_effects": {"external": False, "llm": False, "writes": []},
    })

    rc = mod.render_startup_handshake(SimpleNamespace(json=False, no_write=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Hermes startup handshake: warn" in out
    assert "dynamic_selfcheck: warn" in out
    assert "hermes selfcheck health --json" in out

    rc = mod.render_startup_handshake(SimpleNamespace(json=True, no_write=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["schema"] == "hermes_startup_handshake_v1"


def test_reminder_policy_tracks_severity_intervals_per_entrypoint(tmp_path):
    from hermes_cli import startup_handshake as mod

    receipt = {
        "summary": {"profile": "default"},
        "probes": [
            {"id": "blocked_probe", "status": "blocked", "severity": "blocked", "message": "blocked", "action_taken": False},
            {"id": "warn_probe", "status": "warn", "severity": "warn", "message": "warn", "action_taken": False},
        ],
    }

    first = mod.apply_reminder_policy(receipt, entrypoint="chat.cli", update_state=True, observed_at=1000.0, home=tmp_path)
    assert all(p["reminder"]["should_remind"] for p in first["probes"])

    second = mod.apply_reminder_policy(first, entrypoint="chat.cli", update_state=False, observed_at=1001.0, home=tmp_path)
    blocked = next(p for p in second["probes"] if p["id"] == "blocked_probe")
    warn = next(p for p in second["probes"] if p["id"] == "warn_probe")
    assert blocked["reminder"]["interval_seconds"] == 0
    assert blocked["reminder"]["should_remind"] is True
    assert warn["reminder"]["interval_seconds"] == 86400
    assert warn["reminder"]["should_remind"] is False


def test_main_startup_handshake_entrypoint_covers_command_and_chat(monkeypatch):
    from types import SimpleNamespace
    import hermes_cli.main as main_mod

    assert main_mod._startup_handshake_entrypoint(SimpleNamespace(command="status", oneshot=None)) == "status"
    assert main_mod._startup_handshake_entrypoint(SimpleNamespace(command="selfcheck", selfcheck_command="health", oneshot=None)) == "selfcheck.health"
    assert main_mod._startup_handshake_entrypoint(SimpleNamespace(command=None, oneshot="hello")) == "oneshot"

    captured = {}

    def fake_trigger(entrypoint, **kwargs):
        captured["entrypoint"] = entrypoint
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr("hermes_cli.startup_handshake.trigger_startup_handshake_for_entrypoint", fake_trigger)
    main_mod._trigger_startup_handshake(SimpleNamespace(command="status", oneshot=None), emit_reminder=True)
    assert captured["entrypoint"] == "status"
    assert captured["emit_reminder"] is True
    assert captured["write_receipt"] is True
