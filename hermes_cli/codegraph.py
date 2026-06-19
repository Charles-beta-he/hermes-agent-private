#!/usr/bin/env python3
"""Hermes local codegraph manager for codebase-memory-mcp.

Enterprise default: make local knowledge-graph capability ready to use without
silently crossing trust/permission boundaries. The manager can diagnose a binary,
write a Hermes-scoped MCP activation snippet, optionally merge config.yaml with a
backup, and run an end-to-end indexing/query smoke test against a chosen repo.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_BINARY = "codebase-memory-mcp"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Result:
    ok: bool
    checks: list[Check]
    data: dict[str, Any]


def hermes_home_arg(value: str | None) -> Path:
    return Path(value or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().resolve()


def resolve_binary(binary: str | None) -> str | None:
    candidate = binary or DEFAULT_BINARY
    p = Path(candidate).expanduser()
    if p.exists():
        return str(p.resolve())
    found = shutil.which(candidate)
    return found


def run_json(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 120) -> tuple[bool, Any, str]:
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env, timeout=timeout)
    text = proc.stdout.strip()
    if proc.returncode != 0:
        return False, None, (proc.stderr or proc.stdout).strip()
    try:
        return True, json.loads(text), proc.stderr.strip()
    except json.JSONDecodeError:
        return False, None, text[:1000]


def version(binary: str) -> tuple[bool, str]:
    proc = subprocess.run([binary, "--version"], text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "unknown"


def mcp_snippet(binary: str, cache_dir: Path) -> str:
    return (
        "schema: hermes_codegraph_mcp_activation_v1\n"
        "mcp_servers:\n"
        "  codebase_memory:\n"
        f"    command: {binary}\n"
        "    args: []\n"
        "    timeout: 30\n"
        "    env:\n"
        f"      CBM_CACHE_DIR: {cache_dir}\n"
        "activation:\n"
        "  default_enabled: false\n"
        "  evidence_required:\n"
        "    - binary_version\n"
        "    - cache_dir\n"
        "    - index_repository_smoke\n"
        "    - search_graph_smoke\n"
        "    - trace_path_smoke\n"
    )


def doctor(binary_arg: str | None, home: Path) -> Result:
    checks: list[Check] = []
    binary = resolve_binary(binary_arg)
    checks.append(Check("binary_found", bool(binary), binary or f"not found: {binary_arg or DEFAULT_BINARY}"))
    ver = None
    if binary:
        ok, ver = version(binary)
        checks.append(Check("binary_runs", ok, ver))
    cache_dir = home / "cache" / "codebase-memory-mcp"
    checks.append(Check("cache_dir_scoped", str(cache_dir).startswith(str(home)), str(cache_dir)))
    snippet = home / "config" / "codegraph-mcp.yaml"
    checks.append(Check("activation_snippet", snippet.exists(), str(snippet)))
    return Result(ok=all(c.ok for c in checks[:3]), checks=checks, data={"binary": binary, "version": ver, "cache_dir": str(cache_dir), "snippet": str(snippet)})


def enable(binary_arg: str | None, home: Path, dry_run: bool, force: bool, merge_config: bool) -> Result:
    checks: list[Check] = []
    binary = resolve_binary(binary_arg)
    if not binary:
        return Result(False, [Check("binary_found", False, f"not found: {binary_arg or DEFAULT_BINARY}")], {})
    ok, ver = version(binary)
    checks.append(Check("binary_runs", ok, ver))
    if not ok:
        return Result(False, checks, {"binary": binary})
    cache_dir = home / "cache" / "codebase-memory-mcp"
    snippet_path = home / "config" / "codegraph-mcp.yaml"
    activation = home / "state" / "codegraph-activation.json"
    payload = mcp_snippet(binary, cache_dir)
    ops: list[dict[str, str]] = []
    for d in [home / "config", home / "state", cache_dir]:
        ops.append({"action": "mkdir" if not d.exists() else "exists", "path": str(d)})
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)
    if snippet_path.exists() and not force:
        ops.append({"action": "skip", "path": str(snippet_path), "reason": "exists; use --force to overwrite"})
    else:
        ops.append({"action": "write", "path": str(snippet_path)})
        if not dry_run:
            snippet_path.write_text(payload, encoding="utf-8")
    activation_payload = {
        "schema": "hermes_codegraph_activation_v1",
        "enabled": bool(merge_config),
        "binary": binary,
        "version": ver,
        "cache_dir": str(cache_dir),
        "mcp_snippet": str(snippet_path),
        "side_effects": {"ui_enabled": False, "auto_index": False, "shared_artifact": False},
    }
    ops.append({"action": "write", "path": str(activation)})
    if not dry_run:
        activation.write_text(json.dumps(activation_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if merge_config:
        merge_ok, merge_detail = merge_config_yaml(home / "config.yaml", binary, cache_dir, dry_run=dry_run, force=force)
        checks.append(Check("config_yaml_merge", merge_ok, merge_detail))
    return Result(all(c.ok for c in checks), checks, {"operations": ops, "activation": activation_payload})


def merge_config_yaml(config_path: Path, binary: str, cache_dir: Path, dry_run: bool, force: bool) -> tuple[bool, str]:
    try:
        import yaml  # type: ignore
    except Exception as exc:
        return False, f"PyYAML unavailable, cannot merge config.yaml safely: {exc}"
    data: dict[str, Any]
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    mcp = data.setdefault("mcp_servers", {})
    if "codebase_memory" in mcp and not force:
        return True, "codebase_memory already present; preserved"
    mcp["codebase_memory"] = {
        "command": binary,
        "args": [],
        "timeout": 30,
        "env": {"CBM_CACHE_DIR": str(cache_dir)},
    }
    if dry_run:
        return True, f"dry-run would merge {config_path}"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        backup = config_path.with_suffix(config_path.suffix + f".bak.{int(time.time())}")
        shutil.copy2(config_path, backup)
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return True, f"merged {config_path}"


def project_name_from_index(payload: dict[str, Any], repo: Path) -> str:
    return str(payload.get("project") or repo.name)


def list_status(binary_arg: str | None, home: Path) -> Result:
    binary = resolve_binary(binary_arg)
    cache_dir = home / "cache" / "codebase-memory-mcp"
    snippet = home / "config" / "codegraph-mcp.yaml"
    activation = home / "state" / "codegraph-activation.json"
    config_yaml = home / "config.yaml"
    data: dict[str, Any] = {
        "binary": binary,
        "cache_dir": str(cache_dir),
        "snippet": str(snippet),
        "snippet_exists": snippet.exists(),
        "activation": str(activation),
        "activation_exists": activation.exists(),
        "config_yaml": str(config_yaml),
        "config_yaml_exists": config_yaml.exists(),
        "merged_in_config": False,
    }
    if activation.exists():
        try:
            data["activation_payload"] = json.loads(activation.read_text(encoding="utf-8"))
        except Exception as exc:
            data["activation_read_error"] = str(exc)
    if config_yaml.exists():
        try:
            import yaml  # type: ignore

            cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
            data["merged_in_config"] = "codebase_memory" in (cfg.get("mcp_servers") or {})
        except Exception as exc:
            data["config_read_error"] = str(exc)
    checks = [
        Check("binary_found", bool(binary), binary or f"not found: {binary_arg or DEFAULT_BINARY}"),
        Check("snippet_exists", snippet.exists(), str(snippet)),
        Check("activation_exists", activation.exists(), str(activation)),
    ]
    return Result(True, checks, data)


def smoke(binary_arg: str | None, home: Path, repo: Path, query: str | None, function_name: str | None, mode: str) -> Result:
    checks: list[Check] = []
    binary = resolve_binary(binary_arg)
    if not binary:
        return Result(False, [Check("binary_found", False, f"not found: {binary_arg or DEFAULT_BINARY}")], {})
    repo = repo.expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        return Result(False, [Check("repo_exists", False, str(repo))], {})
    cache_dir = home / "cache" / "codebase-memory-mcp"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CBM_CACHE_DIR"] = str(cache_dir)
    idx_ok, idx_payload, idx_err = run_json([binary, "cli", "index_repository", json.dumps({"repo_path": str(repo), "mode": mode})], env=env, timeout=300)
    checks.append(Check("index_repository", idx_ok, idx_err or json.dumps(idx_payload, ensure_ascii=False)[:500]))
    if not idx_ok:
        return Result(False, checks, {"cache_dir": str(cache_dir)})
    project = project_name_from_index(idx_payload, repo)
    list_ok, list_payload, list_err = run_json([binary, "cli", "list_projects", "{}"], env=env, timeout=60)
    checks.append(Check("list_projects", list_ok and project in json.dumps(list_payload, ensure_ascii=False), list_err or json.dumps(list_payload, ensure_ascii=False)[:500]))
    q = query or repo.name
    search_ok, search_payload, search_err = run_json([binary, "cli", "search_graph", json.dumps({"project": project, "query": q, "limit": 5})], env=env, timeout=60)
    checks.append(Check("search_graph", search_ok, search_err or json.dumps(search_payload, ensure_ascii=False)[:500]))
    trace_payload = None
    if function_name:
        trace_ok, trace_payload, trace_err = run_json([binary, "cli", "trace_path", json.dumps({"project": project, "function_name": function_name, "direction": "both", "depth": 3})], env=env, timeout=60)
        checks.append(Check("trace_path", trace_ok, trace_err or json.dumps(trace_payload, ensure_ascii=False)[:500]))
    return Result(all(c.ok for c in checks), checks, {"project": project, "cache_dir": str(cache_dir), "index": idx_payload, "search": search_payload, "trace": trace_payload})


def print_result(result: Result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return
    print(("PASS" if result.ok else "FAIL") + f" checks={len(result.checks)}")
    for c in result.checks:
        print(f"- {'OK' if c.ok else 'FAIL'} {c.name}: {c.detail}")
    if result.data:
        print(json.dumps(result.data, ensure_ascii=False, indent=2)[:4000])


def cmd_codegraph(args) -> int:
    """Dispatch the `hermes codegraph` argparse namespace."""
    home = hermes_home_arg(getattr(args, "home", None))
    command = getattr(args, "codegraph_command", None)
    if command in {None, "list", "ls"}:
        result = list_status(getattr(args, "binary", None), home)
        print_result(result, bool(getattr(args, "json", False)))
        return 0
    if command == "doctor":
        result = doctor(getattr(args, "binary", None), home)
        print_result(result, bool(getattr(args, "json", False)))
        return 0 if result.ok else 2
    if command == "enable":
        result = enable(
            getattr(args, "binary", None),
            home,
            dry_run=bool(getattr(args, "dry_run", False)),
            force=bool(getattr(args, "force", False)),
            merge_config=bool(getattr(args, "merge_config", False)),
        )
        print_result(result, bool(getattr(args, "json", False)))
        return 0 if result.ok else 2
    if command == "smoke":
        result = smoke(
            getattr(args, "binary", None),
            home,
            Path(getattr(args, "repo")),
            query=getattr(args, "query", None),
            function_name=getattr(args, "function_name", None),
            mode=getattr(args, "mode", "fast"),
        )
        print_result(result, bool(getattr(args, "json", False)))
        return 0 if result.ok else 2
    print("Unknown codegraph command", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes codegraph manager for codebase-memory-mcp")
    parser.add_argument("--home", default=None, help="Hermes home (default: HERMES_HOME or ~/.hermes)")
    parser.add_argument("--binary", default=None, help="codebase-memory-mcp binary path/name")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ["list", "doctor"]:
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("enable")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--merge-config", action="store_true", help="write mcp_servers.codebase_memory into config.yaml with backup")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("smoke")
    p.add_argument("--repo", required=True)
    p.add_argument("--query")
    p.add_argument("--function-name")
    p.add_argument("--mode", default="fast", choices=["fast", "moderate", "full"])
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    home = hermes_home_arg(args.home)
    if args.cmd == "list":
        result = list_status(args.binary, home)
        print_result(result, args.json)
        return 0
    if args.cmd == "doctor":
        result = doctor(args.binary, home)
        print_result(result, args.json)
        return 0 if result.ok else 2
    if args.cmd == "enable":
        result = enable(args.binary, home, dry_run=args.dry_run, force=args.force, merge_config=args.merge_config)
        print_result(result, args.json)
        return 0 if result.ok else 2
    if args.cmd == "smoke":
        result = smoke(args.binary, home, Path(args.repo), query=args.query, function_name=args.function_name, mode=args.mode)
        print_result(result, args.json)
        return 0 if result.ok else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
