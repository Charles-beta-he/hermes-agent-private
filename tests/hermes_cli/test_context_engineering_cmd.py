from __future__ import annotations

import json
from types import SimpleNamespace


def _write_packet(path, *, evidence_ids=("src-1",), validation_refs=("src-1",), authority=None):
    packet = {
        "schema": "hermes_context_packet_v1",
        "objective": {"goal": "Prove edge context packet lint", "non_goals": ["runtime mutation"]},
        "sources": [{"id": "src-1", "kind": "local_file", "locator": "fixtures/source.md", "claim": "source claim", "evidence": "readback"}],
        "constraints": {"red_lines": ["do not expand authority"], "negative_cases": ["missing readback"], "memory_policy": "do_not_save_wip"},
        "skills": [{"name": "context-engineering-os", "reason": "governs context packet"}],
        "authority": authority or {"external_dispatch": False, "publish_deploy_push": False, "destructive": False, "credential_entry": False},
        "validation": {"required_checks": [{"id": "lint", "command": "context lint", "evidence_refs": list(validation_refs)}]},
        "evidence_map": [{"id": evidence_id, "source_id": evidence_id, "claim": "claim", "verification": "readback"} for evidence_id in evidence_ids],
    }
    path.write_text(json.dumps(packet, indent=2), encoding="utf-8")


def test_context_packet_lint_accepts_valid_packet(tmp_path, capsys):
    from hermes_cli.context_engineering import cmd_context

    packet = tmp_path / "packet.json"
    _write_packet(packet)

    rc = cmd_context(SimpleNamespace(context_command="packet", packet_command="lint", packet_path=str(packet), json=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "PASS" in out
    assert "sources=1" in out
    assert "closed_authority=4" in out


def test_context_packet_lint_rejects_unknown_evidence_ref(tmp_path, capsys):
    from hermes_cli.context_engineering import cmd_context

    packet = tmp_path / "packet.json"
    _write_packet(packet, validation_refs=("missing",))

    rc = cmd_context(SimpleNamespace(context_command="packet", packet_command="lint", packet_path=str(packet), json=False))
    err = capsys.readouterr().err

    assert rc == 1
    assert "unknown evidence_refs: missing" in err


def test_context_eval_run_checks_pass_and_fail_fixtures(tmp_path, capsys):
    from hermes_cli.context_engineering import cmd_context

    root = tmp_path
    pass_dir = root / "eval" / "fixtures" / "pass"
    fail_dir = root / "eval" / "fixtures" / "fail"
    pass_dir.mkdir(parents=True)
    fail_dir.mkdir(parents=True)
    _write_packet(pass_dir / "valid.json")
    _write_packet(fail_dir / "invalid.json", validation_refs=("missing",))

    rc = cmd_context(SimpleNamespace(context_command="eval", eval_command="run", root=str(root), json=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "PASS_EXPECT valid.json: exit=0" in out
    assert "FAIL_EXPECT invalid.json: exit=1" in out
    assert "REGRESSION PASS checked=2" in out


def test_context_eval_run_fails_when_fail_fixture_passes(tmp_path, capsys):
    from hermes_cli.context_engineering import cmd_context

    root = tmp_path
    fail_dir = root / "eval" / "fixtures" / "fail"
    fail_dir.mkdir(parents=True)
    _write_packet(fail_dir / "unexpected-pass.json")

    rc = cmd_context(SimpleNamespace(context_command="eval", eval_command="run", root=str(root), json=False))
    err = capsys.readouterr().err

    assert rc == 1
    assert "FAIL_EXPECT unexpected-pass.json: exit=0" in err


def test_context_parser_is_registered():
    from hermes_cli._parser import build_top_level_parser
    from hermes_cli.subcommands.context import build_context_parser

    parser, subparsers, _chat = build_top_level_parser()
    build_context_parser(subparsers, cmd_context=lambda args: 0)

    args = parser.parse_args(["context", "packet", "lint", "packet.json"])
    assert args.context_command == "packet"
    assert args.packet_command == "lint"
    assert args.packet_path == "packet.json"
