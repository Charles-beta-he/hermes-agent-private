"""Tests for Google Gemini OAuth entries shown in the /model picker."""

from unittest.mock import patch

from hermes_cli.model_switch import list_authenticated_providers


def test_google_gemini_cli_picker_uses_oauth_auth_status():
    """Gemini OAuth credentials live outside auth.json/pool but must show in /model."""
    with patch("agent.models_dev.fetch_models_dev", return_value={}), \
         patch("hermes_cli.auth.get_auth_status") as auth_status, \
         patch("hermes_cli.auth._load_auth_store", return_value={}), \
         patch("agent.credential_pool.load_pool") as load_pool:
        auth_status.side_effect = lambda provider: (
            {"logged_in": True, "email": "user@example.com"}
            if provider == "google-gemini-cli"
            else {"logged_in": False}
        )
        load_pool.return_value.has_credentials.return_value = False

        providers = list_authenticated_providers(current_provider="openai-codex", max_models=50)

    gemini = next((p for p in providers if p["slug"] == "google-gemini-cli"), None)

    assert gemini is not None
    assert gemini["name"] == "Google Gemini (OAuth)"
    assert "gemini-3-flash-preview" in gemini["models"]
    assert "gemini-3.1-pro-preview" in gemini["models"]
