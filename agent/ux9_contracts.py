"""Machine-readable UX 9 verifier-first contracts.

These contracts are intentionally small and dependency-free.  They bind the
Hermes UX 9 SSOT to concrete, testable fields before broader behavior changes
land.  The goal is not to implement the full dashboard/device/taskpool UX in
one patch; it is to make the non-negotiable governance boundaries executable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

UX9_SSOT_PATH = "/Users/charles/.hermes/docs/private/hermes-ux-9-stable-optimization-20260531.md"
UX9_CONTRACT_VERSION = "ux9_verifier_contracts_v1"


@dataclass(frozen=True)
class DiagnoseContract:
    """Explainable diagnosis payload contract for Hermes/edge failures."""

    schema: str = "diagnose_contract_v1"
    ssot_ref: str = UX9_SSOT_PATH
    reason_code: str = ""
    recovery_actions: tuple[str, ...] = ()
    risk_level: str = "unknown"
    evidence_refs: tuple[str, ...] = ()
    _required_fields: ClassVar[tuple[str, ...]] = (
        "schema",
        "ssot_ref",
        "reason_code",
        "recovery_actions",
        "risk_level",
        "evidence_refs",
    )

    def validate(self) -> list[str]:
        failures = _require_fields(self.to_dict(), self._required_fields)
        failures.extend(_require_structured_evidence(self.evidence_refs))
        if self.risk_level not in {"low", "medium", "high", "critical", "unknown"}:
            failures.append("risk_level must be low|medium|high|critical|unknown")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ssot_ref": self.ssot_ref,
            "reason_code": self.reason_code,
            "recovery_actions": list(self.recovery_actions),
            "risk_level": self.risk_level,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class DeviceTwinContract:
    """Reported-only device twin boundary.

    UX9 explicitly separates desired state from reported/observed/verified
    state.  This contract rejects desired-state fields so read-side projections
    cannot silently become command authority.
    """

    schema: str = "device_twin_reported_only_v1"
    ssot_ref: str = UX9_SSOT_PATH
    device_id: str = ""
    reported_state: dict[str, Any] = field(default_factory=dict)
    observed_state: dict[str, Any] = field(default_factory=dict)
    verified_state: dict[str, Any] = field(default_factory=dict)
    trust_level: str = "unknown"
    health: str = "unknown"
    capabilities: tuple[str, ...] = ()
    _required_fields: ClassVar[tuple[str, ...]] = (
        "schema",
        "ssot_ref",
        "device_id",
        "reported_state",
        "observed_state",
        "verified_state",
        "trust_level",
        "health",
        "capabilities",
    )

    def validate(self) -> list[str]:
        data = self.to_dict()
        failures = _require_fields(data, self._required_fields)
        forbidden = {"desired_state", "commands", "write_intent"} & set(data)
        if forbidden:
            failures.append(f"reported-only twin forbids desired/control fields: {sorted(forbidden)}")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ssot_ref": self.ssot_ref,
            "device_id": self.device_id,
            "reported_state": dict(self.reported_state),
            "observed_state": dict(self.observed_state),
            "verified_state": dict(self.verified_state),
            "trust_level": self.trust_level,
            "health": self.health,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class TaskLedgerContract:
    """Explainable, recoverable task transition contract."""

    schema: str = "task_ledger_explainability_v1"
    ssot_ref: str = UX9_SSOT_PATH
    task_id: str = ""
    from_status: str = ""
    to_status: str = ""
    reason_code: str = ""
    attempt_id: str = ""
    lease_epoch: int = 0
    idempotency_key: str = ""
    evidence_refs: tuple[str, ...] = ()
    recovery_proposal: str = ""
    _required_fields: ClassVar[tuple[str, ...]] = (
        "schema",
        "ssot_ref",
        "task_id",
        "from_status",
        "to_status",
        "reason_code",
        "attempt_id",
        "lease_epoch",
        "idempotency_key",
        "evidence_refs",
    )

    def validate(self) -> list[str]:
        failures = _require_fields(self.to_dict(), self._required_fields)
        failures.extend(_require_structured_evidence(self.evidence_refs))
        if self.lease_epoch < 0:
            failures.append("lease_epoch must be non-negative")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ssot_ref": self.ssot_ref,
            "task_id": self.task_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "reason_code": self.reason_code,
            "attempt_id": self.attempt_id,
            "lease_epoch": self.lease_epoch,
            "idempotency_key": self.idempotency_key,
            "evidence_refs": list(self.evidence_refs),
            "recovery_proposal": self.recovery_proposal,
        }


@dataclass(frozen=True)
class MultiModelGovernanceContract:
    """User-facing governance contract for multimodel outcomes."""

    schema: str = "multimodel_governance_ux_v1"
    ssot_ref: str = UX9_SSOT_PATH
    status: str = "degraded"
    evidence_coverage: str = "unknown"
    model_disagreement: str = "unknown"
    fallback_reason: str = ""
    action_restrictions: tuple[str, ...] = ()
    advisory_only: bool = True
    _required_fields: ClassVar[tuple[str, ...]] = (
        "schema",
        "ssot_ref",
        "status",
        "evidence_coverage",
        "model_disagreement",
        "action_restrictions",
        "advisory_only",
    )

    def validate(self) -> list[str]:
        data = self.to_dict()
        failures = _require_fields(data, self._required_fields)
        if self.status not in {"certified", "degraded", "single", "failed"}:
            failures.append("status must be certified|degraded|single|failed")
        if self.status != "certified" and not self.advisory_only:
            failures.append("non-certified multimodel outcomes must remain advisory_only")
        if self.status != "certified" and not self.action_restrictions:
            failures.append("non-certified multimodel outcomes require action_restrictions")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ssot_ref": self.ssot_ref,
            "status": self.status,
            "evidence_coverage": self.evidence_coverage,
            "model_disagreement": self.model_disagreement,
            "fallback_reason": self.fallback_reason,
            "action_restrictions": list(self.action_restrictions),
            "advisory_only": self.advisory_only,
        }


def ux9_contract_manifest() -> dict[str, Any]:
    """Return the canonical machine-readable contract manifest."""

    return {
        "schema": UX9_CONTRACT_VERSION,
        "ssot_ref": UX9_SSOT_PATH,
        "contracts": {
            "diagnose": DiagnoseContract._required_fields,
            "device_twin": DeviceTwinContract._required_fields,
            "task_ledger": TaskLedgerContract._required_fields,
            "multimodel_governance": MultiModelGovernanceContract._required_fields,
        },
        "invariants": [
            "No silent downgrade.",
            "No trust without evidence.",
            "No task done without evidence_ref.",
            "No degraded advisory triggering high-risk execution.",
        ],
    }


def _require_fields(data: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [f"missing required field: {name}" for name in fields if name not in data]


def _require_structured_evidence(evidence_refs: tuple[str, ...]) -> list[str]:
    allowed = ("artifact:", "command:", "proof:", "classification:")
    failures: list[str] = []
    if not evidence_refs:
        failures.append("evidence_refs must not be empty")
    for ref in evidence_refs:
        if not isinstance(ref, str) or not ref.startswith(allowed):
            failures.append(f"evidence_ref is not structured: {ref!r}")
    return failures
