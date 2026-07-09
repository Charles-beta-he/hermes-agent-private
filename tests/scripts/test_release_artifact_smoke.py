from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_artifact_smoke.py"
spec = importlib.util.spec_from_file_location("release_artifact_smoke", MODULE_PATH)
assert spec is not None
release_artifact_smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = release_artifact_smoke
spec.loader.exec_module(release_artifact_smoke)


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_rejects_missing_wheel(tmp_path):
    assert not release_artifact_smoke.smoke_wheel(tmp_path / "missing.whl")


def test_rejects_non_wheel(tmp_path):
    artifact = tmp_path / "hermes.tar.gz"
    artifact.write_text("x")
    assert not release_artifact_smoke.smoke_wheel(artifact)


def test_smoke_wheel_installs_and_runs_cli_checks(monkeypatch, tmp_path):
    wheel = tmp_path / "hermes_agent-1.0.0-py3-none-any.whl"
    wheel.write_text("wheel")
    calls: list[tuple[str, ...]] = []

    class FakeBuilder:
        def __init__(self, with_pip: bool):
            assert with_pip is True

        def create(self, env_dir):
            bin_dir = Path(env_dir) / ("Scripts" if sys.platform == "win32" else "bin")
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text("python")

    def fake_run(cmd, **_kwargs):
        calls.append(tuple(cmd))
        if cmd[2:5] == ["pip", "install", "--no-input"]:
            return _proc(0, stdout="installed")
        if cmd[-1:] == ["--version"]:
            return _proc(0, stdout="Hermes 1.0.0")
        if cmd[-2:] == ["context", "--help"]:
            return _proc(0, stdout="usage: hermes context")
        return _proc(1, stderr="unexpected")

    monkeypatch.setattr(release_artifact_smoke.venv, "EnvBuilder", FakeBuilder)
    monkeypatch.setattr(release_artifact_smoke.shutil, "which", lambda _name: None)
    monkeypatch.setattr(release_artifact_smoke, "_run", fake_run)

    assert release_artifact_smoke.smoke_wheel(wheel)
    assert any(call[2:5] == ("pip", "install", "--no-input") for call in calls)
    assert any(call[-1:] == ("--version",) for call in calls)
    assert any(call[-2:] == ("context", "--help") for call in calls)


def test_smoke_wheel_fails_on_context_help_failure(monkeypatch, tmp_path):
    wheel = tmp_path / "hermes_agent-1.0.0-py3-none-any.whl"
    wheel.write_text("wheel")

    class FakeBuilder:
        def __init__(self, with_pip: bool):
            pass

        def create(self, env_dir):
            bin_dir = Path(env_dir) / ("Scripts" if sys.platform == "win32" else "bin")
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text("python")

    def fake_run(cmd, **_kwargs):
        if cmd[2:5] == ["pip", "install", "--no-input"] or cmd[-1:] == ["--version"]:
            return _proc(0, stdout="ok")
        if cmd[-2:] == ["context", "--help"]:
            return _proc(2, stderr="invalid choice: context")
        return _proc(1, stderr="unexpected")

    monkeypatch.setattr(release_artifact_smoke.venv, "EnvBuilder", FakeBuilder)
    monkeypatch.setattr(release_artifact_smoke.shutil, "which", lambda _name: None)
    monkeypatch.setattr(release_artifact_smoke, "_run", fake_run)

    assert not release_artifact_smoke.smoke_wheel(wheel)
