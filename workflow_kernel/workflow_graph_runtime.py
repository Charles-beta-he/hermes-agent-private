"""Bounded local executor for canonical WorkflowExecutionPlanV1 graphs.

The compiled plan remains authority-neutral. Execution is opened only by an
exact, single-use :class:`LocalExecutionGrant` and call-time runner bindings.
Runner callables are deliberately outside the definition/plan digests; the
receipt records a digest of their serializable binding descriptors instead.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from workflow_kernel.workflow_definition import compile_workflow_definition_v1
from workflow_kernel.workflow_execution_event_ledger import (
    WorkflowExecutionLedgerSink,
    compare_rfc3339_instants_v1,
    derive_execution_approval_digest_v1,
    derive_execution_ids_v1,
)


_SIDE_EFFECT_RANK = {
    "none": 0,
    "local_read": 1,
    "local_write": 2,
    "external_read": 3,
    "external_write": 4,
}
_ALLOWED_REDUCERS = {"mapping", "list", "first", "concat"}
_ALLOWED_JOIN_POLICIES = {"require_success", "allow_skipped"}
_ALLOWED_MODEL_TIERS = {"none", "cheap", "standard", "reasoning"}
_ALLOWED_ISOLATION = {"in_process"}
_TERMINAL = {"achieved", "failed", "blocked", "skipped", "planned"}
_GRANT_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)
_CONSUMED_GRANT_IDS: set[str] = set()
_GRANT_REGISTRY_LOCK = Lock()
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SESSION_ID = re.compile(r"[a-z][a-z0-9._-]{0,127}")
_RFC3339 = re.compile(
    r"(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})"
)

Runner = Callable[["TaskContext"], Any]
Condition = Callable[["TaskContext"], bool]
ExecutionEventSink = Callable[[Mapping[str, Any]], None]


class WorkflowGraphRuntimeError(ValueError):
    """Fail-closed admission or scheduling error with a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuntimeTaskBinding:
    """Serializable policy metadata plus registry keys for one plan task."""

    task_id: str
    runner_key: str
    condition_key: str | None = None
    reducer: str = "mapping"
    join_policy: str = "require_success"
    model_tier: str = "none"
    isolation: str = "in_process"


@dataclass(frozen=True)
class LocalExecutionGrant:
    """Exact process-local single-use authority for injected local runners."""

    grant_id: str
    plan_digest: str
    definition_digest: str
    allowed_capabilities: frozenset[str]
    max_side_effect: str = "local_write"
    max_workers: int = 4
    consumed: bool = field(default=False, init=False, compare=False)
    _consume_lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    def __copy__(self) -> "LocalExecutionGrant":
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> "LocalExecutionGrant":
        memo[id(self)] = self
        return self

    def consume(self, admitted_grant_id: str | None = None) -> None:
        """Atomically close this grant before the first runner is submitted."""

        grant_id = admitted_grant_id or self.grant_id
        with self._consume_lock:
            with _GRANT_REGISTRY_LOCK:
                if self.consumed or grant_id in _CONSUMED_GRANT_IDS:
                    raise WorkflowGraphRuntimeError(
                        "grant_already_consumed",
                        "local execution grant id is single-use in this process",
                    )
                _CONSUMED_GRANT_IDS.add(grant_id)
                object.__setattr__(self, "consumed", True)


@dataclass(frozen=True)
class TaskContext:
    """Immutable scheduler-owned input view passed to a runner/condition."""

    task_id: str
    inputs: tuple[tuple[str, Any], ...]
    reduced_input: Any
    outputs: Mapping[str, Any]
    model_tier: str
    isolation: str
    run_id: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    effect_id: str | None = None
    fencing_token: int | None = None


@dataclass(frozen=True)
class RuntimeExecutionSession:
    """Exact authority-neutral identity for durable execution evidence."""

    run_id: str
    session_owner: str
    lease_id: str
    lease_expires_at: str
    fencing_token: int
    workflow_id: str
    definition_digest: str
    plan_digest: str
    binding_digest: str
    execution_mode: str = "apply"
    policy_digest: str | None = None
    approval_digest: str | None = None


@dataclass(frozen=True)
class _AdmittedGrant:
    """Detached authority values used after caller-owned grant admission."""

    grant_id: str
    plan_digest: str
    definition_digest: str
    allowed_capabilities: frozenset[str]
    max_side_effect: str
    max_workers: int


@dataclass(frozen=True)
class _AdmittedExecutionSession:
    """Detached durable-evidence identity used after caller admission."""

    run_id: str
    session_owner: str
    lease_id: str
    lease_expires_at: str
    fencing_token: int
    workflow_id: str
    definition_digest: str
    plan_digest: str
    binding_digest: str
    execution_mode: str
    policy_digest: str | None
    approval_digest: str | None


