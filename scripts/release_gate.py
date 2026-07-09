#!/usr/bin/env python3
"""Fail-closed release gate for Hermes Agent.

This gate is intentionally focused on bootstrap/update regressions that can make
`hermes` unusable after install/update, plus the minimal git hygiene required
before tagging a release.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CRITICAL_COMPILE_FILES = (
    "hermes_cli/main.py",
    "hermes_cli/config.py",
    "hermes_cli/__init__.py",
    "hermes_cli/web_server.py",
    "cli.py",
    "run_agent.py",
    "model_tools.py",
)
CRITICAL_IMPORTS = ("hermes_cli.main",)
CLI_SMOKE_COMMANDS = (
    ("--version",),
    ("context", "--help"),
)
FOCUSED_TESTS = (
    "tests/hermes_cli/test_update_post_pull_syntax_guard.py",
    "tests/hermes_cli/test_context_engineering_cmd.py",
    "tests/hermes_cli/test_update_target.py",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def _run(cmd: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True)


def _format_failure(proc: subprocess.CompletedProcess[str], max_chars: int = 1600) -> str:
    out = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
    return out[-max_chars:] if out else f"exit {proc.returncode}"


def check_git_clean() -> CheckResult:
    proc = _run(["git", "status", "--porcelain"])
    if proc.returncode != 0:
        return CheckResult("git-clean", False, _format_failure(proc))
    if proc.stdout.strip():
        return CheckResult("git-clean", False, proc.stdout.strip())
    return CheckResult("git-clean", True)


def check_remote_sync(branch: str = "private/main", remote: str = "origin") -> CheckResult:
    fetch = _run(["git", "fetch", remote, "--prune"])
    if fetch.returncode != 0:
        return CheckResult("remote-sync", False, _format_failure(fetch))
    proc = _run(["git", "rev-list", "--left-right", "--count", f"{remote}/{branch}...HEAD"])
    if proc.returncode != 0:
        return CheckResult("remote-sync", False, _format_failure(proc))
    parts = proc.stdout.strip().split()
    if len(parts) != 2:
        return CheckResult("remote-sync", False, f"unexpected rev-list output: {proc.stdout!r}")
    behind, ahead = parts
    if behind != "0" or ahead != "0":
        return CheckResult("remote-sync", False, f"{remote}/{branch}...HEAD behind={behind} ahead={ahead}")
    return CheckResult("remote-sync", True, f"{remote}/{branch} == HEAD")


def check_compile(python: str) -> CheckResult:
    files = [str(REPO_ROOT / rel) for rel in CRITICAL_COMPILE_FILES if (REPO_ROOT / rel).exists()]
    proc = _run([python, "-m", "py_compile", *files])
    if proc.returncode != 0:
        return CheckResult("critical-py-compile", False, _format_failure(proc))
    return CheckResult("critical-py-compile", True, f"{len(files)} files")


def check_imports(python: str) -> CheckResult:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    code = "\n".join(f"import {name}" for name in CRITICAL_IMPORTS)
    proc = _run([python, "-c", code], env=env)
    if proc.returncode != 0:
        return CheckResult("critical-imports", False, _format_failure(proc))
    return CheckResult("critical-imports", True, ", ".join(CRITICAL_IMPORTS))


def check_cli_smoke(python: str) -> CheckResult:
    for args in CLI_SMOKE_COMMANDS:
        proc = _run([python, "-m", "hermes_cli.main", *args])
        if proc.returncode != 0:
            return CheckResult("cli-smoke", False, f"{' '.join(args)} failed:\n{_format_failure(proc)}")
    return CheckResult("cli-smoke", True, "; ".join(" ".join(args) for args in CLI_SMOKE_COMMANDS))


def check_focused_tests(python: str) -> CheckResult:
    proc = _run([python, "-m", "pytest", *FOCUSED_TESTS, "-q"])
    if proc.returncode != 0:
        return CheckResult("focused-tests", False, _format_failure(proc))
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "pytest passed"
    return CheckResult("focused-tests", True, summary)


def run_gate(args: argparse.Namespace) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not args.skip_git_clean:
        results.append(check_git_clean())
    if not args.skip_remote_sync:
        results.append(check_remote_sync(branch=args.branch, remote=args.remote))
    results.append(check_compile(args.python))
    results.append(check_imports(args.python))
    results.append(check_cli_smoke(args.python))
    if not args.skip_tests:
        results.append(check_focused_tests(args.python))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Hermes release readiness gates")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use for import/CLI/test checks")
    parser.add_argument("--branch", default="private/main", help="Remote branch used by the sync gate")
    parser.add_argument("--remote", default="origin", help="Remote name used by the sync gate")
    parser.add_argument("--skip-git-clean", action="store_true", help="Do not require a clean worktree")
    parser.add_argument("--skip-remote-sync", action="store_true", help="Do not require HEAD to match remote/branch")
    parser.add_argument("--skip-tests", action="store_true", help="Skip focused pytest slice")
    args = parser.parse_args(argv)

    results = run_gate(args)
    failed = [result for result in results if not result.ok]
    for result in results:
        mark = "✓" if result.ok else "✗"
        suffix = f" — {result.detail}" if result.detail else ""
        print(f"{mark} {result.name}{suffix}")

    if failed:
        print("\nRelease gate failed; refusing to publish/tag.", file=sys.stderr)
        return 1
    print("\nRelease gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
