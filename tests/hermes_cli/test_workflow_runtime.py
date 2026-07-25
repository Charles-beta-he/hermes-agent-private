from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli.subcommands import workflow_runtime as subcommand
from tests.workflow_kernel.test_governed_runtime import _definition, _manifest


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_parser_module_import_does_not_eagerly_load_runtime_kernel() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import hermes_cli.subcommands.workflow_runtime; "
                "assert 'workflow_kernel.governed_runtime' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )

    assert result.returncode == 0, result.stderr


def test_runtime_import_failure_is_structured(monkeypatch, capsys) -> None:
    original_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name == "workflow_kernel.governed_runtime":
            raise ImportError("runtime dependency unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    rc = subcommand.cmd_workflow_runtime(argparse.Namespace(json=True))

    assert rc == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "runtime_unavailable"


def test_text_status_does_not_invent_missing_governance_fields(capsys) -> None:
    rc = subcommand._emit(
        {
            "run_id": "run-text-status",
            "status": "run_completed",
        },
        as_json=False,
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "run_id: run-text-status" in output
    assert "status: run_completed" in output
    assert "execution_enabled" not in output
    assert "verification_state" not in output
    assert "convergence_state" not in output


def test_compile_command_is_read_only(tmp_path: Path, capsys) -> None:
    definition = tmp_path / "definition.json"
    manifest = tmp_path / "manifest.json"
    ledger = tmp_path / "run.jsonl"
    _write_json(definition, _definition())
    _write_json(manifest, _manifest())

    rc = subcommand.cmd_workflow_runtime(
        argparse.Namespace(
            workflow_runtime_action="compile",
            definition=str(definition),
            manifest=str(manifest),
            ledger=str(ledger),
            private_root=str(tmp_path),
            execute_local=False,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["mode"] == "compile_preview_only"
    assert payload["execution_enabled"] is False
    assert not ledger.exists()


def test_run_command_requires_explicit_execute_local_flag(
    tmp_path: Path, capsys
) -> None:
    definition = tmp_path / "definition.json"
    manifest = tmp_path / "manifest.json"
    ledger = tmp_path / "run.jsonl"
    _write_json(definition, _definition())
    _write_json(manifest, _manifest())

    rc = subcommand.cmd_workflow_runtime(
        argparse.Namespace(
            workflow_runtime_action="run",
            definition=str(definition),
            manifest=str(manifest),
            ledger=str(ledger),
            private_root=str(tmp_path),
            execute_local=False,
            json=True,
        )
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert rc == 2
    assert payload["error"]["code"] == "execute_local_flag_required"
    assert not ledger.exists()


def test_run_status_and_resume_use_canonical_ledger(tmp_path: Path, capsys) -> None:
    definition = tmp_path / "definition.json"
    manifest = tmp_path / "manifest.json"
    ledger = tmp_path / "run.jsonl"
    _write_json(definition, _definition())
    _write_json(manifest, _manifest())
    common = {
        "definition": str(definition),
        "manifest": str(manifest),
        "ledger": str(ledger),
        "private_root": str(tmp_path),
        "execute_local": True,
        "json": True,
    }

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(workflow_runtime_action="run", **common)
        )
        == 0
    )
    run_payload = json.loads(capsys.readouterr().out)
    assert run_payload["status"] == "run_completed"

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="status",
                ledger=str(ledger),
                private_root=str(tmp_path),
                json=True,
            )
        )
        == 0
    )
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload == run_payload

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(workflow_runtime_action="resume", **common)
        )
        == 0
    )
    resume_payload = json.loads(capsys.readouterr().out)
    assert resume_payload == run_payload


