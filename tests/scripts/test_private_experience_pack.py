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
    manifest = result["manifest"]
    assert manifest["policy"] == "templates_only_no_secrets_no_memory_no_runtime_state"
    assert manifest["package"] == "capability_shape"
    assert manifest["exclude"] == "lived_state"
    assert manifest["overwrite_existing"] is False
    assert manifest["default_enabled"] is False
    assert manifest["activation_gates"]["credentials"] == "never_agent_typed"
    assert manifest["activation_gates"]["publish_deploy_push"] == "exact_target_rollback_receipt_required"
    copied = set(manifest["source_model"]["copied"])
    assert "replaceable persona/profile/project schemas" in copied
    assert "default-off side-effect gates" in copied
    not_copied = set(manifest["source_model"]["not_copied"])
    assert "live config.yaml or .env" in not_copied
    assert "memory/user profile/session databases" in not_copied
    assert "project-profiles with real client/project facts" in not_copied
    assert "gateway chat IDs or delivery targets" in not_copied


def test_private_experience_bundle_expected_defaults_exist() -> None:
    result = selfcheck.validate_bundle()
    paths = {item["path"] for item in result["manifest"]["files"]}
    assert "user-profile.schema.json" in paths
    assert "user-profile.example.json" in paths
    assert "project-profile.schema.json" in paths
    assert "ssot.template.md" in paths
    assert "evidence-ledger.template.json" in paths
    assert "authority-matrix.template.yaml" in paths
    assert "side-effect-gates.template.yaml" in paths
    assert "memory-policy.md" in paths
    assert "evidence-first-completion.md" in paths
    assert "capability-graph-primer.md" in paths


def test_private_experience_scanner_rejects_owner_path(tmp_path: Path) -> None:
    bad = selfcheck.BUNDLE_ROOT / "temporary-bad-owner-path.md"
    try:
        bad.write_text("bad /Users/charles/private-state\n", encoding="utf-8")
        result = selfcheck.validate_bundle()
        assert result["status"] == "failed"
        assert any("/Users/charles" in finding for finding in result["findings"])
    finally:
        bad.unlink(missing_ok=True)


def test_private_experience_scanner_rejects_secret_assignment(tmp_path: Path) -> None:
    bad = selfcheck.BUNDLE_ROOT / "temporary-bad-secret.md"
    try:
        bad.write_text("api_key = 'sk-example-secret-value'\n", encoding="utf-8")
        result = selfcheck.validate_bundle()
        assert result["status"] == "failed"
        assert any("api" in finding.lower() for finding in result["findings"])
    finally:
        bad.unlink(missing_ok=True)


def test_private_experience_install_helper_copies_and_skips(tmp_path: Path) -> None:
    result = selfcheck.install_bundle(tmp_path)
    assert result["status"] == "ok"
    target = tmp_path / "templates" / "private-experience"
    assert (target / "README.md").exists()
    assert (target / "user-profile.example.json").exists()
    receipt = tmp_path / "install-receipts" / "private-experience-pack.json"
    assert receipt.exists()
    second = selfcheck.install_bundle(tmp_path)
    assert second["status"] == "ok"
    assert any(action["action"] == "skip" for action in second["actions"])


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
    assert "private_experience_selfcheck.py" in proc.stdout
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
