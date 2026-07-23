"""Default-off, local-only governed execution runtime for compiled workflows.

Importing this module has no execution side effects. `run_workflow` requires a
fresh, non-persisted ExecuteGrant and only exposes trusted pure local adapters.
"""
from __future__ import annotations

import hashlib
import importlib.resources
import json
import math
import secrets
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, NoReturn, cast

from jsonschema import Draft202012Validator

from workflow_kernel.workflow_definition import compile_workflow_definition_v1
from workflow_kernel.workflow_execution_ledger import (
    WorkflowExecutionLedgerError,
    append_execution_event,
    project_execution_events,
    read_execution_events_read_only,
)


_MANIFEST_SCHEMA = "workflow-runtime-manifest-v1.schema.json"
_RESOURCE_KEYS = ("spawns", "wall_ms", "tool_calls", "tokens", "side_effect_ops")
_SUPPORTED_ISOLATION = "in_process_trusted_pure"
_SIDE_EFFECT_RANK = {
    "none": 0,
    "local_read": 1,
    "local_write": 2,
    "external_read": 3,
    "external_write": 4,
}


class WorkflowRuntimeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowRuntimeError(code, message)


@dataclass
class _GrantTicket:
    lock: Lock = field(default_factory=Lock)
    consumed: bool = False


@dataclass(frozen=True, init=False)
class ExecuteGrant:
    """One-shot invocation capability. It is intentionally never serialized."""

    mode: str
    nonce: str
    _ticket: _GrantTicket = field(repr=False, compare=False)

    @classmethod
    def local_only(cls) -> "ExecuteGrant":
        grant = object.__new__(cls)
        object.__setattr__(grant, "mode", "local_only")
        object.__setattr__(grant, "nonce", secrets.token_hex(16))
        object.__setattr__(grant, "_ticket", _GrantTicket())
        return grant

    def consume(self) -> None:
        """Atomically consume this invocation-owned capability exactly once."""
        with self._ticket.lock:
            if self._ticket.consumed:
                _fail("execute_grant_reused", "ExecuteGrant has already been consumed")
            self._ticket.consumed = True


@dataclass(frozen=True)
class _AdapterResult:
    result: Any
    claims: tuple[str, ...]
    evidence: tuple[dict[str, str], ...]
    usage: dict[str, int]


@dataclass(frozen=True)
class _Adapter:
    adapter_id: str
    capabilities: frozenset[str]
    side_effect: str
    supported_isolations: frozenset[str]
    execute: Callable[[Any, Mapping[str, dict[str, Any]]], _AdapterResult]


def _zero_usage() -> dict[str, int]:
    return {key: 0 for key in _RESOURCE_KEYS}


def _echo_adapter(value: Any, dependencies: Mapping[str, dict[str, Any]]) -> _AdapterResult:
    del dependencies
    return _AdapterResult(
        result=value,
        claims=("trusted pure local echo completed",),
        evidence=({"type": "runtime_receipt", "ref": "adapter:local.echo"},),
        usage=_zero_usage(),
    )


def _verify_dependency_adapter(value: Any, dependencies: Mapping[str, dict[str, Any]]) -> _AdapterResult:
    if not isinstance(value, dict) or set(value) != {"dependency"}:
        raise ValueError("local.verify_dependency input must contain only dependency")
    dependency = value["dependency"]
    if not isinstance(dependency, str) or dependency not in dependencies:
        raise ValueError("local.verify_dependency names an unavailable dependency")
    packet = dependencies[dependency]
    if packet["status"] != "completed" or not packet["value_present"]:
        raise ValueError("dependency does not have a completed value-bearing packet")
    return _AdapterResult(
        result={"verified": True, "dependency": dependency},
        claims=(f"dependency {dependency} has a completed structured packet",),
        evidence=({"type": "runtime_receipt", "ref": f"leaf:{dependency}"},),
        usage=_zero_usage(),
    )


