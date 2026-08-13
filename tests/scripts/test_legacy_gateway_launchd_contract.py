from __future__ import annotations

import importlib.machinery
import importlib.util
import plistlib
from pathlib import Path


def _load_legacy_gateway_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "hermes-gateway"
    loader = importlib.machinery.SourceFileLoader("legacy_gateway_script", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_legacy_macos_installer_matches_immutable_runtime_contract(monkeypatch):
    module = _load_legacy_gateway_script()
    monkeypatch.setattr(module, "get_python_path", lambda: "/release/.venv/bin/python")
    payload = plistlib.loads(module.generate_launchd_plist().encode())

    assert payload["ProgramArguments"][:4] == [
        "/release/.venv/bin/python",
        "-s",
        "-m",
        "hermes_cli.main",
    ]
    environment = payload["EnvironmentVariables"]
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    assert environment["HERMES_IMMUTABLE_RUNTIME"] == "1"
    assert "HERMES_LAZY_INSTALL_TARGET" not in environment
