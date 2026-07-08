from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


def test_resolve_update_target_prefers_install_metadata_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "install.json").write_text(
        json.dumps(
            {
                "channel": "charles-private",
                "origin": "https://github.com/Charles-beta-he/hermes-agent-private.git",
                "upstream": "https://github.com/NousResearch/hermes-agent.git",
                "branch": "private/main",
                "installer": "scripts/install-private.sh",
            }
        ),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    from hermes_cli.update_target import resolve_update_target

    target = resolve_update_target(repo_dir=repo)

    assert target.channel == "charles-private"
    assert target.branch == "private/main"
    assert target.remote == "origin"
    assert target.compare_ref == "origin/private/main"
    assert target.source == "install.json"
    assert target.update_command == "hermes update"


def test_resolve_update_target_private_origin_defaults_to_private_main(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    def fake_git(args, *, cwd, timeout=5):
        if args == ["remote", "get-url", "origin"]:
            return "https://github.com/Charles-beta-he/hermes-agent-private.git"
        raise AssertionError(args)

    from hermes_cli.update_target import resolve_update_target

    target = resolve_update_target(repo_dir=repo, git_stdout=fake_git)

    assert target.channel == "charles-private"
    assert target.branch == "private/main"
    assert target.compare_ref == "origin/private/main"
    assert target.source == "git-origin"


def test_banner_check_via_local_git_uses_resolved_private_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "install.json").write_text(
        json.dumps({"channel": "charles-private", "branch": "private/main"}),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return MagicMock(returncode=0, stdout="https://github.com/Charles-beta-he/hermes-agent-private.git\n")
        if cmd == ["git", "rev-parse", "--is-shallow-repository"]:
            return MagicMock(returncode=0, stdout="false\n")
        if cmd == ["git", "fetch", "origin", "private/main", "--quiet"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/private/main"]:
            return MagicMock(returncode=0, stdout="2\n")
        raise AssertionError(f"unexpected command: {cmd!r}")

    import hermes_cli.banner as banner

    monkeypatch.setattr(banner.subprocess, "run", fake_run)
    result = banner._check_via_local_git(repo)

    assert result == 2
    assert ["git", "rev-list", "--count", "HEAD..origin/main"] not in calls


def test_dashboard_recent_commits_uses_resolved_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "install.json").write_text(json.dumps({"branch": "private/main"}), encoding="utf-8")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:4] == ["git", "-C", str(Path(cmd[2])), "remote"]:
            return MagicMock(returncode=0, stdout="https://github.com/Charles-beta-he/hermes-agent-private.git\n")
        if cmd[:4] == ["git", "-C", str(Path(cmd[2])), "log"]:
            assert "HEAD..origin/private/main" in cmd
            return MagicMock(returncode=0, stdout="abc123\x1ffeat: private\x1fdev\x1f1\n")
        raise AssertionError(f"unexpected command: {cmd!r}")

    import hermes_cli.web_server as ws

    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    rows = ws._recent_upstream_commits()

    assert rows[0]["summary"] == "feat: private"
    assert all("HEAD..origin/main" not in cmd for cmd in calls)