def test_run_command_returns_nonzero_for_unverified_workflow(
    tmp_path: Path, capsys
) -> None:
    definition = tmp_path / "definition.json"
    manifest = tmp_path / "manifest.json"
    ledger = tmp_path / "run.jsonl"
    manifest_value = _manifest()
    manifest_value["leaves"]["implement"]["output_schema"] = {"type": "string"}
    _write_json(definition, _definition())
    _write_json(manifest, manifest_value)

    rc = subcommand.cmd_workflow_runtime(
        argparse.Namespace(
            workflow_runtime_action="run",
            definition=str(definition),
            manifest=str(manifest),
            ledger=str(ledger),
            private_root=str(tmp_path),
            execute_local=True,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "run_completed"
    assert payload["verification_state"] == "unverified"
    assert rc == 1


def test_parser_registers_default_off_workflow_runtime_surface() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    subcommand.build_workflow_runtime_parser(subparsers)

    args = parser.parse_args([
        "workflow-runtime",
        "run",
        "--definition",
        "definition.json",
        "--manifest",
        "manifest.json",
        "--ledger",
        "run.jsonl",
        "--private-root",
        ".",
    ])
    assert args.workflow_runtime_action == "run"
    assert args.execute_local is False
    assert args._workflow_runtime_cli is True


def test_registered_cli_failure_exits_nonzero_without_creating_ledger(
    tmp_path: Path,
) -> None:
    definition = tmp_path / "definition.json"
    manifest = tmp_path / "manifest.json"
    ledger = tmp_path / "run.jsonl"
    _write_json(definition, _definition())
    _write_json(manifest, _manifest())
    parser = argparse.ArgumentParser()
    subcommand.build_workflow_runtime_parser(
        parser.add_subparsers(dest="command", required=True)
    )
    args = parser.parse_args([
        "workflow-runtime",
        "run",
        "--definition",
        str(definition),
        "--manifest",
        str(manifest),
        "--ledger",
        str(ledger),
        "--private-root",
        str(tmp_path),
        "--json",
    ])

    with pytest.raises(SystemExit) as excinfo:
        args.func(args)

    assert excinfo.value.code == 2
    assert not ledger.exists()


@pytest.mark.parametrize("action", ["run", "status", "resume"])
def test_cli_rejects_symlinked_ledger_without_touching_target(
    action: str, tmp_path: Path, capsys
) -> None:
    definition = tmp_path / "definition.json"
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target.jsonl"
    alias = tmp_path / "alias.jsonl"
    _write_json(definition, _definition())
    _write_json(manifest, _manifest())
    target.write_text("sentinel\n", encoding="utf-8")
    target.chmod(0o600)
    alias.symlink_to(target)

    before = (target.read_bytes(), target.stat().st_mode)
    args = argparse.Namespace(
        workflow_runtime_action=action,
        definition=str(definition),
        manifest=str(manifest),
        ledger=str(alias),
        private_root=str(tmp_path),
        execute_local=True,
        json=True,
    )

    assert subcommand.cmd_workflow_runtime(args) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "input_invalid"
    assert alias.is_symlink()
    assert (target.read_bytes(), target.stat().st_mode) == before


@pytest.mark.parametrize("action", ["run", "status", "resume"])
def test_cli_rejects_symlinked_runtime_root_without_touching_target(
    action: str, tmp_path: Path, capsys
) -> None:
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    actual_root.chmod(0o700)
    target = actual_root / "run.jsonl"
    target.write_text("sentinel\n", encoding="utf-8")
    target.chmod(0o600)
    alias_root = tmp_path / "alias"
    alias_root.symlink_to(actual_root, target_is_directory=True)
    definition = tmp_path / "definition.json"
    manifest = tmp_path / "manifest.json"
    _write_json(definition, _definition())
    _write_json(manifest, _manifest())

    before = (target.read_bytes(), target.stat().st_mode, actual_root.stat().st_mode)
    args = argparse.Namespace(
        workflow_runtime_action=action,
        definition=str(definition),
        manifest=str(manifest),
        ledger=str(alias_root / "run.jsonl"),
        private_root=str(alias_root),
        execute_local=True,
        json=True,
    )

    assert subcommand.cmd_workflow_runtime(args) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "input_invalid"
    assert alias_root.is_symlink()
    assert (
        target.read_bytes(),
        target.stat().st_mode,
        actual_root.stat().st_mode,
    ) == before


def test_artifact_cli_requires_explicit_write_local(tmp_path: Path, capsys) -> None:
    definition = tmp_path / "definition.json"
    ledger = tmp_path / "artifact.jsonl"
    _write_json(definition, _definition())

    rc = subcommand.cmd_workflow_runtime(
        argparse.Namespace(
            workflow_runtime_action="artifact-init",
            definition=str(definition),
            artifact_ledger=str(ledger),
            private_root=str(tmp_path),
            idempotency_key="init-1",
            write_local=False,
            json=True,
        )
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "write_local_flag_required"
    assert not ledger.exists()
    assert not Path(f"{ledger}.lock").exists()


def test_artifact_cli_init_validate_apply_status_and_history(
    tmp_path: Path, capsys
) -> None:
    definition_path = tmp_path / "definition.json"
    mutation_path = tmp_path / "mutation.json"
    ledger = tmp_path / "artifact.jsonl"
    definition = _definition()
    _write_json(definition_path, definition)

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="artifact-init",
                definition=str(definition_path),
                artifact_ledger=str(ledger),
                private_root=str(tmp_path),
                idempotency_key="init-1",
                write_local=True,
                json=True,
            )
        )
        == 0
    )
    init_receipt = json.loads(capsys.readouterr().out)
    assert init_receipt["readback_verified"] is True
    assert init_receipt["new_revision"] == 1

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="artifact-status",
                artifact_ledger=str(ledger),
                private_root=str(tmp_path),
                json=True,
            )
        )
        == 0
    )
    state = json.loads(capsys.readouterr().out)
    mutation = {
        "schema_version": "workflow_mutation_v1",
        "artifact_id": state["artifact_id"],
        "base_revision": state["revision"],
        "base_artifact_digest": state["artifact_digest"],
        "idempotency_key": "mutation-1",
        "operations": [
            {
                "op": "set_metadata",
                "title": "Governed workflow v2",
                "goal": definition["goal"],
                "non_goals": definition["non_goals"],
                "constraints": definition["constraints"],
            }
        ],
    }
    _write_json(mutation_path, mutation)
    before = ledger.read_bytes()

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="artifact-validate",
                mutation=str(mutation_path),
                artifact_ledger=str(ledger),
                private_root=str(tmp_path),
                json=True,
            )
        )
        == 0
    )
    validation = json.loads(capsys.readouterr().out)
    assert validation["valid"] is True
    assert validation["candidate_revision"] == 2
    assert ledger.read_bytes() == before

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="artifact-apply",
                mutation=str(mutation_path),
                artifact_ledger=str(ledger),
                private_root=str(tmp_path),
                write_local=True,
                json=True,
            )
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["new_revision"] == 2
    assert receipt["readback_verified"] is True

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="artifact-history",
                artifact_ledger=str(ledger),
                private_root=str(tmp_path),
                json=True,
            )
        )
        == 0
    )
    history = json.loads(capsys.readouterr().out)
    assert [event["event_type"] for event in history["events"]] == [
        "artifact_initialized",
        "mutation_committed",
    ]


