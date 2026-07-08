"""Shared update target resolution for Hermes CLI surfaces.

This module is intentionally read-only: it resolves what source a running
Hermes install should compare against, but it never fetches, pulls, upgrades, or
writes install metadata. Callers decide whether to perform a network check.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from hermes_constants import get_hermes_home


PRIVATE_REPO_CANONICAL = "github.com/charles-beta-he/hermes-agent-private"
OFFICIAL_REPO_CANONICAL = "github.com/nousresearch/hermes-agent"
OFFICIAL_REPO_URL = "https://github.com/NousResearch/hermes-agent.git"
PRIVATE_DEFAULT_BRANCH = "private/main"
PUBLIC_DEFAULT_BRANCH = "main"

GitStdout = Callable[[list[str]], Optional[str]]


@dataclass(frozen=True)
class UpdateTarget:
    """Normalized update source used by CLI, banner, and dashboard surfaces."""

    channel: str
    source: str
    remote: str
    branch: str
    compare_ref: str
    update_command: str
    can_apply: bool = True
    origin: str | None = None
    upstream: str | None = None


def canonical_github_remote(url: str | None) -> str:
    """Return ``host/owner/repo`` for common GitHub remote URL forms."""
    if not url:
        return ""
    value = url.strip()
    if value.startswith("git@github.com:"):
        value = "github.com/" + value[len("git@github.com:"):]
    elif value.startswith("ssh://git@github.com/"):
        value = "github.com/" + value[len("ssh://git@github.com/"):]
    else:
        parsed = urlparse(value)
        if parsed.netloc and parsed.path:
            value = f"{parsed.netloc}{parsed.path}"
    value = value.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value.lower()


def load_install_metadata() -> dict:
    """Read ``$HERMES_HOME/install.json`` if present; invalid files are ignored."""
    path = get_hermes_home() / "install.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _subprocess_git_stdout(args: list[str], *, cwd: Path, timeout: int = 5) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def _normalize_branch(branch: str | None, fallback: str) -> str:
    value = str(branch or "").strip()
    return value or fallback


def _target_from_branch(
    *,
    branch: str,
    source: str,
    channel: str,
    origin: str | None,
    upstream: str | None,
) -> UpdateTarget:
    # Private channels and non-main branches belong to origin. Public main keeps
    # the historical update-check behavior of comparing with upstream/main when
    # available in the apply path, while passive banner/dashboard checks compare
    # against the local origin tracking ref to avoid accidental SSH prompts.
    remote = "origin"
    return UpdateTarget(
        channel=channel,
        source=source,
        remote=remote,
        branch=branch,
        compare_ref=f"{remote}/{branch}",
        update_command="hermes update",
        can_apply=True,
        origin=origin,
        upstream=upstream,
    )


def resolve_update_target(
    *,
    repo_dir: Path | None = None,
    explicit_branch: str | None = None,
    git_stdout: Callable[..., Optional[str]] | None = None,
) -> UpdateTarget:
    """Resolve the authoritative update target for this install.

    Precedence:
    1. explicit branch (CLI ``--branch``);
    2. ``$HERMES_HOME/install.json`` metadata stamped by installers;
    3. private origin URL => ``origin/private/main``;
    4. current branch's upstream tracking ref;
    5. public fallback ``origin/main``.
    """
    repo = Path(repo_dir) if repo_dir is not None else None
    metadata = load_install_metadata()

    def git(args: list[str], *, timeout: int = 5) -> Optional[str]:
        if repo is None:
            return None
        if git_stdout is not None:
            try:
                return git_stdout(args, cwd=repo, timeout=timeout)
            except TypeError:
                try:
                    return git_stdout(args)  # type: ignore[misc]
                except Exception:
                    return None
            except Exception:
                return None
        return _subprocess_git_stdout(args, cwd=repo, timeout=timeout)

    origin = str(metadata.get("origin") or "").strip() or git(["remote", "get-url", "origin"])
    upstream = str(metadata.get("upstream") or "").strip() or git(["remote", "get-url", "upstream"])

    if explicit_branch and str(explicit_branch).strip():
        branch = _normalize_branch(explicit_branch, PUBLIC_DEFAULT_BRANCH)
        channel = str(metadata.get("channel") or "custom").strip() or "custom"
        return _target_from_branch(
            branch=branch,
            source="explicit",
            channel=channel,
            origin=origin,
            upstream=upstream,
        )

    metadata_branch = str(metadata.get("branch") or "").strip()
    if metadata_branch:
        channel = str(metadata.get("channel") or "custom").strip() or "custom"
        return _target_from_branch(
            branch=metadata_branch,
            source="install.json",
            channel=channel,
            origin=origin,
            upstream=upstream,
        )

    if canonical_github_remote(origin) == PRIVATE_REPO_CANONICAL:
        return _target_from_branch(
            branch=PRIVATE_DEFAULT_BRANCH,
            source="git-origin",
            channel="charles-private",
            origin=origin,
            upstream=upstream,
        )

    tracking_ref = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if tracking_ref and "/" in tracking_ref:
        remote, branch = tracking_ref.split("/", 1)
        if remote and branch:
            return UpdateTarget(
                channel="public",
                source="git-upstream",
                remote=remote,
                branch=branch,
                compare_ref=tracking_ref,
                update_command="hermes update",
                can_apply=True,
                origin=origin,
                upstream=upstream,
            )

    return _target_from_branch(
        branch=PUBLIC_DEFAULT_BRANCH,
        source="fallback",
        channel="public",
        origin=origin,
        upstream=upstream,
    )


def resolve_update_branch(explicit_branch: str | None = None, *, repo_dir: Path | None = None) -> str:
    """Compatibility helper for CLI update code that still takes a branch."""
    return resolve_update_target(repo_dir=repo_dir, explicit_branch=explicit_branch).branch