def _validate_grant(grant: LocalExecutionGrant | None) -> _AdmittedGrant:
    """Snapshot, then validate, the complete authority token exactly once."""

    if grant is None:
        raise WorkflowGraphRuntimeError("grant_required", "an exact local grant is required")
    if type(grant) is not LocalExecutionGrant:
        raise WorkflowGraphRuntimeError(
            "invalid_grant", "local execution grant subclasses are not accepted"
        )
    candidate = _AdmittedGrant(
        grant_id=grant.grant_id,
        plan_digest=grant.plan_digest,
        definition_digest=grant.definition_digest,
        allowed_capabilities=grant.allowed_capabilities,
        max_side_effect=grant.max_side_effect,
        max_workers=grant.max_workers,
    )
    valid = (
        type(candidate.grant_id) is str
        and 1 <= len(candidate.grant_id) <= 128
        and all(char in _GRANT_ID_CHARS for char in candidate.grant_id)
        and type(candidate.plan_digest) is str
        and _DIGEST.fullmatch(candidate.plan_digest) is not None
        and type(candidate.definition_digest) is str
        and _DIGEST.fullmatch(candidate.definition_digest) is not None
        and type(candidate.allowed_capabilities) is frozenset
        and all(
            type(item) is str and bool(item)
            for item in candidate.allowed_capabilities
        )
        and type(candidate.max_side_effect) is str
        and candidate.max_side_effect in _SIDE_EFFECT_RANK
        and _SIDE_EFFECT_RANK[candidate.max_side_effect]
        <= _SIDE_EFFECT_RANK["local_write"]
        and type(candidate.max_workers) is int
        and 1 <= candidate.max_workers <= 32
    )
    if not valid:
        raise WorkflowGraphRuntimeError(
            "invalid_grant",
            "local execution grant has an invalid field or authority boundary",
        )
    return candidate


def derive_runtime_apply_approval_digest_v1(
    *,
    run_id: str,
    workflow_id: str,
    plan_digest: str,
    binding_digest: str,
    policy_digest: str,
) -> str:
    """Bind apply evidence to one admitted run, plan, bindings, and policy.

    This digest is evidence, not authority. The exact single-use local execution
    grant remains the authority gate.
    """

    return derive_execution_approval_digest_v1(
        run_id=run_id,
        workflow_id=workflow_id,
        plan_digest=plan_digest,
        binding_digest=binding_digest,
        policy_digest=policy_digest,
    )


def _validate_execution_session(
    session: RuntimeExecutionSession | None,
    event_sink: ExecutionEventSink | None,
) -> _AdmittedExecutionSession | None:
    """Single-read snapshot of optional durable-evidence identity."""

    if (session is None) != (event_sink is None):
        raise WorkflowGraphRuntimeError(
            "durable_evidence_incomplete",
            "execution session and event sink must be supplied together",
        )
    if session is None:
        return None
    if type(session) is not RuntimeExecutionSession:
        raise WorkflowGraphRuntimeError(
            "invalid_execution_session",
            "runtime execution session subclasses are not accepted",
        )
    candidate = _AdmittedExecutionSession(
        run_id=session.run_id,
        session_owner=session.session_owner,
        lease_id=session.lease_id,
        lease_expires_at=session.lease_expires_at,
        fencing_token=session.fencing_token,
        workflow_id=session.workflow_id,
        definition_digest=session.definition_digest,
        plan_digest=session.plan_digest,
        binding_digest=session.binding_digest,
        execution_mode=session.execution_mode,
        policy_digest=session.policy_digest,
        approval_digest=session.approval_digest,
    )
    valid = (
        type(candidate.run_id) is str
        and _SESSION_ID.fullmatch(candidate.run_id) is not None
        and type(candidate.session_owner) is str
        and _SESSION_ID.fullmatch(candidate.session_owner) is not None
        and type(candidate.lease_id) is str
        and _SESSION_ID.fullmatch(candidate.lease_id) is not None
        and type(candidate.lease_expires_at) is str
        and _RFC3339.fullmatch(candidate.lease_expires_at) is not None
        and type(candidate.fencing_token) is int
        and 1 <= candidate.fencing_token <= 9_007_199_254_740_991
        and type(candidate.workflow_id) is str
        and _SESSION_ID.fullmatch(candidate.workflow_id) is not None
        and type(candidate.definition_digest) is str
        and _DIGEST.fullmatch(candidate.definition_digest) is not None
        and type(candidate.plan_digest) is str
        and _DIGEST.fullmatch(candidate.plan_digest) is not None
        and type(candidate.binding_digest) is str
        and _DIGEST.fullmatch(candidate.binding_digest) is not None
        and type(candidate.execution_mode) is str
        and candidate.execution_mode in {"apply", "dry_run"}
        and (
            candidate.policy_digest is None
            or (
                type(candidate.policy_digest) is str
                and _DIGEST.fullmatch(candidate.policy_digest) is not None
            )
        )
        and (
            candidate.approval_digest is None
            or (
                type(candidate.approval_digest) is str
                and _DIGEST.fullmatch(candidate.approval_digest) is not None
            )
        )
        and type(event_sink) is WorkflowExecutionLedgerSink
    )
    valid_policy = (
        candidate.execution_mode == "dry_run"
        and candidate.policy_digest is not None
        and candidate.approval_digest is None
    ) or (
        candidate.execution_mode == "apply"
        and candidate.policy_digest is not None
        and candidate.approval_digest
        == derive_runtime_apply_approval_digest_v1(
            run_id=candidate.run_id,
            workflow_id=candidate.workflow_id,
            plan_digest=candidate.plan_digest,
            binding_digest=candidate.binding_digest,
            policy_digest=candidate.policy_digest,
        )
    )
    if not valid or not valid_policy:
        raise WorkflowGraphRuntimeError(
            "invalid_execution_session",
            "runtime execution session has an invalid identity field",
        )
    return candidate