def test_compile_can_read_definition_from_artifact_ledger(
    tmp_path: Path, capsys
) -> None:
    definition_path = tmp_path / "definition.json"
    manifest_path = tmp_path / "manifest.json"
    ledger = tmp_path / "artifact.jsonl"
    _write_json(definition_path, _definition())
    _write_json(manifest_path, _manifest())
    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="artifact-init",
                definition=str(definition_path),
                artifact_ledger=str(ledger),
                private_root=str(tmp_path),
                idempotency_key="init-1",
                write_local=True,
                json=True,
            )
        )
        == 0
    )
    capsys.readouterr()

    assert (
        subcommand.cmd_workflow_runtime(
            argparse.Namespace(
                workflow_runtime_action="compile",
                definition=None,
                artifact_ledger=str(ledger),
                artifact_private_root=str(tmp_path),
                manifest=str(manifest_path),
                json=True,
            )
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "compile_preview_only"
    assert preview["definition_digest"]


def test_parser_registers_artifact_governance_surface() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    subcommand.build_workflow_runtime_parser(subparsers)

    args = parser.parse_args(
        [
            "workflow-runtime",
            "artifact-apply",
            "--mutation",
            "mutation.json",
            "--artifact-ledger",
            "artifact.jsonl",
            "--private-root",
            ".",
            "--write-local",
        ]
    )
    assert args.workflow_runtime_action == "artifact-apply"
    assert args.write_local is True