def _builtin_adapters() -> dict[str, _Adapter]:
    common = {
        "capabilities": frozenset(),
        "side_effect": "none",
        "supported_isolations": frozenset({_SUPPORTED_ISOLATION}),
    }
    return {
        "local.echo": _Adapter(adapter_id="local.echo", execute=_echo_adapter, **common),
        "local.verify_dependency": _Adapter(
            adapter_id="local.verify_dependency",
            execute=_verify_dependency_adapter,
            **common,
        ),
    }


@lru_cache(maxsize=1)
def _manifest_validator() -> Draft202012Validator:
    try:
        resource = importlib.resources.files("workflow_kernel.schemas").joinpath(_MANIFEST_SCHEMA)
        schema = json.loads(resource.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        _fail("schema_unavailable", f"runtime manifest schema could not be loaded: {exc}")
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _normalize_json_numbers(value: object) -> object:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_normalize_json_numbers(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _normalize_json_numbers(item) for key, item in value.items()}
    return value


def _compact_json(value: object) -> bytes:
    try:
        return json.dumps(
            _normalize_json_numbers(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("noncanonical_input", str(exc))


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_compact_json(value)).hexdigest()}"


def _validate_local_schema_refs(schema: object, *, leaf_id: str) -> None:
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(value, str) or not value.startswith("#")
            ):
                _fail(
                    "output_schema_remote_ref",
                    f"{leaf_id}: output schema references must be local fragments",
                )
            _validate_local_schema_refs(value, leaf_id=leaf_id)
    elif isinstance(schema, list):
        for value in schema:
            _validate_local_schema_refs(value, leaf_id=leaf_id)


def _validate_manifest(manifest: object) -> dict[str, Any]:
    if isinstance(manifest, dict) and manifest.get("identity_schema_version") != 1:
        _fail("identity_schema_unsupported", "only resume identity schema version 1 is supported")
    errors = sorted(
        _manifest_validator().iter_errors(cast(Any, manifest)),
        key=lambda error: tuple(f"{type(part).__name__}:{part}" for part in error.absolute_path),
    )
    if errors:
        _fail("manifest_schema_invalid", errors[0].message)
    if not isinstance(manifest, dict):
        _fail("manifest_schema_invalid", "manifest must be an object")
    normalized = json.loads(_compact_json(manifest))
    for leaf_id, leaf in normalized["leaves"].items():
        _validate_local_schema_refs(leaf["output_schema"], leaf_id=leaf_id)
        try:
            Draft202012Validator.check_schema(leaf["output_schema"])
        except Exception as exc:
            _fail("output_schema_invalid", f"{leaf_id}: {exc}")
    return normalized


