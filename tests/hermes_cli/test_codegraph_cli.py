from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from hermes_cli.codegraph import cmd_codegraph, install_binary, list_status
from hermes_cli._parser import build_top_level_parser
from hermes_cli.subcommands.codegraph import build_codegraph_parser


def _fake_binary(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('codebase-memory-mcp fake')\n"
        "    raise SystemExit(0)\n"
        "tool = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == 'cli' else ''\n"
        "payload = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}\n"
        "if tool == 'index_repository': print(json.dumps({'project':'proj','status':'indexed'}))\n"
        "elif tool == 'list_projects': print(json.dumps({'projects':['proj']}))\n"
        "elif tool == 'search_graph': print(json.dumps({'results':[{'name':'handler'}]}))\n"
        "elif tool == 'trace_path': print(json.dumps({'paths':[{'caller':'handler'}]}))\n"
        "else: print(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _parser():
    parser, subparsers, _chat = build_top_level_parser()
    build_codegraph_parser(subparsers, cmd_codegraph=cmd_codegraph)
    return parser


def test_codegraph_parser_accepts_list_doctor_enable_smoke(tmp_path):
    parser = _parser()
    assert parser.parse_args(["codegraph", "list"]).codegraph_command == "list"
    assert parser.parse_args(["codegraph", "install", "--dry-run"]).codegraph_command == "install"
    assert parser.parse_args(["codegraph", "doctor", "--json"]).codegraph_command == "doctor"
    assert parser.parse_args(["codegraph", "enable", "--dry-run"]).dry_run is True
    parsed = parser.parse_args(["codegraph", "smoke", "--repo", str(tmp_path), "--mode", "fast"])
    assert parsed.codegraph_command == "smoke"
    assert parsed.repo == str(tmp_path)


def test_list_status_reports_activation_and_merge_state(tmp_path):
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "state").mkdir()
    (home / "config" / "codegraph-mcp.yaml").write_text("mcp_servers: {}\n", encoding="utf-8")
    (home / "state" / "codegraph-activation.json").write_text('{"enabled": false}\n', encoding="utf-8")
    (home / "config.yaml").write_text("mcp_servers:\n  codebase_memory:\n    command: cbm\n", encoding="utf-8")
    binary = _fake_binary(tmp_path / "codebase-memory-mcp")

    result = list_status(str(binary), home)

    assert result.ok is True
    assert result.data["snippet_exists"] is True
    assert result.data["activation_exists"] is True
    assert result.data["merged_in_config"] is True


def test_cmd_codegraph_enable_dry_run_outputs_json(tmp_path, capsys):
    binary = _fake_binary(tmp_path / "codebase-memory-mcp")
    parser = _parser()
    args = parser.parse_args([
        "codegraph",
        "--home",
        str(tmp_path / "home"),
        "--binary",
        str(binary),
        "enable",
        "--dry-run",
        "--json",
    ])

    rc = cmd_codegraph(args)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["data"]["operations"]
    assert not (tmp_path / "home" / "config" / "codegraph-mcp.yaml").exists()


def test_parse_checksums_and_install_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_cli.codegraph.latest_release_tag", lambda: "v1.2.3")
    monkeypatch.setattr("hermes_cli.codegraph.detect_asset_name", lambda include_ui=False: "codebase-memory-mcp-darwin-arm64.tar.gz")
    monkeypatch.setattr(
        "hermes_cli.codegraph._download_text",
        lambda url: "a" * 64 + "  codebase-memory-mcp-darwin-arm64.tar.gz\n",
    )

    result = install_binary(tmp_path / "home", tag=None, asset=None, include_ui=False, dry_run=True, force=False)

    assert result.ok is True
    assert result.data["tag"] == "v1.2.3"
    assert result.data["asset"] == "codebase-memory-mcp-darwin-arm64.tar.gz"
    assert any(c.name == "download_skipped" and c.ok for c in result.checks)
    assert not (tmp_path / "home" / "bin" / "codebase-memory-mcp").exists()


def test_install_dry_run_refuses_unknown_checksum(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_cli.codegraph.latest_release_tag", lambda: "v1.2.3")
    monkeypatch.setattr("hermes_cli.codegraph.detect_asset_name", lambda include_ui=False: "missing.tar.gz")
    monkeypatch.setattr("hermes_cli.codegraph._download_text", lambda url: "b" * 64 + "  other.tar.gz\n")

    result = install_binary(tmp_path / "home", tag=None, asset=None, include_ui=False, dry_run=True, force=False)

    assert result.ok is False
    assert any(c.name == "checksum_available" and not c.ok for c in result.checks)
