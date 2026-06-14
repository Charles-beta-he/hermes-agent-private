from __future__ import annotations

import os
import subprocess
from pathlib import Path

import scripts.private_experience_selfcheck as selfcheck

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PRIVATE = REPO_ROOT / "scripts" / "install-private.sh"
BOOTSTRAP_PRIVATE = REPO_ROOT / "scripts" / "bootstrap-private.sh"


def test_private_experience_bundle_is_sanitized() -> None:
    result = selfcheck.validate_bundle()
    assert result["status"] == "ok", result["findings"]
    assert result["manifest"]["policy"] == "templates_only_no_secrets_no_memory_no_runtime_state"
    not_copied = set(result["manifest"]["source_model"]["not_copied"])
    assert "live config.yaml or .env" in not_copied
    assert "memory/user profile/session databases" in not_copied
    assert "project-profiles with real client/project facts" in not_copied


def test_private_installer_dry_run_installs_experience_pack_by_default(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        ["bash", str(INSTALL_PRIVATE), "--dry-run", "--skip-setup", "--yes"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Installing sanitized experience templates" in proc.stdout
    assert "copy missing files" in proc.stdout
    assert "Skipped ./setup-hermes.sh" in proc.stdout


def test_private_installer_can_skip_experience_pack(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        ["bash", str(INSTALL_PRIVATE), "--dry-run", "--skip-setup", "--skip-experience-pack", "--yes"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Skipped sanitized experience templates" in proc.stdout
    assert "copy missing files" not in proc.stdout


def test_private_bootstrap_passes_skip_experience_pack_to_finalizer() -> None:
    text = BOOTSTRAP_PRIVATE.read_text(encoding="utf-8")
    assert "--skip-experience-pack" in text
    assert "args+=(--skip-experience-pack)" in text


def test_private_bootstrap_documents_headless_ssh_path() -> None:
    proc = subprocess.run(
        ["bash", str(BOOTSTRAP_PRIVATE), "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Headless SSH" in proc.stdout
    assert "no gh" in proc.stdout
    assert "git@github.com:Charles-beta-he/hermes-agent-private.git" in proc.stdout
    assert "Never put a token in the repository URL" in proc.stdout


def test_private_bootstrap_dry_run_prints_ssh_path(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        ["bash", str(BOOTSTRAP_PRIVATE), "--dry-run", "--skip-setup", "--ssh", "--yes"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Headless SSH, no gh/browser" in proc.stdout
    assert "Dry-run: would verify SSH access" in proc.stdout
    assert "git clone --branch" in proc.stdout
    assert "--ssh" not in proc.stderr
