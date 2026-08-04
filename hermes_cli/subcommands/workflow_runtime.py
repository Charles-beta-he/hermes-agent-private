"""Default-off CLI surface for the governed local workflow runtime."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, cast


def _load_json(path: str) -> object:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_mapping(path: str) -> Mapping[str, Any]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return cast(dict[str, Any], value)


def _emit(payload: dict[str, Any], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key in (
            "mode",
            "artifact_id",
            "revision",
            "artifact_digest",
            "run_id",
            "status",
            "execution_enabled",
            "verification_state",
            "convergence_state",
        ):
            if key in payload:
                print(f"{key}: {payload[key]}")
    return 0


def _emit_error(code: str, message: str, *, as_json: bool) -> int:
    payload = {"ok": False, "error": {"code": code, "message": message}}
    if as_json:
        print(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            file=sys.stderr,
        )
    else:
        print(f"workflow-runtime error [{code}]: {message}", file=sys.stderr)
    return 2


def _command_error(
    args: argparse.Namespace, code: str, message: str, *, as_json: bool
) -> int:
    rc = _emit_error(code, message, as_json=as_json)
    if bool(getattr(args, "_workflow_runtime_cli", False)):
        raise SystemExit(rc)
    return rc


def _command_result(
    args: argparse.Namespace, payload: dict[str, Any], *, as_json: bool
) -> int:
    _emit(payload, as_json=as_json)
    rc = (
        0
        if payload.get("outcome") == "completed"
        and payload.get("verification_state") == "verified"
        else 1
    )
    if rc and bool(getattr(args, "_workflow_runtime_cli", False)):
        raise SystemExit(rc)
    return rc


def _now() -> str:
    return (
        datetime
        .now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def cmd_workflow_runtime(args: argparse.Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    try:
        from workflow_kernel.governed_runtime import (
            ExecuteGrant,
            WorkflowRuntimeError,
            compile_runtime_preview,
            read_runtime_status,
            run_workflow,
        )
        from workflow_kernel.workflow_definition import WorkflowDefinitionError
        from workflow_kernel.workflow_artifact_ledger import (
            WorkflowArtifactLedgerError,
            apply_workflow_mutation,
            initialize_workflow_artifact,
            read_workflow_artifact,
            read_workflow_artifact_history,
            validate_workflow_mutation,
        )
    except ImportError as exc:
        return _command_error(args, "runtime_unavailable", str(exc), as_json=as_json)

    action = getattr(args, "workflow_runtime_action", None) or "compile"
    try:
        def load_definition() -> Mapping[str, Any]:
            definition_path = getattr(args, "definition", None)
            if definition_path:
                return _load_mapping(definition_path)
            artifact_ledger = getattr(args, "artifact_ledger", None)
            artifact_private_root = getattr(args, "artifact_private_root", None)
            if not artifact_ledger or not artifact_private_root:
                raise ValueError(
                    "artifact definition source requires --artifact-ledger and "
                    "--artifact-private-root"
                )
            definition = read_workflow_artifact(
                Path(artifact_ledger).expanduser(),
                private_root=Path(artifact_private_root).expanduser(),
            )["definition"]
            if not isinstance(definition, dict):
                raise ValueError("artifact projection contains no workflow definition")
            return cast(dict[str, Any], definition)

        if action.startswith("artifact-"):
            artifact_ledger = Path(args.artifact_ledger).expanduser()
            private_root = Path(args.private_root).expanduser()
            if action == "artifact-status":
                return _emit(
                    read_workflow_artifact(
                        artifact_ledger, private_root=private_root
                    ),
                    as_json=as_json,
                )
            if action == "artifact-history":
                return _emit(
                    {
                        "schema_version": "workflow_artifact_history_v1",
                        "events": read_workflow_artifact_history(
                            artifact_ledger, private_root=private_root
                        ),
                    },
                    as_json=as_json,
                )
            if action == "artifact-validate":
                return _emit(
                    validate_workflow_mutation(
                        _load_mapping(args.mutation),
                        ledger_path=artifact_ledger,
                        private_root=private_root,
                    ),
                    as_json=as_json,
                )
            if not bool(getattr(args, "write_local", False)):
                return _command_error(
                    args,
                    "write_local_flag_required",
                    "artifact init/apply is default-off; pass --write-local for this invocation",
                    as_json=as_json,
                )
            if action == "artifact-init":
                return _emit(
                    initialize_workflow_artifact(
                        _load_mapping(args.definition),
                        ledger_path=artifact_ledger,
                        private_root=private_root,
                        idempotency_key=args.idempotency_key,
                        now=_now(),
                    ),
                    as_json=as_json,
                )
            if action == "artifact-apply":
                return _emit(
                    apply_workflow_mutation(
                        _load_mapping(args.mutation),
                        ledger_path=artifact_ledger,
                        private_root=private_root,
                        now=_now(),
                    ),
                    as_json=as_json,
                )
            raise ValueError(f"unsupported artifact action: {action}")
        if action == "compile":
            return _emit(
                compile_runtime_preview(
                    load_definition(),
                    _load_json(args.manifest),
                ),
                as_json=as_json,
            )
        # Preserve caller-controlled path components so descriptor-relative
        # no-follow validation in the runtime can reject symlinked leaves and
        # ancestors. The runtime canonicalizes only known macOS top-level
        # aliases such as /tmp -> /private/tmp.
        ledger_path = Path(args.ledger).expanduser()
        private_root = Path(args.private_root).expanduser()
        if action == "status":
            return _emit(
                read_runtime_status(ledger_path, private_root=private_root),
                as_json=as_json,
            )
        if not bool(getattr(args, "execute_local", False)):
            return _command_error(
                args,
                "execute_local_flag_required",
                "run/resume is default-off; pass --execute-local for this invocation",
                as_json=as_json,
            )
        result = run_workflow(
            load_definition(),
            _load_json(args.manifest),
            ledger_path=ledger_path,
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            resume=action == "resume",
            now=_now(),
        )
        return _command_result(args, result, as_json=as_json)
    except WorkflowRuntimeError as exc:
        return _command_error(args, exc.code, str(exc), as_json=as_json)
    except WorkflowDefinitionError as exc:
        return _command_error(args, exc.code, str(exc), as_json=as_json)
    except WorkflowArtifactLedgerError as exc:
        return _command_error(args, exc.code, str(exc), as_json=as_json)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return _command_error(args, "input_invalid", str(exc), as_json=as_json)


def _add_definition_source(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--definition", help="WorkflowDefinition v1 JSON path")
    source.add_argument(
        "--artifact-ledger",
        help="Replay the current WorkflowDefinition from this artifact JSONL ledger",
    )
    parser.add_argument(
        "--artifact-private-root",
        help="Private directory containing --artifact-ledger",
    )


def _add_contract_paths(parser: argparse.ArgumentParser) -> None:
    _add_definition_source(parser)
    parser.add_argument(
        "--manifest", required=True, help="Governed runtime manifest JSON path"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )


def _add_execution_paths(parser: argparse.ArgumentParser) -> None:
    _add_contract_paths(parser)
    parser.add_argument(
        "--ledger", required=True, help="Exact canonical execution-ledger JSONL path"
    )
    parser.add_argument(
        "--private-root", required=True, help="Private directory containing the ledger"
    )
    parser.add_argument(
        "--execute-local",
        action="store_true",
        help="Grant this invocation trusted-pure local execution only",
    )


def build_workflow_runtime_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "workflow-runtime",
        help="Compile or execute a governed local-only WorkflowDefinition",
        description=(
            "Default-off governed workflow runtime. Execution is limited to trusted-pure "
            "local adapters and requires --execute-local on every run/resume."
        ),
    )
    parser.set_defaults(_workflow_runtime_cli=True)
    actions = parser.add_subparsers(dest="workflow_runtime_action", required=True)

    compile_parser = actions.add_parser(
        "compile", help="Validate and preview without writing state"
    )
    _add_contract_paths(compile_parser)
    compile_parser.set_defaults(func=cmd_workflow_runtime)

    run_parser = actions.add_parser("run", help="Start one exact local-only run")
    _add_execution_paths(run_parser)
    run_parser.set_defaults(func=cmd_workflow_runtime)

    resume_parser = actions.add_parser(
        "resume", help="Resume only under an identical bound identity"
    )
    _add_execution_paths(resume_parser)
    resume_parser.set_defaults(func=cmd_workflow_runtime)

    status_parser = actions.add_parser(
        "status", help="Project status from the canonical ledger"
    )
    status_parser.add_argument(
        "--ledger", required=True, help="Exact execution-ledger JSONL path"
    )
    status_parser.add_argument(
        "--private-root", required=True, help="Private directory containing the ledger"
    )
    status_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    status_parser.set_defaults(func=cmd_workflow_runtime)

    def add_artifact_paths(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--artifact-ledger", required=True, help="WorkflowArtifact JSONL ledger"
        )
        target.add_argument(
            "--private-root", required=True, help="Private directory containing the ledger"
        )
        target.add_argument(
            "--json", action="store_true", help="Emit machine-readable JSON"
        )

    artifact_init = actions.add_parser(
        "artifact-init", help="Initialize a versioned WorkflowArtifact ledger"
    )
    artifact_init.add_argument("--definition", required=True)
    artifact_init.add_argument("--idempotency-key", required=True)
    artifact_init.add_argument("--write-local", action="store_true")
    add_artifact_paths(artifact_init)
    artifact_init.set_defaults(func=cmd_workflow_runtime)

    artifact_validate = actions.add_parser(
        "artifact-validate", help="Validate one typed mutation without writing"
    )
    artifact_validate.add_argument("--mutation", required=True)
    add_artifact_paths(artifact_validate)
    artifact_validate.set_defaults(func=cmd_workflow_runtime)

    artifact_apply = actions.add_parser(
        "artifact-apply", help="Commit one typed mutation with read-back verification"
    )
    artifact_apply.add_argument("--mutation", required=True)
    artifact_apply.add_argument("--write-local", action="store_true")
    add_artifact_paths(artifact_apply)
    artifact_apply.set_defaults(func=cmd_workflow_runtime)

    artifact_status = actions.add_parser(
        "artifact-status", help="Replay the current WorkflowArtifact projection"
    )
    add_artifact_paths(artifact_status)
    artifact_status.set_defaults(func=cmd_workflow_runtime)

    artifact_history = actions.add_parser(
        "artifact-history", help="Read the validated append-only artifact history"
    )
    add_artifact_paths(artifact_history)
    artifact_history.set_defaults(func=cmd_workflow_runtime)