def _validate_strict_json(value: Any, path: str = "$") -> None:
    """Reject Python values that JSON would silently coerce or ambiguously encode."""

    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_strict_json(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} contains a non-string object key")
            _validate_strict_json(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} contains unsupported type {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    _validate_strict_json(value)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _plan_body_digest(plan: Mapping[str, Any]) -> str:
    return _digest({key: value for key, value in plan.items() if key != "plan_digest"})


def _binding_descriptor(binding: RuntimeTaskBinding) -> dict[str, Any]:
    return asdict(binding)


def _snapshot_binding(binding: RuntimeTaskBinding) -> RuntimeTaskBinding:
    """Detach a frozen binding from its caller-owned dataclass instance."""

    return RuntimeTaskBinding(
        task_id=binding.task_id,
        runner_key=binding.runner_key,
        condition_key=binding.condition_key,
        reducer=binding.reducer,
        join_policy=binding.join_policy,
        model_tier=binding.model_tier,
        isolation=binding.isolation,
    )


def derive_runtime_binding_digest_v1(
    bindings: Sequence[RuntimeTaskBinding],
) -> str:
    """Digest detached serializable binding descriptors in task-id order."""

    snapshots = tuple(_snapshot_binding(binding) for binding in bindings)
    task_ids = [binding.task_id for binding in snapshots]
    if len(task_ids) != len(set(task_ids)):
        raise WorkflowGraphRuntimeError(
            "binding_duplicate", "runtime binding digest input contains duplicate task ids"
        )
    return _digest(
        [_binding_descriptor(binding) for binding in sorted(snapshots, key=lambda item: item.task_id)]
    )


def _reduce_inputs(name: str, pairs: tuple[tuple[str, Any], ...]) -> Any:
    if name == "mapping":
        return {task_id: value for task_id, value in pairs}
    if name == "list":
        return [value for _, value in pairs]
    if name == "first":
        return pairs[0][1] if pairs else None
    if name == "concat":
        flattened: list[Any] = []
        for _, value in pairs:
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        return flattened
    raise AssertionError(f"unvalidated reducer {name!r}")


def _accepted_verdict(value: Any) -> bool:
    if type(value) is bool:
        return value
    return isinstance(value, Mapping) and value.get("accepted") is True


def _ensure_json_value(value: Any) -> None:
    try:
        _canonical_bytes(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowGraphRuntimeError(
            "runner_output_not_json",
            f"runner output is not deterministic JSON data: {exc}",
        ) from exc


def _json_clone(value: Any) -> Any:
    """Return a detached strict-JSON snapshot or fail with a stable runtime code."""

    try:
        return json.loads(_canonical_bytes(value))
    except (TypeError, ValueError) as exc:
        raise WorkflowGraphRuntimeError(
            "runner_output_not_json",
            f"runner output is not deterministic JSON: {type(exc).__name__}",
        ) from exc


def _admit(
    plan: Mapping[str, Any],
    definition: Mapping[str, Any],
    bindings: Sequence[RuntimeTaskBinding],
    runners: Mapping[str, Runner],
    conditions: Mapping[str, Condition],
    grant: _AdmittedGrant,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, RuntimeTaskBinding],
    dict[str, list[str]],
    dict[str, list[str]],
    str,
]:
    """Validate every graph/policy/authority binding before any invocation."""

    if plan.get("plan_digest") != _plan_body_digest(plan):
        raise WorkflowGraphRuntimeError(
            "plan_digest_mismatch", "received plan body does not match plan_digest"
        )

    compiled = compile_workflow_definition_v1(definition)
    if compiled != plan:
        raise WorkflowGraphRuntimeError(
            "plan_definition_mismatch",
            "received plan is not the deterministic compilation of the definition",
        )
    if plan.get("authority_granted") is not False or plan.get("executable") is not False:
        raise WorkflowGraphRuntimeError(
            "plan_authority_invalid", "compiled plan must remain authority-neutral"
        )
    if grant.plan_digest != plan["plan_digest"]:
        raise WorkflowGraphRuntimeError(
            "grant_plan_mismatch", "grant is bound to a different plan digest"
        )
    if grant.definition_digest != plan["definition_digest"]:
        raise WorkflowGraphRuntimeError(
            "grant_definition_mismatch", "grant is bound to a different definition"
        )
    task_by_id = {binding["task_id"]: dict(binding) for binding in plan["task_bindings"]}
    binding_by_id: dict[str, RuntimeTaskBinding] = {}
    for binding in bindings:
        if binding.task_id in binding_by_id:
            raise WorkflowGraphRuntimeError(
                "binding_duplicate", f"duplicate runtime binding {binding.task_id!r}"
            )
        binding_by_id[binding.task_id] = binding
    if set(binding_by_id) != set(task_by_id):
        missing = sorted(set(task_by_id) - set(binding_by_id))
        extra = sorted(set(binding_by_id) - set(task_by_id))
        raise WorkflowGraphRuntimeError(
            "binding_set_mismatch", f"binding mismatch; missing={missing}, extra={extra}"
        )

    verifier_targets: dict[str, list[str]] = {}
    target_verifiers: dict[str, list[str]] = {}
    for link in plan["verifier_links"]:
        target = link["execute_task_id"]
        target_verifiers[target] = list(link["verifier_task_ids"])
        for verifier in link["verifier_task_ids"]:
            verifier_targets.setdefault(verifier, []).append(target)

    for task_id in sorted(task_by_id):
        task = task_by_id[task_id]
        binding = binding_by_id[task_id]
        if binding.runner_key not in runners:
            raise WorkflowGraphRuntimeError(
                "runner_missing", f"runner key {binding.runner_key!r} is not registered"
            )
        if binding.condition_key is not None and binding.condition_key not in conditions:
            raise WorkflowGraphRuntimeError(
                "condition_missing",
                f"condition key {binding.condition_key!r} is not registered",
            )
        if task["role"] == "verify" and binding.condition_key is not None:
            raise WorkflowGraphRuntimeError(
                "verifier_condition_forbidden",
                "verifier conditions are derived from target state, not routing predicates",
            )
        if binding.reducer not in _ALLOWED_REDUCERS:
            raise WorkflowGraphRuntimeError(
                "reducer_unsupported", f"unsupported reducer {binding.reducer!r}"
            )
        if binding.join_policy not in _ALLOWED_JOIN_POLICIES:
            raise WorkflowGraphRuntimeError(
                "join_policy_unsupported",
                f"unsupported join policy {binding.join_policy!r}",
            )
        if binding.model_tier not in _ALLOWED_MODEL_TIERS:
            raise WorkflowGraphRuntimeError(
                "model_tier_unsupported",
                f"unsupported model tier {binding.model_tier!r}",
            )
        if binding.isolation not in _ALLOWED_ISOLATION:
            raise WorkflowGraphRuntimeError(
                "isolation_unsupported", f"unsupported isolation {binding.isolation!r}"
            )
        if _SIDE_EFFECT_RANK[task["side_effect"]] > _SIDE_EFFECT_RANK["local_write"]:
            raise WorkflowGraphRuntimeError(
                "side_effect_runtime_unsupported",
                f"task {task_id!r} exceeds the local runtime side-effect boundary",
            )
        if _SIDE_EFFECT_RANK[task["side_effect"]] > _SIDE_EFFECT_RANK[grant.max_side_effect]:
            raise WorkflowGraphRuntimeError(
                "side_effect_ceiling_exceeded",
                f"task {task_id!r} exceeds local grant side-effect ceiling",
            )
        extra_capabilities = set(task["capabilities"]) - set(grant.allowed_capabilities)
        if extra_capabilities:
            raise WorkflowGraphRuntimeError(
                "grant_capability_exceeded",
                f"task {task_id!r} requires ungranted capabilities: {sorted(extra_capabilities)}",
            )

    binding_digest = _digest(
        [_binding_descriptor(binding_by_id[task_id]) for task_id in sorted(binding_by_id)]
    )
    return task_by_id, binding_by_id, verifier_targets, target_verifiers, binding_digest


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def execute_workflow_plan_v1(
    plan: Mapping[str, Any],
    definition: Mapping[str, Any],
    bindings: Sequence[RuntimeTaskBinding],
    runners: Mapping[str, Runner],
    *,
    conditions: Mapping[str, Condition] | None = None,
    grant: LocalExecutionGrant | None = None,
    execution_session: RuntimeExecutionSession | None = None,
    event_sink: ExecutionEventSink | None = None,
    execution_clock: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Execute an admitted plan with dependency-driven local concurrency.

    ``waves`` are deliberately not used for dispatch. Readiness derives from the
    canonical ``task_bindings[].depends_on`` edges, allowing a downstream task to
    start as soon as its own dependencies are accepted.
    """

    # Snapshot every caller-owned registry and strict-JSON document before
    # admission. Injected runners may mutate their original containers while
    # execution is in flight; scheduling and receipt identity must remain bound
    # to the admitted bytes.
    if execution_session is None and execution_clock is not None:
        raise WorkflowGraphRuntimeError(
            "durable_evidence_incomplete",
            "execution clock requires an execution session and event sink",
        )
    if execution_clock is not None and not callable(execution_clock):
        raise WorkflowGraphRuntimeError(
            "invalid_execution_clock", "execution clock must be callable"
        )
    admitted_clock = execution_clock or _utc_now
    admitted_session = _validate_execution_session(execution_session, event_sink)
    admitted_grant = _validate_grant(grant)
    try:
        admitted_plan = json.loads(_canonical_bytes(plan))
        admitted_definition = json.loads(_canonical_bytes(definition))
    except (TypeError, ValueError) as exc:
        raise WorkflowGraphRuntimeError(
            "admission_input_not_json",
            f"plan and definition must be deterministic JSON: {type(exc).__name__}",
        ) from exc
    admitted_bindings = tuple(_snapshot_binding(binding) for binding in bindings)
    admitted_runners = dict(runners)
    selected_conditions = dict(conditions or {})
    (
        task_by_id,
        binding_by_id,
        verifier_targets,
        target_verifiers,
        binding_digest,
    ) = _admit(
        admitted_plan,
        admitted_definition,
        admitted_bindings,
        admitted_runners,
        selected_conditions,
        admitted_grant,
    )
    if admitted_session is not None:
        session_matches = (
            admitted_session.workflow_id
            == admitted_plan["workflow_ref"]["workflow_id"]
            and admitted_session.definition_digest == admitted_plan["definition_digest"]
            and admitted_session.plan_digest == admitted_plan["plan_digest"]
            and admitted_session.binding_digest == binding_digest
        )
        if not session_matches:
            raise WorkflowGraphRuntimeError(
                "execution_session_mismatch",
                "durable execution identity is not bound to admitted graph bytes",
            )
    if grant is None:  # narrowed for static analyzers; validation above already rejects None
        raise AssertionError("validated local grant unexpectedly missing")
    admitted_event_sink = event_sink
    execution_mode = (
        admitted_session.execution_mode if admitted_session is not None else "apply"
    )

    def execution_ids(task_id: str) -> dict[str, str | None]:
        if admitted_session is None:
            return {"step_id": None, "attempt_id": None, "effect_id": None}
        return derive_execution_ids_v1(
            run_id=admitted_session.run_id,
            plan_digest=admitted_session.plan_digest,
            binding_digest=admitted_session.binding_digest,
            task_id=task_id,
            attempt=1,
            side_effect=task_by_id[task_id]["side_effect"],
        )

    def read_execution_now() -> str:
        try:
            current = admitted_clock()
            if type(current) is not str:
                raise TypeError("clock result is not str")
            compare_rfc3339_instants_v1(current, current)
            return current
        except Exception as exc:
            raise WorkflowGraphRuntimeError(
                "invalid_execution_clock",
                f"execution clock failed strict RFC3339 validation: {type(exc).__name__}",
            ) from exc

    def emit_execution(
        kind: str,
        task_id: str | None = None,
        *,
        evidence_digest: str | None = None,
        reason_code: str | None = None,
        include_attempt: bool = True,
    ) -> None:
        if admitted_session is None:
            return
        if admitted_event_sink is None:
            raise AssertionError("admitted execution session unexpectedly has no sink")
        ids = execution_ids(task_id) if task_id is not None else {
            "step_id": None,
            "attempt_id": None,
            "effect_id": None,
        }
        if not include_attempt:
            ids = {"step_id": ids["step_id"], "attempt_id": None, "effect_id": None}
        side_effect = task_by_id[task_id]["side_effect"] if task_id is not None else "none"
        observation = MappingProxyType(
            {
                "run_id": admitted_session.run_id,
                "occurred_at": read_execution_now(),
                "identity": MappingProxyType(
                    {
                        "session_owner": admitted_session.session_owner,
                        "lease_id": admitted_session.lease_id,
                        "lease_expires_at": admitted_session.lease_expires_at,
                        "fencing_token": admitted_session.fencing_token,
                        "workflow_id": admitted_session.workflow_id,
                        "definition_digest": admitted_session.definition_digest,
                        "plan_digest": admitted_session.plan_digest,
                        "binding_digest": admitted_session.binding_digest,
                        "execution_mode": admitted_session.execution_mode,
                        "policy_digest": admitted_session.policy_digest,
                        "approval_digest": admitted_session.approval_digest,
                    }
                ),
                "kind": kind,
                "task_id": task_id,
                **ids,
                "side_effect": side_effect,
                "evidence_digest": evidence_digest,
                "reason_code": reason_code,
            }
        )
        try:
            admitted_event_sink(observation)
        except WorkflowGraphRuntimeError:
            raise
        except Exception as exc:
            raise WorkflowGraphRuntimeError(
                "durable_evidence_failed",
                f"durable event sink rejected {kind!r}: {type(exc).__name__}",
            ) from exc

    def lease_expired() -> bool:
        if admitted_session is None:
            return False
        return (
            compare_rfc3339_instants_v1(
                read_execution_now(), admitted_session.lease_expires_at
            )
            >= 0
        )

    def ensure_active_lease(*, record_stale: bool) -> None:
        if not lease_expired():
            return
        if record_stale:
            emit_execution("stale_observed", reason_code="lease_expired")
        raise WorkflowGraphRuntimeError(
            "execution_lease_expired", "execution lease is no longer active"
        )

    ensure_active_lease(record_stale=False)
    grant.consume(admitted_grant.grant_id)
    emit_execution("run_admitted")

    states = {task_id: "waiting" for task_id in task_by_id}
    outputs: dict[str, Any] = {}
    accepted = {task_id: False for task_id in task_by_id}
    failures: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    event_sequence = 0
    max_in_flight = 0

    def emit(event: str, task_id: str) -> None:
        nonlocal event_sequence
        event_sequence += 1
        events.append({"sequence": event_sequence, "event": event, "task_id": task_id})

    def input_pairs(task_id: str) -> tuple[tuple[str, Any], ...]:
        task = task_by_id[task_id]
        pairs = []
        for dependency in sorted(task["depends_on"]):
            if dependency in outputs and states[dependency] in {
                "achieved",
                "pending_verification",
            }:
                pairs.append((dependency, _json_clone(outputs[dependency])))
        return tuple(pairs)

    def context_for(task_id: str) -> TaskContext:
        binding = binding_by_id[task_id]
        pairs = input_pairs(task_id)
        ids = execution_ids(task_id)
        return TaskContext(
            task_id=task_id,
            inputs=pairs,
            reduced_input=_reduce_inputs(binding.reducer, pairs),
            # Expose edge-materialized dependency values only. Global completed
            # outputs would make behavior depend on unrelated completion order.
            outputs=MappingProxyType(dict(pairs)),
            model_tier=binding.model_tier,
            isolation=binding.isolation,
            run_id=admitted_session.run_id if admitted_session is not None else None,
            step_id=ids["step_id"],
            attempt_id=ids["attempt_id"],
            effect_id=ids["effect_id"],
            fencing_token=(
                admitted_session.fencing_token if admitted_session is not None else None
            ),
        )

    def skip(task_id: str) -> None:
        if states[task_id] == "waiting":
            emit_execution("task_skipped", task_id, include_attempt=False)
            states[task_id] = "skipped"
            emit("skipped", task_id)

    def block(task_id: str, reason: str) -> None:
        if states[task_id] == "waiting":
            emit_execution(
                "task_blocked",
                task_id,
                reason_code="dependency_blocked",
                include_attempt=False,
            )
            states[task_id] = "blocked"
            failures[task_id] = reason
            emit("blocked", task_id)

    def fail_verifier_targets(verifier_id: str, reason: str) -> None:
        for target in verifier_targets.get(verifier_id, []):
            if states[target] == "pending_verification":
                emit_execution("task_failed", target, reason_code="verifier_failed")
                states[target] = "failed"
                accepted[target] = False
                failures[target] = f"verifier {verifier_id!r} failed: {reason}"
                emit("failed", target)

    in_flight: dict[Future[Any], str] = {}
    with ThreadPoolExecutor(
        max_workers=admitted_grant.max_workers,
        thread_name_prefix="hermes-workflow-graph",
    ) as pool:
        while True:
            progress = False

            for task_id in sorted(task_by_id):
                if states[task_id] != "waiting":
                    continue
                task = task_by_id[task_id]
                dependency_states = {
                    dependency: states[dependency] for dependency in task["depends_on"]
                }

                if (
                    execution_mode == "dry_run"
                    and task["side_effect"] == "local_write"
                    and not any(
                        state in {"failed", "blocked"}
                        for state in dependency_states.values()
                    )
                    and all(state in _TERMINAL for state in dependency_states.values())
                ):
                    ensure_active_lease(record_stale=True)
                    ids = execution_ids(task_id)
                    emit_execution(
                        "effect_planned",
                        task_id,
                        evidence_digest=_digest(
                            {
                                "schema_version": "workflow_effect_preview_v1",
                                "task_id": task_id,
                                "effect_id": ids["effect_id"],
                                "policy_digest": admitted_session.policy_digest
                                if admitted_session is not None
                                else None,
                            }
                        ),
                    )
                    states[task_id] = "planned"
                    emit("planned", task_id)
                    progress = True
                    continue

                if execution_mode == "dry_run" and any(
                    state == "planned" for state in dependency_states.values()
                ):
                    skip(task_id)
                    progress = True
                    continue

                if task["role"] == "verify":
                    targets = verifier_targets.get(task_id, [])
                    active_targets = [
                        target for target in targets if states[target] != "skipped"
                    ]
                    if not active_targets:
                        skip(task_id)
                        progress = True
                        continue
                    if any(
                        states[target] in {"failed", "blocked"}
                        for target in active_targets
                    ):
                        block(task_id, "verification target failed or was blocked")
                        fail_verifier_targets(
                            task_id, "another verification target failed or was blocked"
                        )
                        progress = True
                        continue
                    if any(
                        state in {"failed", "blocked"}
                        for dependency, state in dependency_states.items()
                        if dependency not in targets
                    ):
                        block(task_id, "non-target dependency failed or was blocked")
                        fail_verifier_targets(
                            task_id, "non-target dependency failed or was blocked"
                        )
                        progress = True
                        continue
                    if any(
                        state == "skipped"
                        for dependency, state in dependency_states.items()
                        if dependency not in targets
                    ):
                        block(task_id, "non-target dependency was skipped")
                        fail_verifier_targets(
                            task_id, "non-target dependency was skipped"
                        )
                        progress = True
                        continue
                    target_ready = all(
                        states[target] in {"pending_verification", "achieved"}
                        for target in active_targets
                    )
                    other_ready = all(
                        dependency in targets or state == "achieved"
                        for dependency, state in dependency_states.items()
                    )
                    if not (target_ready and other_ready):
                        continue
                else:
                    if any(state in {"failed", "blocked"} for state in dependency_states.values()):
                        block(task_id, "dependency failed or was blocked")
                        progress = True
                        continue
                    binding = binding_by_id[task_id]
                    if binding.join_policy == "require_success" and any(
                        state == "skipped" for state in dependency_states.values()
                    ):
                        skip(task_id)
                        progress = True
                        continue
                    allowed_terminal = {"achieved"}
                    if binding.join_policy == "allow_skipped":
                        allowed_terminal.add("skipped")
                    if not all(state in allowed_terminal for state in dependency_states.values()):
                        continue

                    if binding.condition_key is not None:
                        try:
                            selected = selected_conditions[binding.condition_key](
                                context_for(task_id)
                            )
                        except Exception as exc:  # condition is an injected boundary
                            emit_execution(
                                "task_blocked",
                                task_id,
                                reason_code="condition_raised",
                                include_attempt=False,
                            )
                            states[task_id] = "failed"
                            failures[task_id] = f"condition raised: {type(exc).__name__}: {exc}"
                            emit("failed", task_id)
                            progress = True
                            continue
                        if type(selected) is not bool:
                            emit_execution(
                                "task_blocked",
                                task_id,
                                reason_code="condition_invalid",
                                include_attempt=False,
                            )
                            states[task_id] = "failed"
                            failures[task_id] = "condition did not return bool"
                            emit("failed", task_id)
                            progress = True
                            continue
                        if not selected:
                            skip(task_id)
                            progress = True
                            continue

                if len(in_flight) >= admitted_grant.max_workers:
                    continue
                binding = binding_by_id[task_id]
                ensure_active_lease(record_stale=True)
                emit_execution("task_started", task_id)
                if task["side_effect"] == "local_write":
                    emit_execution("effect_intent", task_id)
                states[task_id] = "running"
                emit("started", task_id)
                in_flight[
                    pool.submit(admitted_runners[binding.runner_key], context_for(task_id))
                ] = task_id
                max_in_flight = max(max_in_flight, len(in_flight))
                progress = True

            if not in_flight:
                if all(state in _TERMINAL for state in states.values()):
                    break
                if not progress:
                    pending = {
                        task_id: state
                        for task_id, state in states.items()
                        if state not in _TERMINAL
                    }
                    raise WorkflowGraphRuntimeError(
                        "runtime_deadlock", f"no runnable tasks remain: {pending}"
                    )
                continue

            completed, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
            for future in sorted(completed, key=lambda item: in_flight[item]):
                task_id = in_flight.pop(future)
                task = task_by_id[task_id]
                ensure_active_lease(record_stale=True)
                try:
                    value = future.result()
                    _ensure_json_value(value)
                    value_snapshot = _json_clone(value)
                except Exception as exc:  # runner is an injected boundary
                    emit_execution("task_failed", task_id, reason_code="runner_raised")
                    states[task_id] = "failed"
                    stable_code = (
                        f"{exc.code}: "
                        if isinstance(exc, WorkflowGraphRuntimeError)
                        else ""
                    )
                    failures[task_id] = (
                        f"runner raised: {type(exc).__name__}: {stable_code}{exc}"
                    )
                    emit("failed", task_id)
                    if task["role"] == "verify":
                        fail_verifier_targets(task_id, failures[task_id])
                    continue

                output_digest = _digest(value_snapshot)
                if task["side_effect"] == "local_write":
                    emit_execution(
                        "effect_observed",
                        task_id,
                        evidence_digest=output_digest,
                    )
                outputs[task_id] = value_snapshot
                if task["role"] == "verify":
                    if _accepted_verdict(value_snapshot):
                        emit_execution(
                            "task_succeeded",
                            task_id,
                            evidence_digest=output_digest,
                        )
                        states[task_id] = "achieved"
                        accepted[task_id] = True
                        emit("achieved", task_id)
                        for target in verifier_targets.get(task_id, []):
                            if states[target] != "pending_verification":
                                continue
                            verifiers = target_verifiers[target]
                            if all(states[verifier] == "achieved" for verifier in verifiers):
                                emit_execution(
                                    "task_succeeded",
                                    target,
                                    evidence_digest=_digest(outputs[target]),
                                )
                                states[target] = "achieved"
                                accepted[target] = True
                                emit("accepted", target)
                    else:
                        emit_execution(
                            "task_failed",
                            task_id,
                            reason_code="verifier_rejected",
                        )
                        states[task_id] = "failed"
                        failures[task_id] = "verifier returned a non-accepted verdict"
                        emit("failed", task_id)
                        fail_verifier_targets(task_id, failures[task_id])
                elif task["role"] == "execute":
                    unavailable_verifiers = [
                        verifier
                        for verifier in target_verifiers.get(task_id, [])
                        if states[verifier] in {"failed", "blocked"}
                    ]
                    if unavailable_verifiers:
                        emit_execution(
                            "task_failed",
                            task_id,
                            reason_code="verifier_unavailable",
                        )
                        states[task_id] = "failed"
                        failures[task_id] = (
                            "required verifier unavailable: "
                            f"{sorted(unavailable_verifiers)}"
                        )
                        emit("failed", task_id)
                    else:
                        states[task_id] = "pending_verification"
                        emit("produced", task_id)
                else:
                    emit_execution(
                        "task_succeeded",
                        task_id,
                        evidence_digest=output_digest,
                    )
                    states[task_id] = "achieved"
                    accepted[task_id] = True
                    emit("achieved", task_id)

    node_receipts: dict[str, dict[str, Any]] = {}
    for task_id in sorted(task_by_id):
        node: dict[str, Any] = {
            "status": states[task_id],
            "accepted": accepted[task_id],
            "role": task_by_id[task_id]["role"],
        }
        if task_id in outputs:
            node["output"] = outputs[task_id]
        if task_id in failures:
            node["failure"] = failures[task_id]
        node_receipts[task_id] = node

    execute_task_ids = [
        task_id
        for task_id, task in task_by_id.items()
        if task["role"] == "execute"
    ]
    has_accepted_execute = any(accepted[task_id] for task_id in execute_task_ids)
    has_planned_execute = any(states[task_id] == "planned" for task_id in execute_task_ids)
    all_active_executes_accepted = all(
        states[task_id] == "skipped" or accepted[task_id]
        for task_id in execute_task_ids
    )
    if execution_mode == "dry_run":
        workflow_status = (
            "dry_run"
            if has_planned_execute
            and not any(
                state in {"waiting", "running", "failed", "blocked"}
                for state in states.values()
            )
            else "failed"
        )
    else:
        workflow_status = (
            "success"
            if has_accepted_execute
            and all_active_executes_accepted
            and not any(state in {"failed", "blocked"} for state in states.values())
            else "failed"
        )
    core = {
        "schema_version": "workflow_graph_execution_receipt_v1",
        "status": workflow_status,
        "workflow_ref": dict(admitted_plan["workflow_ref"]),
        "definition_digest": admitted_plan["definition_digest"],
        "plan_digest": admitted_plan["plan_digest"],
        "binding_digest": binding_digest,
        "grant_id": admitted_grant.grant_id,
        "execution_mode": execution_mode,
        "policy_digest": admitted_session.policy_digest
        if admitted_session is not None
        else None,
        "approval_digest": admitted_session.approval_digest
        if admitted_session is not None
        else None,
        "plan_authority_granted": False,
        "plan_executable": False,
        "nodes": node_receipts,
    }
    core_digest = _digest(core)
    ensure_active_lease(record_stale=True)
    terminal_kind = {
        "success": "run_succeeded",
        "dry_run": "run_dry_run_completed",
        "failed": "run_failed",
    }[workflow_status]
    emit_execution(
        terminal_kind,
        evidence_digest=core_digest,
        reason_code=None if workflow_status in {"success", "dry_run"} else "workflow_failed",
    )
    return {
        "core": core,
        "core_digest": core_digest,
        "timing": {
            "max_in_flight": max_in_flight,
            "events": events,
        },
    }


__all__ = [
    "LocalExecutionGrant",
    "RuntimeTaskBinding",
    "TaskContext",
    "WorkflowGraphRuntimeError",
    "execute_workflow_plan_v1",
]