def _prepare(
    definition: object,
    manifest: object,
    *,
    validate_adapters: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, _Adapter]]:
    normalized_manifest = _validate_manifest(manifest)
    plan = compile_workflow_definition_v1(definition)
    task_bindings = {item["task_id"]: item for item in plan["task_bindings"]}
    if set(normalized_manifest["leaves"]) != set(task_bindings):
        missing = sorted(set(task_bindings) - set(normalized_manifest["leaves"]))
        extra = sorted(set(normalized_manifest["leaves"]) - set(task_bindings))
        _fail("leaf_set_mismatch", f"manifest leaf mismatch; missing={missing}, extra={extra}")
    adapters = _builtin_adapters()
    if validate_adapters:
        for leaf_id, leaf in normalized_manifest["leaves"].items():
            if leaf["reserve"]["spawns"] != 1:
                _fail(
                    "reservation_invalid",
                    f"{leaf_id}: each leaf must reserve exactly one spawn",
                )
            adapter = adapters.get(leaf["adapter"])
            if adapter is None:
                _fail("adapter_unsupported", f"{leaf_id}: unsupported adapter {leaf['adapter']!r}")
            if leaf["isolation"] not in adapter.supported_isolations:
                _fail(
                    "isolation_unsupported",
                    f"{leaf_id}: adapter {adapter.adapter_id!r} cannot enforce isolation {leaf['isolation']!r}",
                )
            binding = task_bindings[leaf_id]
            missing_capabilities = adapter.capabilities - set(binding["capabilities"])
            if missing_capabilities:
                _fail(
                    "authority_projection_invalid",
                    f"{leaf_id}: adapter capabilities exceed task authority: {sorted(missing_capabilities)}",
                )
            if _SIDE_EFFECT_RANK[adapter.side_effect] > _SIDE_EFFECT_RANK[binding["side_effect"]]:
                _fail("authority_projection_invalid", f"{leaf_id}: adapter side effect exceeds task authority")
    ordered_leaves = {
        leaf_id: normalized_manifest["leaves"][leaf_id]
        for leaf_id in sorted(normalized_manifest["leaves"])
    }
    identity = {
        "schema_version": "workflow_resume_identity_v1",
        "workflow_digest": plan["definition_digest"],
        "plan_digest": plan["plan_digest"],
        "input_digest": _digest({key: item["input"] for key, item in ordered_leaves.items()}),
        "baseline_ref": normalized_manifest["baseline_ref"],
        "project_scope": normalized_manifest["project_scope"],
        "profile_scope": normalized_manifest["profile_scope"],
        "tool_policy_ref": normalized_manifest["tool_policy_ref"],
        "model_contract_ref": normalized_manifest["model_contract_ref"],
        "authority_ref": normalized_manifest["authority_ref"],
        "isolation_contract_digest": _digest(
            {
                key: {"adapter": item["adapter"], "isolation": item["isolation"]}
                for key, item in ordered_leaves.items()
            }
        ),
        "output_schema_digest": _digest({key: item["output_schema"] for key, item in ordered_leaves.items()}),
        "execution_contract_digest": _digest(
            {
                "hard_budget": normalized_manifest["hard_budget"],
                "max_concurrency": normalized_manifest["max_concurrency"],
                "reservation_ttl_ms": normalized_manifest["reservation_ttl_ms"],
                "reservations": {key: item["reserve"] for key, item in ordered_leaves.items()},
            }
        ),
    }
    preview = {
        "schema_version": "workflow_runtime_preview_v1",
        "mode": "compile_preview_only",
        "execution_enabled": False,
        "run_id": normalized_manifest["run_id"],
        "definition_digest": plan["definition_digest"],
        "plan_digest": plan["plan_digest"],
        "manifest_digest": _digest(normalized_manifest),
        "identity": identity,
        "identity_hash": _digest(identity),
        "task_count": len(task_bindings),
        "max_concurrency": normalized_manifest["max_concurrency"],
        "adapters": sorted({item["adapter"] for item in ordered_leaves.values()}),
        "isolation": sorted({item["isolation"] for item in ordered_leaves.values()}),
    }
    return plan, normalized_manifest, preview, adapters


def compile_runtime_preview(definition: object, manifest: object) -> dict[str, Any]:
    """Compile and validate all local execution contracts without writing state."""
    _, _, preview, _ = _prepare(definition, manifest, validate_adapters=True)
    return preview


def _parse_instant(value: str) -> datetime:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail("time_invalid", str(exc))
    if instant.tzinfo is None:
        _fail("time_invalid", "timestamp must carry a timezone")
    return instant.astimezone(timezone.utc)


