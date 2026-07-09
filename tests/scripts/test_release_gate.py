from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_gate.py"
spec = importlib.util.spec_from_file_location("release_gate", MODULE_PATH)
assert spec is not None
release_gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = release_gate
spec.loader.exec_module(release_gate)


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_critical_imports_fail_when_main_registered_module_is_missing(monkeypatch):
    def fake_run(cmd, **_kwargs):
        assert cmd[:2] == ["python", "-c"]
        assert "import hermes_cli.main" in cmd[2]
        return _proc(1, stderr="ModuleNotFoundError: No module named 'hermes_cli.subcommands.context'")

    monkeypatch.setattr(release_gate, "_run", fake_run)

    result = release_gate.check_imports("python")

    assert not result.ok
    assert result.name == "critical-imports"
    assert "hermes_cli.subcommands.context" in result.detail


def test_cli_smoke_requires_context_help(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(tuple(cmd))
        if cmd[-2:] == ["context", "--help"]:
            return _proc(2, stderr="invalid choice: 'context'")
        return _proc(0, stdout="ok")

    monkeypatch.setattr(release_gate, "_run", fake_run)

    result = release_gate.check_cli_smoke("python")

    assert not result.ok
    assert result.name == "cli-smoke"
    assert any(call[-2:] == ("context", "--help") for call in calls)
    assert "invalid choice" in result.detail


def test_run_gate_can_be_used_in_ci_without_git_or_pytest(monkeypatch):
    called: list[str] = []

    monkeypatch.setattr(release_gate, "check_compile", lambda _python: called.append("compile") or release_gate.CheckResult("critical-py-compile", True))
    monkeypatch.setattr(release_gate, "check_imports", lambda _python: called.append("imports") or release_gate.CheckResult("critical-imports", True))
    monkeypatch.setattr(release_gate, "check_cli_smoke", lambda _python: called.append("cli") or release_gate.CheckResult("cli-smoke", True))
    monkeypatch.setattr(release_gate, "check_focused_tests", lambda _python: called.append("tests") or release_gate.CheckResult("focused-tests", True))
    monkeypatch.setattr(release_gate, "check_git_clean", lambda: called.append("git-clean") or release_gate.CheckResult("git-clean", True))
    monkeypatch.setattr(release_gate, "check_remote_sync", lambda **_kw: called.append("remote") or release_gate.CheckResult("remote-sync", True))

    args = release_gate.argparse.Namespace(
        python="python",
        branch="private/main",
        remote="origin",
        skip_git_clean=True,
        skip_remote_sync=True,
        skip_tests=True,
    )

    results = release_gate.run_gate(args)

    assert [result.name for result in results] == ["critical-py-compile", "critical-imports", "cli-smoke"]
    assert called == ["compile", "imports", "cli"]
