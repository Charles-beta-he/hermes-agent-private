"""Regression coverage for Gemini OAuth provider visibility in /model picker."""

from unittest.mock import patch

from hermes_cli.model_switch import list_authenticated_providers


def test_google_gemini_cli_oauth_auth_status_appears_in_model_picker(monkeypatch):
    """OAuth-only Gemini CLI should not require env/auth-store credentials."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fake_auth_status(provider):
        if provider == "google-gemini-cli":
            return {"logged_in": True, "auth_type": "google_oauth"}
        return {"logged_in": False}

    with patch("hermes_cli.auth.get_auth_status", side_effect=fake_auth_status), \
         patch("hermes_cli.auth._load_auth_store", return_value={"providers": {}}), \
         patch("agent.credential_pool.load_pool") as load_pool, \
         patch("hermes_cli.models.cached_provider_model_ids", return_value=["gemini-2.5-pro"]):
        load_pool.return_value.has_credentials.return_value = False
        providers = list_authenticated_providers(current_provider="openrouter", max_models=10)

    gemini = next((p for p in providers if p["slug"] == "google-gemini-cli"), None)
    assert gemini is not None
    assert gemini["models"] == ["gemini-2.5-pro"]
    assert gemini["total_models"] == 1


def test_google_gemini_cli_oauth_logged_out_stays_hidden(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fake_auth_status(provider):
        if provider == "google-gemini-cli":
            return {"logged_in": False, "auth_type": "google_oauth"}
        return {"logged_in": False}

    with patch("hermes_cli.auth.get_auth_status", side_effect=fake_auth_status), \
         patch("hermes_cli.auth._load_auth_store", return_value={"providers": {}}), \
         patch("agent.credential_pool.load_pool") as load_pool, \
         patch("hermes_cli.models.cached_provider_model_ids", return_value=["gemini-2.5-pro"]):
        load_pool.return_value.has_credentials.return_value = False
        providers = list_authenticated_providers(current_provider="openrouter", max_models=10)

    assert all(p["slug"] != "google-gemini-cli" for p in providers)


def test_external_process_command_status_does_not_make_provider_authenticated(monkeypatch):
    monkeypatch.delenv("COPILOT_ACP_BASE_URL", raising=False)

    def fake_auth_status(provider):
        if provider == "copilot-acp":
            return {"logged_in": True, "configured": True, "auth_type": "external_process"}
        return {"logged_in": False}

    def fake_cached_models(provider, *args, **kwargs):
        if provider == "copilot-acp":
            return ["copilot/test-model"]
        return ["placeholder-model"]

    with patch("hermes_cli.auth.get_auth_status", side_effect=fake_auth_status), \
         patch("hermes_cli.auth._load_auth_store", return_value={"providers": {}}), \
         patch("agent.credential_pool.load_pool") as load_pool, \
         patch("hermes_cli.models.cached_provider_model_ids", side_effect=fake_cached_models):
        load_pool.return_value.has_credentials.return_value = False
        providers = list_authenticated_providers(current_provider="openrouter", max_models=10)

    assert all(p["slug"] != "copilot-acp" for p in providers)