def _format_instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ledger_call(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except WorkflowExecutionLedgerError as exc:
        raise WorkflowRuntimeError(exc.code, str(exc)) from exc


def _append(
    ledger_path: Path,
    private_root: Path,
    run_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    key: str,
    now: str,
) -> dict[str, Any]:
    return _ledger_call(
        lambda: append_execution_event(
            ledger_path,
            private_root=private_root,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=key,
            occurred_at=now,
        )
    )


def _read(ledger_path: Path, private_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        events = read_execution_events_read_only(ledger_path, private_root=private_root)
        return events, project_execution_events(events)
    except WorkflowExecutionLedgerError as exc:
        raise WorkflowRuntimeError(exc.code, str(exc)) from exc


def _sum_reserved(projection: Mapping[str, Any]) -> dict[str, int]:
    total = _zero_usage()
    for reservation in projection["reservations"].values():
        for key in _RESOURCE_KEYS:
            total[key] += reservation["amounts"][key]
    return total


def _render_status(projection: Mapping[str, Any]) -> dict[str, Any]:
    if projection["run_id"] is None:
        _fail("run_missing", "execution ledger does not contain a run")
    packets = [
        projection["leaf_packets_by_id"][task_id]
        for task_id in projection["task_order"]
        if task_id in projection["leaf_packets_by_id"]
    ]
    return {
        "schema_version": "workflow_runtime_status_v1",
        "run_id": projection["run_id"],
        "status": projection["status"],
        "outcome": projection["outcome"],
        "verification_state": projection["verification_state"],
        "convergence_state": projection["convergence_state"],
        "identity_hash": projection["identity_hash"],
        "definition_digest": projection["definition_digest"],
        "plan_digest": projection["plan_digest"],
        "manifest_digest": projection["manifest_digest"],
        "leaf_packets": packets,
        "budget": {
            "hard": projection["hard_budget"],
            "reserved": _sum_reserved(projection),
            "settled": projection["settled"],
        },
        "event_count": projection["event_count"],
        "ledger_tip": projection["last_event_id"],
    }


def read_runtime_status(ledger_path: Path, *, private_root: Path) -> dict[str, Any]:
    """Read-only projection from the canonical ledger. Never repairs or rewrites."""
    try:
        events = read_execution_events_read_only(ledger_path, private_root=private_root)
        return _render_status(project_execution_events(events))
    except WorkflowExecutionLedgerError as exc:
        raise WorkflowRuntimeError(exc.code, str(exc)) from exc


def _leaf_packet(
    *,
    leaf_id: str,
    leaf: Mapping[str, Any],
    manifest: Mapping[str, Any],
    dependencies: Mapping[str, dict[str, Any]],
    status: str,
    result: Any,
    value_present: bool,
    claims: list[str] | tuple[str, ...] = (),
    evidence: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
    uncertainty: list[dict[str, str]] | tuple[dict[str, str], ...] = (),
    usage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    dependency_values = {
        key: {"status": packet["status"], "value_present": packet["value_present"], "result": packet["result"]}
        for key, packet in sorted(dependencies.items())
    }
    return {
        "schema_version": "workflow_leaf_packet_v1",
        "leaf_id": leaf_id,
        "input_hash": _digest({"input": leaf["input"], "dependencies": dependency_values}),
        "baseline_ref": manifest["baseline_ref"],
        "authority_ref": manifest["authority_ref"],
        "status": status,
        "claims": list(claims),
        "evidence": list(evidence),
        "uncertainty": list(uncertainty),
        "usage": dict(usage or _zero_usage()),
        "continuation": None,
        "value_present": value_present,
        "result": result,
    }


def _execute_leaf(
    adapter: _Adapter,
    leaf: Mapping[str, Any],
    dependencies: Mapping[str, dict[str, Any]],
) -> tuple[_AdapterResult, int]:
    started = time.monotonic_ns()
    result = adapter.execute(leaf["input"], dependencies)
    wall_ms = max(0, math.ceil((time.monotonic_ns() - started) / 1_000_000))
    return result, wall_ms


def _blocked_packet(
    task_id: str,
    leaf: Mapping[str, Any],
    manifest: Mapping[str, Any],
    dependencies: Mapping[str, dict[str, Any]],
    code: str,
    message: str,
) -> dict[str, Any]:
    return _leaf_packet(
        leaf_id=task_id,
        leaf=leaf,
        manifest=manifest,
        dependencies=dependencies,
        status="blocked",
        result=None,
        value_present=False,
        uncertainty=[{"code": code, "message": message}],
    )


def _cancelled_packet(
    task_id: str,
    leaf: Mapping[str, Any],
    manifest: Mapping[str, Any],
    dependencies: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    return _leaf_packet(
        leaf_id=task_id,
        leaf=leaf,
        manifest=manifest,
        dependencies=dependencies,
        status="cancelled",
        result=None,
        value_present=False,
        uncertainty=[
            {
                "code": "cancellation_requested",
                "message": "leaf was not admitted after cancellation",
            }
        ],
    )


def run_workflow(
    definition: object,
    manifest: object,
    *,
    ledger_path: Path,
    private_root: Path,
    execute_grant: ExecuteGrant | None,
    resume: bool = False,
    now: str,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute or resume a bounded trusted-pure local DAG.

    No adapter in this runtime can access tools, network, files, taskpool, or
    external dispatch. A new grant is mandatory for every invocation/resume.
    """
    if execute_grant is None or execute_grant.mode != "local_only" or not execute_grant.nonce:
        _fail("execute_grant_required", "a fresh local-only ExecuteGrant is required")
    execute_grant.consume()
    _parse_instant(now)

    plan, normalized_manifest, preview, adapters = _prepare(
        definition,
        manifest,
        validate_adapters=False,
    )
    existing_events, existing = _read(ledger_path, private_root)
    if existing_events:
        if not resume:
            _fail("run_exists", "ledger already contains a run; use explicit resume")
        if existing["identity_schema_version"] != 1:
            _fail("identity_schema_unsupported", "stored resume identity schema is unsupported")
        if existing["identity_hash"] != preview["identity_hash"]:
            _fail("resume_identity_mismatch", "resume identity differs from accepted run")
        if existing["manifest_digest"] != preview["manifest_digest"]:
            _fail("resume_identity_mismatch", "manifest differs from accepted run")
        plan, normalized_manifest, preview, adapters = _prepare(
            definition,
            manifest,
            validate_adapters=True,
        )
        if existing["status"] == "run_completed":
            return _render_status(existing)
        if existing["status"] == "accepted":
            existing = _append(
                ledger_path,
                private_root,
                normalized_manifest["run_id"],
                "run_started",
                {},
                f"{normalized_manifest['run_id']}:started",
                now,
            )
    else:
        if resume:
            _fail("resume_missing", "cannot resume a missing execution ledger")
        plan, normalized_manifest, preview, adapters = _prepare(
            definition,
            manifest,
            validate_adapters=True,
        )
        task_order = [
            task_id
            for wave in plan["waves"]
            for task_id in wave["task_ids"]
        ]
        verification_task_ids = sorted(
            binding["task_id"]
            for binding in plan["task_bindings"]
            if binding["role"] == "verify"
        )
        _append(
            ledger_path,
            private_root,
            normalized_manifest["run_id"],
            "run_accepted",
            {
                "identity_schema_version": 1,
                "identity": preview["identity"],
                "identity_hash": preview["identity_hash"],
                "manifest_digest": preview["manifest_digest"],
                "definition_digest": preview["definition_digest"],
                "plan_digest": preview["plan_digest"],
                "hard_budget": normalized_manifest["hard_budget"],
                "task_order": task_order,
                "verification_task_ids": verification_task_ids,
            },
            f"{normalized_manifest['run_id']}:accepted",
            now,
        )
        existing = _append(
            ledger_path,
            private_root,
            normalized_manifest["run_id"],
            "run_started",
            {},
            f"{normalized_manifest['run_id']}:started",
            now,
        )

    run_id = normalized_manifest["run_id"]
    verification_task_ids = list(existing["verification_task_ids"])
    completed_leaf_ids = set(existing["leaf_packets_by_id"])
    reservations = existing["reservations"]
    for task_id, reservation_id in sorted(existing["started"].items()):
        if task_id in completed_leaf_ids:
            continue
        reservation = reservations.get(reservation_id)
        if reservation is None:
            _fail(
                "leaf_recovery_unsafe",
                f"leaf {task_id} consumed its reservation without a terminal packet",
            )
        if _parse_instant(reservation["expires_at"]) <= _parse_instant(now):
            _fail(
                "reservation_orphaned",
                f"leaf {task_id} may still be executing after reservation expiry; fencing is unavailable",
            )

    # Unstarted reservations are admission leases and may be reclaimed only after
    # expiry. Started work is handled above and always fails closed without fencing.
    for reservation_id, reservation in sorted(reservations.items()):
        if _parse_instant(reservation["expires_at"]) > _parse_instant(now):
            _fail("reservation_active", f"reservation {reservation_id} is still active")
        existing = _append(
            ledger_path,
            private_root,
            run_id,
            "budget_released",
            {"reservation_id": reservation_id, "reason": "expired_on_resume"},
            f"{run_id}:release:{reservation_id}",
            now,
        )

    task_bindings = {item["task_id"]: item for item in plan["task_bindings"]}
    packets: dict[str, dict[str, Any]] = dict(existing["leaf_packets_by_id"])
    pending = set(task_bindings) - set(packets)
    running: dict[Future[tuple[_AdapterResult, int]], tuple[str, str, dict[str, dict[str, Any]]]] = {}
    attempts: dict[str, int] = {task_id: 0 for task_id in task_bindings}

    def append_packet(packet: dict[str, Any], suffix: str) -> None:
        nonlocal existing
        existing = _append(
            ledger_path,
            private_root,
            run_id,
            "leaf_packet",
            {"packet": packet},
            f"{run_id}:packet:{packet['leaf_id']}:{suffix}",
            now,
        )
        packets[packet["leaf_id"]] = packet
        pending.discard(packet["leaf_id"])

    with ThreadPoolExecutor(max_workers=normalized_manifest["max_concurrency"]) as executor:
        while pending or running:
            if pending and cancel_requested is not None and cancel_requested():
                for task_id in sorted(pending):
                    dependencies = {
                        dep: packets[dep]
                        for dep in task_bindings[task_id]["depends_on"]
                        if dep in packets
                    }
                    append_packet(
                        _cancelled_packet(
                            task_id,
                            normalized_manifest["leaves"][task_id],
                            normalized_manifest,
                            dependencies,
                        ),
                        "cancelled",
                    )
            terminal_blocked: list[str] = []
            ready: list[str] = []
            for task_id in sorted(pending):
                dependencies = task_bindings[task_id]["depends_on"]
                if any(dep in packets and packets[dep]["status"] != "completed" for dep in dependencies):
                    terminal_blocked.append(task_id)
                elif all(dep in packets and packets[dep]["status"] == "completed" for dep in dependencies):
                    ready.append(task_id)
            for task_id in terminal_blocked:
                dependencies = {
                    dep: packets[dep]
                    for dep in task_bindings[task_id]["depends_on"]
                    if dep in packets
                }
                append_packet(
                    _blocked_packet(
                        task_id,
                        normalized_manifest["leaves"][task_id],
                        normalized_manifest,
                        dependencies,
                        "dependency_not_completed",
                        "one or more dependencies did not complete successfully",
                    ),
                    "dependency",
                )
            available_slots = normalized_manifest["max_concurrency"] - len(running)
            for task_id in ready[:available_slots]:
                leaf = normalized_manifest["leaves"][task_id]
                dependencies = {dep: packets[dep] for dep in task_bindings[task_id]["depends_on"]}
                attempts[task_id] += 1
                reservation_id = f"res:{task_id}:{attempts[task_id]}:{existing['event_count'] + 1}"
                expires_at = _format_instant(
                    _parse_instant(now) + timedelta(milliseconds=normalized_manifest["reservation_ttl_ms"])
                )
                try:
                    existing = _append(
                        ledger_path,
                        private_root,
                        run_id,
                        "budget_reserved",
                        {
                            "reservation_id": reservation_id,
                            "task_id": task_id,
                            "amounts": leaf["reserve"],
                            "expires_at": expires_at,
                        },
                        f"{run_id}:reserve:{reservation_id}",
                        now,
                    )
                except WorkflowRuntimeError as exc:
                    if exc.code != "hard_budget_exhausted":
                        raise
                    append_packet(
                        _blocked_packet(
                            task_id,
                            leaf,
                            normalized_manifest,
                            dependencies,
                            "hard_budget_exhausted",
                            "hard budget rejected the next leaf reservation",
                        ),
                        "budget",
                    )
                    continue
                existing = _append(
                    ledger_path,
                    private_root,
                    run_id,
                    "leaf_started",
                    {"leaf_id": task_id, "reservation_id": reservation_id},
                    f"{run_id}:started:{reservation_id}",
                    now,
                )
                future = executor.submit(_execute_leaf, adapters[leaf["adapter"]], leaf, dependencies)
                running[future] = (task_id, reservation_id, dependencies)
                pending.discard(task_id)
            if not running:
                if pending:
                    if terminal_blocked or ready:
                        continue
                    _fail("scheduler_deadlock", f"no runnable tasks remain: {sorted(pending)}")
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: running[item][0]):
                task_id, reservation_id, dependencies = running.pop(future)
                leaf = normalized_manifest["leaves"][task_id]
                try:
                    adapter_result, wall_ms = future.result()
                except Exception as exc:
                    # Dispatch already occurred. Conservatively charge the full
                    # reservation so failed work cannot refund a consumed spawn.
                    existing = _append(
                        ledger_path,
                        private_root,
                        run_id,
                        "budget_settled",
                        {"reservation_id": reservation_id, "actual": leaf["reserve"]},
                        f"{run_id}:settle:{reservation_id}",
                        now,
                    )
                    packet = _leaf_packet(
                        leaf_id=task_id,
                        leaf=leaf,
                        manifest=normalized_manifest,
                        dependencies=dependencies,
                        status="failed",
                        result=None,
                        value_present=False,
                        uncertainty=[{"code": "adapter_failed", "message": f"{type(exc).__name__}: {exc}"}],
                        usage=leaf["reserve"],
                    )
                else:
                    actual = dict(adapter_result.usage)
                    actual["spawns"] = 1
                    actual["wall_ms"] = wall_ms
                    if any(actual[key] > leaf["reserve"][key] for key in _RESOURCE_KEYS):
                        # The adapter violated its declared upper bound. Charge the
                        # full reservation and fail the packet; never refund work
                        # that was physically dispatched.
                        existing = _append(
                            ledger_path,
                            private_root,
                            run_id,
                            "budget_settled",
                            {"reservation_id": reservation_id, "actual": leaf["reserve"]},
                            f"{run_id}:settle:{reservation_id}",
                            now,
                        )
                        packet = _leaf_packet(
                            leaf_id=task_id,
                            leaf=leaf,
                            manifest=normalized_manifest,
                            dependencies=dependencies,
                            status="failed",
                            result=None,
                            value_present=False,
                            uncertainty=[{"code": "reservation_exceeded", "message": "actual usage exceeded reserved budget"}],
                            usage=leaf["reserve"],
                        )
                    else:
                        existing = _append(
                            ledger_path,
                            private_root,
                            run_id,
                            "budget_settled",
                            {"reservation_id": reservation_id, "actual": actual},
                            f"{run_id}:settle:{reservation_id}",
                            now,
                        )
                        errors = sorted(
                            Draft202012Validator(leaf["output_schema"]).iter_errors(adapter_result.result),
                            key=lambda error: tuple(str(part) for part in error.absolute_path),
                        )
                        if errors:
                            packet = _leaf_packet(
                                leaf_id=task_id,
                                leaf=leaf,
                                manifest=normalized_manifest,
                                dependencies=dependencies,
                                status="unverified",
                                result=adapter_result.result,
                                value_present=True,
                                claims=adapter_result.claims,
                                evidence=adapter_result.evidence,
                                uncertainty=[{"code": "output_schema_invalid", "message": errors[0].message}],
                                usage=actual,
                            )
                        else:
                            packet = _leaf_packet(
                                leaf_id=task_id,
                                leaf=leaf,
                                manifest=normalized_manifest,
                                dependencies=dependencies,
                                status="completed",
                                result=adapter_result.result,
                                value_present=True,
                                claims=adapter_result.claims,
                                evidence=adapter_result.evidence,
                                usage=actual,
                            )
                append_packet(packet, f"attempt-{attempts[task_id]}")
                pending.discard(task_id)

    all_completed = all(packet["status"] == "completed" for packet in packets.values())
    if (
        all_completed
        and verification_task_ids
        and existing["verification_state"] == "unverified"
    ):
        existing = _append(
            ledger_path,
            private_root,
            run_id,
            "run_verified",
            {"verifier_leaf_ids": verification_task_ids},
            f"{run_id}:verified",
            now,
        )
    verification_state = existing["verification_state"]
    existing = _append(
        ledger_path,
        private_root,
        run_id,
        "run_completed",
        {
            "status": "run_completed",
            "verification_state": verification_state,
            "convergence_state": "not_converged",
        },
        f"{run_id}:completed",
        now,
    )
    return _render_status(existing)
