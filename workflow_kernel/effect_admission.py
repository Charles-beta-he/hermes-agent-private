"""Experimental preflight eligibility precursor for effect candidates.

Scope. This module decides whether an effect *candidate* is structurally
eligible to be considered later. It never applies an effect: it has no sink,
no adapter registry, no ledger writer, and no filesystem or network access.
Importing it has no side effect, and nothing here is reachable from the
governed runtime, a manifest, or an ExecuteGrant.

Default-off. Every capability-issuing and eligibility-deciding entry point is
closed unless `HGWK_EFFECT_PRECURSOR_ENABLED` is explicitly set to a truthy
value. Unset, empty and falsey values are typed rejections. The separate
`HGWK_EFFECT_PRECURSOR_KILL_SWITCH` closes the precursor independently.

Authority limitation. `ExperimentalHarnessExactAuthorizationV1` is a
test/local harness authorization: a process-local, short-lived, one-shot,
non-serializable input issued by this module for harness use only. It is not
a Governance Control Plane capability and it does not satisfy the SSOT
section 6.4 production authority chain (parent, workflow, node and fresh
grant intersection). A candidate's `authority_request` is a *request*, never
a grant; nothing in this module can authorize itself.

Result. A successful preflight returns an in-memory
`ExperimentalEffectEligibilityReceiptV1` whose decision is
`eligible_pending_authority_and_sink` and whose result classification is
fixed at `not_applied`. It is not an EffectReceiptV1, it is not evidence that
anything ran, and it must never be treated as either.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Mapping, NoReturn
from weakref import WeakValueDictionary

_CANDIDATE_SCHEMA_VERSION = "effect-admission-candidate-experimental-v1"
_AUTHORIZATION_SCHEMA_VERSION = "harness-exact-authorization-experimental-v1"
_GRANT_SCHEMA_VERSION = "effect-preflight-grant-experimental-v1"
_RECEIPT_SCHEMA_VERSION = "effect-preflight-eligibility-receipt-experimental-v1"

_ENABLED_ENV = "HGWK_EFFECT_PRECURSOR_ENABLED"
_ENABLED_ON_VALUES = frozenset({"1", "true", "yes", "on"})
_KILL_SWITCH_ENV = "HGWK_EFFECT_PRECURSOR_KILL_SWITCH"
_KILL_SWITCH_OFF_VALUES = frozenset({"", "0", "false", "no", "off"})

_TARGET_KEYS = ("target_kind", "target_ref")
_AUTHORITY_REQUEST_KEYS = (
    "request_id",
    "side_effect_tier",
    "requested_operations",
    "requested_target_refs",
)
_DECLARED_SIDE_EFFECT_TIERS = (
    "local_read",
    "local_write",
    "external_read",
    "external_write",
)
# Eligibility stops at local tiers; external tiers stay closed until a
# certified sink, readback and reconcile path exist.
_ELIGIBLE_SIDE_EFFECT_TIERS = frozenset({"local_read", "local_write"})
_BROAD_SCOPE_TOKENS = frozenset({"*", "**", "all", "any", "full_local", "full-local"})

_MAX_TTL_SECONDS = 60.0
_ELIGIBILITY_DECISION = "eligible_pending_authority_and_sink"
_RESULT_CLASSIFICATION = "not_applied"


class EffectAdmissionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise EffectAdmissionError(code, message)


def _compact_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(
            "effect_candidate_schema_invalid", f"candidate is not canonical JSON: {exc}"
        )


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_compact_json(value)).hexdigest()}"


# --- default-off gates ---------------------------------------------------------


def _reject_if_precursor_disabled() -> None:
    value = os.environ.get(_ENABLED_ENV, "")
    if value.strip().lower() not in _ENABLED_ON_VALUES:
        _fail(
            "effect_precursor_disabled",
            f"effect precursor is off unless {_ENABLED_ENV} is explicitly enabled",
        )


def _reject_if_kill_switch_engaged() -> None:
    value = os.environ.get(_KILL_SWITCH_ENV, "")
    if value.strip().lower() not in _KILL_SWITCH_OFF_VALUES:
        _fail(
            "effect_precursor_kill_switch_engaged",
            "effect precursor is disabled by kill switch",
        )


# --- one-shot tickets and the process-local issuance registry -----------------


@dataclass
class _OneShotTicket:
    lock: Lock = field(default_factory=Lock)
    consumed: bool = False
    creator_pid: int = field(default_factory=os.getpid)


# Only tickets issued by the factories below are registered. Identity, not a
# serializable bearer token, is what makes a capability real here.
_REGISTRY_LOCK = Lock()
_AUTHORIZATION_TICKETS: WeakValueDictionary[str, _OneShotTicket] = WeakValueDictionary()
_GRANT_TICKETS: WeakValueDictionary[str, _OneShotTicket] = WeakValueDictionary()


def _register(
    registry: WeakValueDictionary[str, _OneShotTicket],
    nonce: str,
    ticket: _OneShotTicket,
) -> None:
    with _REGISTRY_LOCK:
        registry[nonce] = ticket


def _is_registered(
    registry: WeakValueDictionary[str, _OneShotTicket],
    nonce: str,
    ticket: _OneShotTicket,
) -> bool:
    with _REGISTRY_LOCK:
        return registry.get(nonce) is ticket


def _revoke(registry: WeakValueDictionary[str, _OneShotTicket], nonce: str) -> None:
    with _REGISTRY_LOCK:
        registry.pop(nonce, None)


# --- capability objects --------------------------------------------------------


@dataclass(frozen=True)
class EffectAdmissionCandidateV1:
    """Exact, inert description of one effect an adapter could later apply.

    `authority_request` states what the caller *asks for*. It is never treated
    as proof that the caller holds that authority.
    """

    schema_version: str
    adapter_id: str
    operation: str
    target: Mapping[str, Any]
    payload_digest: str
    authority_request: Mapping[str, Any]
    idempotency_key: str
    compensation_ref: str
    reconcile_ref: str


@dataclass(frozen=True)
class ExperimentalHarnessExactAuthorizationV1:
    """Harness-only exact authorization input. Not a production AuthorityGrant.

    Process-local, short-lived, one-shot, non-serializable, and valid only for
    the exact adapter, operation, target, payload and side effect tier it was
    issued for. It does not satisfy the SSOT section 6.4 authority chain.
    """

    schema_version: str
    nonce: str = field(repr=False, compare=False)
    adapter_id: str
    operation: str
    target_digest: str
    payload_digest: str
    side_effect_tier: str
    expires_at_monotonic: float
    _ticket: _OneShotTicket = field(repr=False, compare=False)

    def __reduce__(self) -> NoReturn:
        _fail(
            "effect_harness_authorization_not_serializable",
            "harness authorization cannot be serialized into bearer authority",
        )

    def __getstate__(self) -> NoReturn:
        _fail(
            "effect_harness_authorization_not_serializable",
            "harness authorization cannot be serialized into bearer authority",
        )

    def __copy__(self) -> NoReturn:
        _fail(
            "effect_harness_authorization_not_serializable",
            "harness authorization cannot be copied into a second capability",
        )

    def __deepcopy__(self, memo: dict) -> NoReturn:
        _fail(
            "effect_harness_authorization_not_serializable",
            "harness authorization cannot be copied into a second capability",
        )


@dataclass(frozen=True)
class ExperimentalEffectPreflightGrantV1:
    """Fresh, process-bound, one-shot preflight capability. Never serialized."""

    schema_version: str
    nonce: str = field(repr=False, compare=False)
    candidate_digest: str
    adapter_id: str
    operation: str
    target_digest: str
    payload_digest: str
    authority_request_digest: str
    expires_at_monotonic: float
    _ticket: _OneShotTicket = field(repr=False, compare=False)

    def __reduce__(self) -> NoReturn:
        _fail(
            "effect_preflight_grant_not_serializable",
            "preflight grant cannot be serialized into bearer authority",
        )

    def __getstate__(self) -> NoReturn:
        _fail(
            "effect_preflight_grant_not_serializable",
            "preflight grant cannot be serialized into bearer authority",
        )

    def __copy__(self) -> NoReturn:
        _fail(
            "effect_preflight_grant_not_serializable",
            "preflight grant cannot be copied into a second capability",
        )

    def __deepcopy__(self, memo: dict) -> NoReturn:
        _fail(
            "effect_preflight_grant_not_serializable",
            "preflight grant cannot be copied into a second capability",
        )


@dataclass(frozen=True)
class ExperimentalEffectEligibilityReceiptV1:
    """Pure eligibility result. Nothing was authorized in production terms and
    nothing was applied."""

    schema_version: str
    decision: str
    result_classification: str
    applied: bool
    candidate_digest: str
    adapter_id: str
    operation: str
    target_digest: str
    payload_digest: str
    authority_request_digest: str
    idempotency_key: str
    compensation_ref: str
    reconcile_ref: str
    receipt_digest: str


# --- candidate validation ------------------------------------------------------


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("effect_candidate_schema_invalid", f"{label} must be a non-empty string")
    return value


def _require_digest(value: object, *, label: str) -> str:
    text = _require_text(value, label=label)
    body = text[len("sha256:") :] if text.startswith("sha256:") else ""
    if len(body) != 64 or any(char not in "0123456789abcdef" for char in body):
        _fail(
            "effect_candidate_schema_invalid",
            f"{label} must be a lowercase sha256:<64 hex> digest",
        )
    return text


def _require_exact_mapping(value: object, keys: tuple[str, ...], *, label: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail(
            "effect_candidate_schema_invalid",
            f"{label} must bind exactly {sorted(keys)}",
        )
    return dict(value)


def _require_text_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        _fail("effect_candidate_schema_invalid", f"{label} must be a non-empty list")
    return [_require_text(item, label=f"{label} entry") for item in value]


def _require_side_effect_tier(value: object, *, label: str) -> str:
    tier = _require_text(value, label=label)
    if tier not in _DECLARED_SIDE_EFFECT_TIERS:
        _fail(
            "effect_candidate_schema_invalid",
            f"{label} must be one of {list(_DECLARED_SIDE_EFFECT_TIERS)}",
        )
    return tier


def _validated_target(value: object) -> dict:
    target = _require_exact_mapping(value, _TARGET_KEYS, label="target")
    for key in _TARGET_KEYS:
        target[key] = _require_text(target[key], label=f"target.{key}")
    return target


def _validated_candidate_body(candidate: object) -> dict:
    if not isinstance(candidate, EffectAdmissionCandidateV1):
        _fail(
            "effect_candidate_schema_invalid",
            "candidate must be an EffectAdmissionCandidateV1",
        )
    if candidate.schema_version != _CANDIDATE_SCHEMA_VERSION:
        _fail(
            "effect_candidate_schema_invalid",
            f"candidate schema_version must be {_CANDIDATE_SCHEMA_VERSION}",
        )

    target = _validated_target(candidate.target)
    request = _require_exact_mapping(
        candidate.authority_request, _AUTHORITY_REQUEST_KEYS, label="authority_request"
    )
    request["request_id"] = _require_text(
        request["request_id"], label="authority_request.request_id"
    )
    request["side_effect_tier"] = _require_side_effect_tier(
        request["side_effect_tier"], label="authority_request.side_effect_tier"
    )
    request["requested_operations"] = _require_text_list(
        request["requested_operations"], label="authority_request.requested_operations"
    )
    request["requested_target_refs"] = _require_text_list(
        request["requested_target_refs"],
        label="authority_request.requested_target_refs",
    )

    return {
        "schema_version": candidate.schema_version,
        "adapter_id": _require_text(candidate.adapter_id, label="adapter_id"),
        "operation": _require_text(candidate.operation, label="operation"),
        "target": target,
        "payload_digest": _require_digest(
            candidate.payload_digest, label="payload_digest"
        ),
        "authority_request": request,
        "idempotency_key": _require_text(
            candidate.idempotency_key, label="idempotency_key"
        ),
        "compensation_ref": _require_text(
            candidate.compensation_ref, label="compensation_ref"
        ),
        "reconcile_ref": _require_text(candidate.reconcile_ref, label="reconcile_ref"),
    }


def _reject_broad_authority_request(request: Mapping[str, Any]) -> None:
    entries = list(request["requested_operations"]) + list(
        request["requested_target_refs"]
    )
    for entry in entries:
        if entry.strip().lower() in _BROAD_SCOPE_TOKENS:
            _fail(
                "effect_authority_request_broad",
                "a preflight grant cannot be derived from a broad authority request",
            )


def _reject_request_violation(body: Mapping[str, Any]) -> None:
    request = body["authority_request"]
    if body["operation"] not in request["requested_operations"]:
        _fail(
            "effect_authority_operation_not_permitted",
            "operation is outside the candidate authority request",
        )
    if body["target"]["target_ref"] not in request["requested_target_refs"]:
        _fail(
            "effect_authority_target_not_permitted",
            "target_ref is outside the candidate authority request",
        )
    if request["side_effect_tier"] not in _ELIGIBLE_SIDE_EFFECT_TIERS:
        _fail(
            "effect_side_effect_tier_not_open",
            f"side effect tier {request['side_effect_tier']} is closed in this precursor",
        )


def _finite_seconds(value: object, *, code: str, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code, f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        _fail(code, f"{label} must be a finite number")
    return number


def derive_effect_admission_candidate_digest_v1(
    candidate: EffectAdmissionCandidateV1,
) -> str:
    """Validate the candidate contract and return its canonical digest."""
    return _digest(_validated_candidate_body(candidate))


# --- harness-only exact authorization -----------------------------------------


def issue_experimental_harness_exact_authorization_v1(
    *,
    adapter_id: str,
    operation: str,
    target: Mapping[str, Any],
    payload_digest: str,
    side_effect_tier: str,
    ttl_seconds: float,
) -> ExperimentalHarnessExactAuthorizationV1:
    """Issue a harness-only, exact, one-shot authorization input.

    This is a test/local harness authorization. It is not a Governance Control
    Plane capability and does not satisfy the SSOT section 6.4 production
    authority chain. It exists so a preflight grant can never be minted from a
    candidate's own self-declared request.
    """
    _reject_if_precursor_disabled()
    _reject_if_kill_switch_engaged()

    ttl = _validated_ttl(ttl_seconds, code="effect_harness_authorization_ttl_invalid")
    ticket = _OneShotTicket()
    authorization = ExperimentalHarnessExactAuthorizationV1(
        schema_version=_AUTHORIZATION_SCHEMA_VERSION,
        nonce=secrets.token_hex(16),
        adapter_id=_require_text(adapter_id, label="adapter_id"),
        operation=_require_text(operation, label="operation"),
        target_digest=_digest(_validated_target(target)),
        payload_digest=_require_digest(payload_digest, label="payload_digest"),
        side_effect_tier=_require_side_effect_tier(
            side_effect_tier, label="side_effect_tier"
        ),
        expires_at_monotonic=time.monotonic() + ttl,
        _ticket=ticket,
    )
    _register(_AUTHORIZATION_TICKETS, authorization.nonce, ticket)
    return authorization


def _validated_ttl(value: object, *, code: str = "effect_grant_ttl_invalid") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code, "ttl_seconds must be a number")
    ttl = float(value)
    if not math.isfinite(ttl) or not 0 < ttl <= _MAX_TTL_SECONDS:
        _fail(code, f"ttl_seconds must be finite and within (0, {_MAX_TTL_SECONDS}]")
    return ttl


def _typed_authorization(
    authorization: object,
) -> ExperimentalHarnessExactAuthorizationV1:
    if not isinstance(authorization, ExperimentalHarnessExactAuthorizationV1):
        _fail(
            "effect_harness_authorization_invalid",
            "authorization must be an ExperimentalHarnessExactAuthorizationV1",
        )
    if authorization.schema_version != _AUTHORIZATION_SCHEMA_VERSION:
        _fail(
            "effect_harness_authorization_invalid",
            f"authorization schema_version must be {_AUTHORIZATION_SCHEMA_VERSION}",
        )
    return authorization


def _reject_authorization_mismatch(
    body: Mapping[str, Any], authorization: ExperimentalHarnessExactAuthorizationV1
) -> None:
    """The authorization must cover exactly the requested effect, nothing wider."""
    request = body["authority_request"]
    exact = (
        (body["adapter_id"], authorization.adapter_id),
        (body["operation"], authorization.operation),
        (_digest(body["target"]), authorization.target_digest),
        (body["payload_digest"], authorization.payload_digest),
        (request["side_effect_tier"], authorization.side_effect_tier),
    )
    for requested, authorized in exact:
        if requested != authorized:
            _fail(
                "effect_harness_authorization_mismatch",
                "authorization does not match the candidate request exactly",
            )
    if authorization.operation not in request["requested_operations"]:
        _fail(
            "effect_harness_authorization_mismatch",
            "authorization operation is outside the candidate request",
        )


def _reject_stale_ticket_state(
    registry: WeakValueDictionary[str, _OneShotTicket],
    nonce: str,
    ticket: _OneShotTicket,
    expires_at_monotonic: float,
    codes: Mapping[str, str],
) -> None:
    """Check clock, process, one-shot state, issuance and expiry. Caller holds
    the ticket lock."""
    now = _finite_seconds(time.monotonic(), code=codes["clock"], label="now")
    if ticket.creator_pid != os.getpid():
        _fail(codes["process"], "capability cannot be used outside its process")
    if ticket.consumed:
        _fail(codes["reused"], "capability has already been consumed")
    if not _is_registered(registry, nonce, ticket):
        _fail(codes["forged"], "capability was not issued by this module")
    if now > expires_at_monotonic:
        _fail(codes["expired"], "capability has expired")


def _validate_ticket_without_consuming(
    registry: WeakValueDictionary[str, _OneShotTicket],
    nonce: str,
    ticket: _OneShotTicket,
    expires_at_monotonic: float,
    codes: Mapping[str, str],
) -> None:
    """Run every ticket gate under the ticket lock, consuming nothing.

    Expiry, one-shot state and issuance identity are read under the same lock
    the consume path takes, so a state-only validation can never observe a
    half-consumed ticket or skip the freshness check. Freshness is measured
    against this module's own `time.monotonic()`; no caller supplies that clock.
    A rejection leaves the ticket exactly as it was, including its registration.
    """
    with ticket.lock:
        _reject_stale_ticket_state(registry, nonce, ticket, expires_at_monotonic, codes)


def _consume_ticket(
    registry: WeakValueDictionary[str, _OneShotTicket],
    nonce: str,
    ticket: _OneShotTicket,
    expires_at_monotonic: float,
    codes: Mapping[str, str],
) -> None:
    """Final commit region: every gate and the one-shot consume under one lock.

    Freshness is measured against this module's own `time.monotonic()`. No
    caller supplies the clock that decides whether a short-lived capability has
    expired, and a clock that is not a finite number fails closed.
    """
    with ticket.lock:
        try:
            _reject_if_precursor_disabled()
            _reject_if_kill_switch_engaged()
        except EffectAdmissionError:
            _revoke(registry, nonce)
            raise
        try:
            _reject_stale_ticket_state(
                registry, nonce, ticket, expires_at_monotonic, codes
            )
        except EffectAdmissionError as error:
            if error.code == codes["expired"]:
                _revoke(registry, nonce)
            raise
        ticket.consumed = True
        _revoke(registry, nonce)


def mint_effect_preflight_grant_v1(
    *,
    candidate: EffectAdmissionCandidateV1,
    authorization: ExperimentalHarnessExactAuthorizationV1,
    ttl_seconds: float,
) -> ExperimentalEffectPreflightGrantV1:
    """Mint a preflight grant by consuming an independent exact authorization.

    A candidate can never authorize itself: without a separately issued,
    unconsumed, exactly matching harness authorization this call fails closed.
    """
    _reject_if_precursor_disabled()
    _reject_if_kill_switch_engaged()

    body = _validated_candidate_body(candidate)
    _reject_broad_authority_request(body["authority_request"])
    _reject_request_violation(body)

    checked = _typed_authorization(authorization)
    _reject_authorization_mismatch(body, checked)
    ttl = _validated_ttl(ttl_seconds)

    _consume_ticket(
        _AUTHORIZATION_TICKETS,
        checked.nonce,
        checked._ticket,
        checked.expires_at_monotonic,
        {
            "process": "effect_harness_authorization_process_mismatch",
            "reused": "effect_harness_authorization_reused",
            "forged": "effect_harness_authorization_not_registered",
            "expired": "effect_harness_authorization_expired",
            "clock": "effect_harness_authorization_clock_invalid",
        },
    )

    ticket = _OneShotTicket()
    grant = ExperimentalEffectPreflightGrantV1(
        schema_version=_GRANT_SCHEMA_VERSION,
        nonce=secrets.token_hex(16),
        candidate_digest=_digest(body),
        adapter_id=body["adapter_id"],
        operation=body["operation"],
        target_digest=_digest(body["target"]),
        payload_digest=body["payload_digest"],
        authority_request_digest=_digest(body["authority_request"]),
        expires_at_monotonic=time.monotonic() + ttl,
        _ticket=ticket,
    )
    _register(_GRANT_TICKETS, grant.nonce, ticket)
    return grant


# --- package-private bridge for the experimental local-write harness ----------

# The bridge below is the only way an exact harness authorization can be
# consumed outside this module, and it is deliberately narrow: one fixed
# adapter, two fixed operations, one fixed target kind and one fixed local
# tier. It cannot be used to consume authority for anything else, and it never
# mints authority from a candidate.
_LOCAL_HARNESS_ADAPTER_ID = "experimental.local_private_json_v1"
_LOCAL_HARNESS_OPERATIONS = frozenset({"write_json", "restore_json"})
_LOCAL_HARNESS_TARGET_KIND = "local_private_json"
_LOCAL_HARNESS_SIDE_EFFECT_TIER = "local_write"
_LOCAL_HARNESS_TICKET_CODES = {
    "process": "effect_harness_authorization_process_mismatch",
    "reused": "effect_harness_authorization_reused",
    "forged": "effect_harness_authorization_not_registered",
    "expired": "effect_harness_authorization_expired",
    "clock": "effect_harness_authorization_clock_invalid",
}


def _validate_local_harness_authorization_v1(
    authorization: object,
    *,
    operation: str,
    target: Mapping[str, Any],
    payload_digest: str,
) -> str:
    """Validate one local-harness authorization *without* consuming it.

    Returns the authorization binding digest. A forged, copied, mismatched,
    expired, already consumed or out-of-process object fails closed here, so a
    caller can fail closed before it mutates anything and without spending the
    one-shot capability on a check that changes no state. Expiry, one-shot
    state and issuance identity are read under the ticket's own lock against
    this module's `time.monotonic()`, so a stale capability cannot buy even a
    state-only answer such as a receipt replay. Validation is not a
    reservation: only `_commit_local_harness_authorization_v1` consumes, and it
    rechecks every gate freshly under the ticket lock.
    """
    _reject_if_precursor_disabled()
    _reject_if_kill_switch_engaged()
    checked = _typed_authorization(authorization)
    if operation not in _LOCAL_HARNESS_OPERATIONS:
        _fail(
            "effect_local_harness_operation_not_allowed",
            f"local harness bridge only covers {sorted(_LOCAL_HARNESS_OPERATIONS)}",
        )
    if checked.adapter_id != _LOCAL_HARNESS_ADAPTER_ID:
        _fail(
            "effect_local_harness_adapter_not_allowed",
            f"local harness bridge only covers {_LOCAL_HARNESS_ADAPTER_ID}",
        )
    if checked.side_effect_tier != _LOCAL_HARNESS_SIDE_EFFECT_TIER:
        _fail(
            "effect_side_effect_tier_not_open",
            "local harness bridge only covers the local_write tier",
        )
    validated_target = _validated_target(target)
    if validated_target["target_kind"] != _LOCAL_HARNESS_TARGET_KIND:
        _fail(
            "effect_local_harness_target_kind_not_allowed",
            f"local harness bridge only covers {_LOCAL_HARNESS_TARGET_KIND} targets",
        )
    exact = (
        (operation, checked.operation),
        (_digest(validated_target), checked.target_digest),
        (
            _require_digest(payload_digest, label="payload_digest"),
            checked.payload_digest,
        ),
    )
    for requested, authorized in exact:
        if requested != authorized:
            _fail(
                "effect_harness_authorization_mismatch",
                "authorization does not match the local harness request exactly",
            )
    _validate_ticket_without_consuming(
        _AUTHORIZATION_TICKETS,
        checked.nonce,
        checked._ticket,
        checked.expires_at_monotonic,
        _LOCAL_HARNESS_TICKET_CODES,
    )
    return _local_harness_binding_digest(checked)


def _local_harness_binding_digest(
    authorization: ExperimentalHarnessExactAuthorizationV1,
) -> str:
    return _digest({
        "schema_version": authorization.schema_version,
        "adapter_id": authorization.adapter_id,
        "operation": authorization.operation,
        "target_digest": authorization.target_digest,
        "payload_digest": authorization.payload_digest,
        "side_effect_tier": authorization.side_effect_tier,
    })


def _commit_local_harness_authorization_v1(
    authorization: object,
    *,
    operation: str,
    target: Mapping[str, Any],
    payload_digest: str,
) -> str:
    """Revalidate and one-shot consume one local-harness authorization.

    Callers validate first and mutate nothing until this commit succeeds. The
    consume itself rechecks the precursor flag, kill switch, issuing process,
    issuance registry and expiry freshly under the ticket's own lock, so a gate
    closed after validation still fails closed here. Expiry is measured against
    this module's own `time.monotonic()`: no caller may supply the clock that
    decides whether a short-lived authorization is still fresh.
    """
    binding_digest = _validate_local_harness_authorization_v1(
        authorization,
        operation=operation,
        target=target,
        payload_digest=payload_digest,
    )
    checked = _typed_authorization(authorization)
    _consume_ticket(
        _AUTHORIZATION_TICKETS,
        checked.nonce,
        checked._ticket,
        checked.expires_at_monotonic,
        _LOCAL_HARNESS_TICKET_CODES,
    )
    return binding_digest


# --- preflight eligibility -----------------------------------------------------


def _typed_grant(grant: object) -> ExperimentalEffectPreflightGrantV1:
    if not isinstance(grant, ExperimentalEffectPreflightGrantV1):
        _fail(
            "effect_preflight_grant_invalid",
            "grant must be an ExperimentalEffectPreflightGrantV1 minted here",
        )
    return grant


def _reject_binding_mismatch(
    body: Mapping[str, Any], grant: ExperimentalEffectPreflightGrantV1
) -> None:
    bound = (
        (_digest(body), grant.candidate_digest),
        (body["adapter_id"], grant.adapter_id),
        (body["operation"], grant.operation),
        (_digest(body["target"]), grant.target_digest),
        (body["payload_digest"], grant.payload_digest),
        (_digest(body["authority_request"]), grant.authority_request_digest),
    )
    for candidate_value, granted_value in bound:
        if candidate_value != granted_value:
            _fail(
                "effect_candidate_binding_mismatch",
                "candidate does not match the exact bindings of this grant",
            )


def preflight_effect_admission_v1(
    candidate: EffectAdmissionCandidateV1,
    grant: ExperimentalEffectPreflightGrantV1,
) -> ExperimentalEffectEligibilityReceiptV1:
    """Decide whether a candidate is eligible, without applying anything.

    Binding and request checks run first and leave zero state behind, so a
    rejected probe never consumes the one-shot grant. Feature flag, kill
    switch, clock, process, expiry, issuance identity and the one-shot consume
    all commit under the grant's own ticket lock. Expiry is measured against
    this module's own `time.monotonic()`: a caller cannot rewind the clock to
    spend an expired grant or to obtain an eligibility receipt from one.
    """
    body = _validated_candidate_body(candidate)
    checked = _typed_grant(grant)
    _reject_binding_mismatch(body, checked)
    _reject_request_violation(body)

    _consume_ticket(
        _GRANT_TICKETS,
        checked.nonce,
        checked._ticket,
        checked.expires_at_monotonic,
        {
            "process": "effect_preflight_grant_process_mismatch",
            "reused": "effect_preflight_grant_reused",
            "forged": "effect_preflight_grant_not_registered",
            "expired": "effect_preflight_grant_expired",
            "clock": "effect_preflight_clock_invalid",
        },
    )

    receipt_body = {
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "decision": _ELIGIBILITY_DECISION,
        "result_classification": _RESULT_CLASSIFICATION,
        "applied": False,
        "candidate_digest": checked.candidate_digest,
        "adapter_id": body["adapter_id"],
        "operation": body["operation"],
        "target_digest": checked.target_digest,
        "payload_digest": body["payload_digest"],
        "authority_request_digest": checked.authority_request_digest,
        "idempotency_key": body["idempotency_key"],
        "compensation_ref": body["compensation_ref"],
        "reconcile_ref": body["reconcile_ref"],
    }
    return ExperimentalEffectEligibilityReceiptV1(
        **receipt_body, receipt_digest=_digest(receipt_body)
    )
