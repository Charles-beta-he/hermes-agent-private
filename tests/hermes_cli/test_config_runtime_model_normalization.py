"""Regression tests for canonical runtime model config normalization."""

from __future__ import annotations

from hermes_cli.config import _normalize_root_model_keys, load_config


def test_load_cli_config_uses_canonical_model_normalization(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "provider: root-provider\n"
        "base_url: https://root.invalid/v1\n"
        "model:\n"
        "  model: cli-legacy-model\n",
        encoding="utf-8",
    )
    import cli

    monkeypatch.setattr(cli, "_hermes_home", tmp_path)

    cfg = cli.load_cli_config()

    assert cfg["model"]["default"] == "cli-legacy-model"
    assert cfg["model"]["provider"] == "root-provider"
    assert cfg["model"]["base_url"] == "https://root.invalid/v1"


def test_load_config_promotes_legacy_model_model_to_default(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "model:\n"
        "  model: gpt-5.5\n"
        "  provider: openai-codex\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(load_config.__globals__, "get_config_path", lambda: config_file)

    cfg = load_config()

    assert cfg["model"]["default"] == "gpt-5.5"
    assert cfg["model"]["model"] == "gpt-5.5"
    assert cfg["model"]["provider"] == "openai-codex"


def test_normalize_root_model_keys_uses_root_only_as_fallback():
    cfg = _normalize_root_model_keys(
        {
            "provider": "root-provider",
            "base_url": "https://root.invalid/v1",
            "model": {
                "model": "legacy-model-field",
                "provider": "nested-provider",
            },
        }
    )

    assert cfg["model"]["default"] == "legacy-model-field"
    assert cfg["model"]["provider"] == "nested-provider"
    assert cfg["model"]["base_url"] == "https://root.invalid/v1"
    assert "provider" not in cfg
    assert "base_url" not in cfg


def test_normalize_root_model_keys_preserves_explicit_default_over_model_field():
    cfg = _normalize_root_model_keys(
        {
            "model": {
                "default": "canonical-default",
                "model": "legacy-model-field",
            }
        }
    )

    assert cfg["model"]["default"] == "canonical-default"
    assert cfg["model"]["model"] == "legacy-model-field"
