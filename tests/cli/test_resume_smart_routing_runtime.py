"""Durable smart-routing runtime restoration across CLI process resume."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cli import HermesCLI


SESSION_ID = "20260812_010820_449e44"


def _session_meta(*, route_session_id=SESSION_ID):
    return {
        "id": SESSION_ID,
        "model": "gpt-5.5",
        "model_config": json.dumps(
            {
                "max_iterations": 60,
                "smart_routing_runtime": {
                    "session_id": route_session_id,
                    "provider": "openai-codex",
                    "model": "gpt-5.5",
                    "base_url": "",
                    "api_mode": "responses",
                    "tier": "SIMPLE",
                },
            }
        ),
    }


def _resume_cli(db, *, explicit_pin=False):
    shell = HermesCLI.__new__(HermesCLI)
    shell.agent = None
    shell.session_id = SESSION_ID
    shell.session_start = datetime.now()
    shell._resumed = True
    setattr(shell, "_resume_runtime_pin_explicit", explicit_pin)
    shell._session_db = db
    shell.conversation_history = [{"role": "user", "content": "existing"}]
    shell.tool_progress_mode = "off"
    shell.model = "gpt-5.6-sol" if not explicit_pin else "pinned-model"
    shell.provider = "openai-codex" if not explicit_pin else "pinned-provider"
    shell.requested_provider = shell.provider
    shell.base_url = "https://default.invalid" if not explicit_pin else "https://pin.invalid"
    shell.api_mode = "chat_completions"
    shell.acp_command = None
    shell.acp_args = []
    shell._install_tool_callbacks = lambda: None
    shell._ensure_tirith_security = lambda: None
    return shell


def test_resume_restores_durable_smart_route_before_credentials_are_resolved():
    db = MagicMock()
    db.get_session.return_value = _session_meta()
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db)

    def assert_restored_before_credentials():
        assert shell.model == "gpt-5.5"
        assert shell.provider == "openai-codex"
        assert shell.requested_provider == "openai-codex"
        assert shell.base_url is None
        assert shell.api_mode == "responses"
        assert getattr(shell, "_resumed_smart_routing_runtime")["tier"] == "SIMPLE"
        return False

    shell._ensure_runtime_credentials = assert_restored_before_credentials

    with patch("cli._prepare_deferred_agent_startup"):
        assert shell._init_agent() is False


def test_resume_explicit_runtime_pin_wins_over_durable_smart_route():
    db = MagicMock()
    db.get_session.return_value = _session_meta()
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db, explicit_pin=True)

    def assert_pin_before_credentials():
        assert shell.model == "pinned-model"
        assert shell.provider == "pinned-provider"
        assert shell.requested_provider == "pinned-provider"
        assert shell.base_url == "https://pin.invalid"
        assert shell.api_mode == "chat_completions"
        assert getattr(shell, "_resumed_smart_routing_runtime", None) is None
        return False

    shell._ensure_runtime_credentials = assert_pin_before_credentials

    with patch("cli._prepare_deferred_agent_startup"):
        assert shell._init_agent() is False


def test_resume_rejects_smart_route_bound_to_another_session():
    db = MagicMock()
    db.get_session.return_value = _session_meta(route_session_id="different-session")
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db)

    def assert_default_before_credentials():
        assert shell.model == "gpt-5.6-sol"
        assert shell.base_url == "https://default.invalid"
        assert getattr(shell, "_resumed_smart_routing_runtime", None) is None
        return False

    shell._ensure_runtime_credentials = assert_default_before_credentials

    with patch("cli._prepare_deferred_agent_startup"):
        assert shell._init_agent() is False


@pytest.mark.parametrize(
    "secret_key",
    ["api_key", "token", "secret", "password", "database_url"],
)
def test_resume_rejects_persisted_secret_material(secret_key):
    meta = _session_meta()
    config = json.loads(meta["model_config"])
    config["smart_routing_runtime"][secret_key] = "must-not-be-restored"
    meta["model_config"] = json.dumps(config)
    db = MagicMock()
    db.get_session.return_value = meta
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db)

    shell._restore_resumed_smart_routing_runtime()

    assert shell.model == "gpt-5.6-sol"
    assert getattr(shell, "_resumed_smart_routing_runtime", None) is None


def test_resume_fails_closed_when_session_metadata_read_raises():
    db = MagicMock()
    db.resolve_resume_session_id.return_value = SESSION_ID
    db.get_session.side_effect = RuntimeError("database unavailable")
    shell = _resume_cli(db)

    shell._restore_resumed_smart_routing_runtime()

    assert shell.model == "gpt-5.6-sol"
    assert getattr(shell, "_resumed_smart_routing_runtime", None) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", {"bad": "shape"}),
        ("provider", ["openai-codex"]),
        ("model", {"name": "gpt-5.5"}),
        ("base_url", ["https://example.invalid"]),
        ("api_mode", {"mode": "responses"}),
        ("tier", ["SIMPLE"]),
    ],
)
def test_resume_rejects_non_string_runtime_fields(field, value):
    meta = _session_meta()
    config = json.loads(meta["model_config"])
    config["smart_routing_runtime"][field] = value
    meta["model_config"] = json.dumps(config)
    db = MagicMock()
    db.get_session.return_value = meta
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db)

    shell._restore_resumed_smart_routing_runtime()

    assert shell.model == "gpt-5.6-sol"
    assert getattr(shell, "_resumed_smart_routing_runtime", None) is None


@pytest.mark.parametrize(
    "missing_field",
    ["session_id", "provider", "model", "base_url", "api_mode", "tier"],
)
def test_resume_rejects_runtime_missing_any_canonical_field(missing_field):
    meta = _session_meta()
    config = json.loads(meta["model_config"])
    config["smart_routing_runtime"].pop(missing_field)
    meta["model_config"] = json.dumps(config)
    db = MagicMock()
    db.get_session.return_value = meta
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db)

    shell._restore_resumed_smart_routing_runtime()

    assert shell.model == "gpt-5.6-sol"
    assert shell.base_url == "https://default.invalid"
    assert getattr(shell, "_resumed_smart_routing_runtime", None) is None


@pytest.mark.parametrize(
    "unsafe_base_url",
    [
        "https://user:password@example.invalid/v1",
        "https://example.invalid/v1?access_token=must-not-be-restored",
        "https://example.invalid/v1?authorization=must-not-be-restored",
        "https://example.invalid/v1?aws_access_key_id=AKIA_SENTINEL",
        "https://example.invalid/v1?api.key=SECRET_SENTINEL",
        "not a url",
        "https://example.invalid:bad/v1",
        "https:///missing-host/v1",
        "https://example.invalid/has whitespace",
        "http://example.invalid/v1",
        "ftp://example.invalid/v1",
        "file://example.invalid/path",
        "javascript://example.invalid/payload",
    ],
)
def test_resume_rejects_runtime_with_sensitive_base_url(unsafe_base_url):
    meta = _session_meta()
    config = json.loads(meta["model_config"])
    config["smart_routing_runtime"]["base_url"] = unsafe_base_url
    meta["model_config"] = json.dumps(config)
    db = MagicMock()
    db.get_session.return_value = meta
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db)

    shell._restore_resumed_smart_routing_runtime()

    assert shell.model == "gpt-5.6-sol"
    assert shell.base_url == "https://default.invalid"
    assert getattr(shell, "_resumed_smart_routing_runtime", None) is None


def test_resume_rejects_malformed_model_config():
    db = MagicMock()
    db.get_session.return_value = {"id": SESSION_ID, "model_config": "{broken"}
    db.resolve_resume_session_id.return_value = SESSION_ID
    shell = _resume_cli(db)

    shell._restore_resumed_smart_routing_runtime()

    assert shell.model == "gpt-5.6-sol"
    assert getattr(shell, "_resumed_smart_routing_runtime", None) is None


def test_resume_resolves_compression_descendant_before_restoring_route():
    descendant = "20260812_010900_descendant"
    meta = _session_meta(route_session_id=descendant)
    meta["id"] = descendant
    db = MagicMock()
    db.resolve_resume_session_id.return_value = descendant
    db.get_session.return_value = meta
    shell = _resume_cli(db)

    shell._restore_resumed_smart_routing_runtime()

    assert shell.session_id == descendant
    assert shell.model == "gpt-5.5"
    assert getattr(shell, "_resume_runtime_resolved_from") == SESSION_ID


def test_restored_runtime_is_not_projected_after_credential_fallback_changes_runtime():
    shell = HermesCLI.__new__(HermesCLI)
    route = json.loads(_session_meta()["model_config"])["smart_routing_runtime"]
    shell.agent = SimpleNamespace(
        provider="fallback-provider",
        model="fallback-model",
        _session_init_model_config={},
        _smart_routing_entrypoint_eligible=True,
    )
    shell.provider = "fallback-provider"
    shell.model = "fallback-model"
    shell._smart_routing_entrypoint_eligible = False
    setattr(shell, "_resumed_smart_routing_runtime", route)

    shell._project_smart_routing_entrypoint_eligibility()

    assert shell.agent._smart_routing_entrypoint_eligible is False
    assert not hasattr(shell.agent, "_smart_routing_runtime")
    assert "smart_routing_runtime" not in shell.agent._session_init_model_config


def test_restored_runtime_is_projected_to_agent_durable_config():
    shell = HermesCLI.__new__(HermesCLI)
    route = json.loads(_session_meta()["model_config"])["smart_routing_runtime"]
    shell.agent = SimpleNamespace(
        _session_init_model_config={},
        _smart_routing_entrypoint_eligible=True,
    )
    shell._smart_routing_entrypoint_eligible = False
    shell.provider = route["provider"]
    shell.model = route["model"]
    setattr(shell, "_resumed_smart_routing_runtime", route)

    shell._project_smart_routing_entrypoint_eligibility()

    assert shell.agent._smart_routing_entrypoint_eligible is False
    assert shell.agent._smart_routing_runtime == route
    assert shell.agent._session_init_model_config["smart_routing_runtime"] == route
