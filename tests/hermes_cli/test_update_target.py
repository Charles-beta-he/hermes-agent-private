from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import subprocess
from types import SimpleNamespace


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


def test_resolve_upstream_sync_target_separates_private_release_and_upstream_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "install.json").write_text(
        json.dumps(
            {
                "channel": "charles-private",
                "origin": "https://github.com/Charles-beta-he/hermes-agent-private.git",
                "upstream": "https://github.com/NousResearch/hermes-agent.git",
                "branch": "private/main",
            }
        ),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    from hermes_cli.update_target import resolve_upstream_sync_target

    target = resolve_upstream_sync_target(repo_dir=repo)

    assert target.role == "maintainer"
    assert target.release_ref == "origin/private/main"
    assert target.sync_ref == "upstream/main"
    assert target.release_branch == "private/main"
    assert target.sync_branch == "main"
    assert target.enabled is True
    assert target.apply_command == "hermes update --upstream-sync --plan"


def test_cmd_update_upstream_check_is_read_only_and_compares_upstream_to_private_release(monkeypatch, capsys):
    import hermes_cli.main as hm

    monkeypatch.setattr(hm, "PROJECT_ROOT", Path("/tmp/hermes-agent"))
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd == ["git", "rev-parse", "--verify", "--quiet", "origin/private/main"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd == ["git", "rev-parse", "--verify", "--quiet", "upstream/main"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd == ["git", "rev-list", "upstream/main..origin/private/main", "--count"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="4\n", stderr="")
        if cmd == ["git", "rev-list", "origin/private/main..upstream/main", "--count"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd!r}")

    monkeypatch.setattr(hm.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "hermes_cli.update_target.resolve_upstream_sync_target",
        lambda repo_dir=None: __import__("hermes_cli.update_target", fromlist=["UpstreamSyncTarget"]).UpstreamSyncTarget(
            role="maintainer",
            channel="charles-private",
            enabled=True,
            release_ref="origin/private/main",
            sync_ref="upstream/main",
            release_remote="origin",
            release_branch="private/main",
            sync_remote="upstream",
            sync_branch="main",
            reason="private channel with upstream remote",
            apply_command="hermes update --upstream-sync --plan",
        ),
    )

    hm.cmd_update(SimpleNamespace(upstream_check=True))

    out = capsys.readouterr().out
    assert "release target: origin/private/main" in out
    assert "sync source: upstream/main" in out
    assert "upstream/main is 9 commit(s) ahead" in out
    assert "private release has 4 commit(s) not in upstream" in out
    assert any(c == ["git", "fetch", "origin", "private/main", "--quiet"] for c in calls)
    assert any(c == ["git", "fetch", "upstream", "main", "--quiet"] for c in calls)
    assert not any("pull" in c for c in calls for c in c)
    assert not any("push" in c for c in calls for c in c)
