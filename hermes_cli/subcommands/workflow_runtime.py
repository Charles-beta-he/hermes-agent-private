"""Default-off CLI surface for the governed local workflow runtime."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def _load_json(path: str) -> object:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _emit(payload: dict[str, Any], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key in (
            "mode",
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
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
    else:
        print(f"workflow-runtime error [{code}]: {message}", file=sys.stderr)
    return 2


def _command_error(args: argparse.Namespace, code: str, message: str, *, as_json: bool) -> int:
    rc = _emit_error(code, message, as_json=as_json)
    if bool(getattr(args, "_workflow_runtime_cli", False)):
        raise SystemExit(rc)
    return rc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
    except ImportError as exc:
        return _command_error(args, "runtime_unavailable", str(exc), as_json=as_json)

    action = getattr(args, "workflow_runtime_action", None) or "compile"
    try:
        if action == "compile":
            return _emit(
                compile_runtime_preview(
                    _load_json(args.definition),
                    _load_json(args.manifest),
                ),
                as_json=as_json,
            )
        ledger_path = Path(args.ledger).expanduser().resolve(strict=False)
        private_root = Path(args.private_root).expanduser().resolve()
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
            _load_json(args.definition),
            _load_json(args.manifest),
            ledger_path=ledger_path,
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            resume=action == "resume",
            now=_now(),
        )
        return _emit(result, as_json=as_json)
    except WorkflowRuntimeError as exc:
        return _command_error(args, exc.code, str(exc), as_json=as_json)
    except WorkflowDefinitionError as exc:
        return _command_error(args, exc.code, str(exc), as_json=as_json)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return _command_error(args, "input_invalid", str(exc), as_json=as_json)


def _add_contract_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--definition", required=True, help="WorkflowDefinition v1 JSON path")
    parser.add_argument("--manifest", required=True, help="Governed runtime manifest JSON path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def _add_execution_paths(parser: argparse.ArgumentParser) -> None:
    _add_contract_paths(parser)
    parser.add_argument("--ledger", required=True, help="Exact canonical execution-ledger JSONL path")
    parser.add_argument("--private-root", required=True, help="Private directory containing the ledger")
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

    compile_parser = actions.add_parser("compile", help="Validate and preview without writing state")
    _add_contract_paths(compile_parser)
    compile_parser.set_defaults(func=cmd_workflow_runtime)

    run_parser = actions.add_parser("run", help="Start one exact local-only run")
    _add_execution_paths(run_parser)
    run_parser.set_defaults(func=cmd_workflow_runtime)

    resume_parser = actions.add_parser("resume", help="Resume only under an identical bound identity")
    _add_execution_paths(resume_parser)
    resume_parser.set_defaults(func=cmd_workflow_runtime)

    status_parser = actions.add_parser("status", help="Project status from the canonical ledger")
    status_parser.add_argument("--ledger", required=True, help="Exact execution-ledger JSONL path")
    status_parser.add_argument("--private-root", required=True, help="Private directory containing the ledger")
    status_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    status_parser.set_defaults(func=cmd_workflow_runtime)
