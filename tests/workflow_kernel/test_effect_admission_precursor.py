"""Experimental preflight eligibility precursor: pure, default-off, no apply.

These tests bind an *eligibility* contract only. Nothing here applies an
effect, writes a ledger event, touches the filesystem, or opens a socket.
The harness authorization used below is a test/local harness input; it does
not satisfy the SSOT section 6.4 production authority chain.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import math
import pickle
import socket
import threading
import types
from pathlib import Path

import pytest

from workflow_kernel import effect_admission
from workflow_kernel.effect_admission import (
    EffectAdmissionCandidateV1,
    EffectAdmissionError,
    ExperimentalEffectEligibilityReceiptV1,
    ExperimentalEffectPreflightGrantV1,
    ExperimentalHarnessExactAuthorizationV1,
    derive_effect_admission_candidate_digest_v1,
    issue_experimental_harness_exact_authorization_v1,
    mint_effect_preflight_grant_v1,
    preflight_effect_admission_v1,
)

ENABLE_ENV = "HGWK_EFFECT_PRECURSOR_ENABLED"
KILL_SWITCH_ENV = "HGWK_EFFECT_PRECURSOR_KILL_SWITCH"

_PAYLOAD_DIGEST = "sha256:" + "11" * 32
_OTHER_PAYLOAD_DIGEST = "sha256:" + "22" * 32

# Digest of the withdrawn self-authorizing `authority_scope` candidate body.
_WITHDRAWN_SELF_AUTHORIZING_DIGEST = (
    "sha256:752918907c8a247f71828820da87a8edf54cd798f61fb7e35a136fdf3b5467c0"
)

MODULE_SOURCE = Path("workflow_kernel/effect_admission.py").read_text(encoding="utf-8")
TEST_SOURCE = Path(
    "tests/workflow_kernel/test_effect_admission_precursor.py"
).read_text(encoding="utf-8")


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)


def _candidate(**overrides) -> EffectAdmissionCandidateV1:
    base = EffectAdmissionCandidateV1(
        schema_version="effect-admission-candidate-experimental-v1",
        adapter_id="local.file_note",
        operation="write_note",
        target={
            "target_kind": "local_private_file",
            "target_ref": "notes/hgwk-precursor.json",
        },
        payload_digest=_PAYLOAD_DIGEST,
        authority_request={
            "request_id": "request:hgwk-precursor-note",
            "side_effect_tier": "local_write",
            "requested_operations": ["write_note"],
            "requested_target_refs": ["notes/hgwk-precursor.json"],
        },
        idempotency_key="hgwk-precursor-note-0001",
        compensation_ref="compensation:local.file_note.restore_previous",
        reconcile_ref="reconcile:local.file_note.readback",
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def _authorization(
    candidate: EffectAdmissionCandidateV1, *, ttl_seconds: float = 5.0, **overrides
) -> ExperimentalHarnessExactAuthorizationV1:
    kwargs = {
        "adapter_id": candidate.adapter_id,
        "operation": candidate.operation,
        "target": dict(candidate.target),
        "payload_digest": candidate.payload_digest,
        "side_effect_tier": candidate.authority_request["side_effect_tier"],
        "ttl_seconds": ttl_seconds,
    }
    kwargs.update(overrides)
    return issue_experimental_harness_exact_authorization_v1(**kwargs)


def _grant(
    candidate: EffectAdmissionCandidateV1, *, ttl_seconds: float = 5.0
) -> ExperimentalEffectPreflightGrantV1:
    return mint_effect_preflight_grant_v1(
        candidate=candidate,
        authorization=_authorization(candidate),
        ttl_seconds=ttl_seconds,
    )


def _forge(original):
    """Rebuild an object's public bindings with a counterfeit one-shot ticket."""
    forged = object.__new__(type(original))
    for field in dataclasses.fields(original):
        value = getattr(original, field.name)
        if field.name == "_ticket":
            value = type(value)()
        object.__setattr__(forged, field.name, value)
    return forged


# --- 1. default-off: unset / empty / false all fail closed ---------------------


def test_authorization_factory_is_default_off_when_flag_unset(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    with pytest.raises(EffectAdmissionError) as excinfo:
        _authorization(_candidate())
    assert excinfo.value.code == "effect_precursor_disabled"


@pytest.mark.parametrize("value", ["", "   ", "0", "false", "FALSE", "no", "off"])
def test_authorization_factory_is_default_off_for_falsey_flag(monkeypatch, value):
    monkeypatch.setenv(ENABLE_ENV, value)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    with pytest.raises(EffectAdmissionError) as excinfo:
        _authorization(_candidate())
    assert excinfo.value.code == "effect_precursor_disabled"


def test_mint_is_default_off_when_flag_is_cleared(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    candidate = _candidate()
    authorization = _authorization(candidate)
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate, authorization=authorization, ttl_seconds=5.0
        )
    assert excinfo.value.code == "effect_precursor_disabled"


def test_preflight_is_default_off_and_revokes_the_grant(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    candidate = _candidate()
    grant = _grant(candidate)

    monkeypatch.setenv(ENABLE_ENV, "false")
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_precursor_disabled"

    monkeypatch.setenv(ENABLE_ENV, "1")
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_preflight_grant_not_registered"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_explicit_enable_values_open_the_precursor(monkeypatch, value):
    monkeypatch.setenv(ENABLE_ENV, value)
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    candidate = _candidate()
    receipt = preflight_effect_admission_v1(candidate, _grant(candidate))
    assert receipt.decision == "eligible_pending_authority_and_sink"


def test_kill_switch_is_independent_of_the_feature_flag(monkeypatch, enabled):
    candidate = _candidate()
    grant = _grant(candidate)

    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    with pytest.raises(EffectAdmissionError) as excinfo:
        _authorization(_candidate())
    assert excinfo.value.code == "effect_precursor_kill_switch_engaged"
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_precursor_kill_switch_engaged"

    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_preflight_grant_not_registered"


def test_kill_switch_blocks_mint(monkeypatch, enabled):
    candidate = _candidate()
    authorization = _authorization(candidate)
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate, authorization=authorization, ttl_seconds=5.0
        )
    assert excinfo.value.code == "effect_precursor_kill_switch_engaged"


# --- 2. candidate contract: request, not self-declared authority ---------------


def test_candidate_declares_an_authority_request_only():
    candidate = _candidate()
    assert not hasattr(candidate, "authority_scope")
    assert set(candidate.authority_request) == {
        "request_id",
        "side_effect_tier",
        "requested_operations",
        "requested_target_refs",
    }
    assert candidate.schema_version == "effect-admission-candidate-experimental-v1"


def test_candidate_digest_is_canonical_and_deterministic():
    first = derive_effect_admission_candidate_digest_v1(_candidate())
    second = derive_effect_admission_candidate_digest_v1(_candidate())
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_candidate_digest_differs_from_the_withdrawn_self_authorizing_body():
    assert (
        derive_effect_admission_candidate_digest_v1(_candidate())
        != _WITHDRAWN_SELF_AUTHORIZING_DIGEST
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"operation": "delete_note"},
        {"payload_digest": _OTHER_PAYLOAD_DIGEST},
        {
            "target": {
                "target_kind": "local_private_file",
                "target_ref": "notes/other.json",
            }
        },
        {
            "authority_request": {
                "request_id": "request:hgwk-precursor-note",
                "side_effect_tier": "local_write",
                "requested_operations": ["write_note", "delete_note"],
                "requested_target_refs": ["notes/hgwk-precursor.json"],
            }
        },
    ],
)
def test_candidate_digest_changes_when_any_bound_field_mutates(overrides):
    assert derive_effect_admission_candidate_digest_v1(
        _candidate(**overrides)
    ) != derive_effect_admission_candidate_digest_v1(_candidate())


def test_candidate_digest_rejects_malformed_candidate():
    with pytest.raises(EffectAdmissionError) as excinfo:
        derive_effect_admission_candidate_digest_v1(
            _candidate(payload_digest="deadbeef")
        )
    assert excinfo.value.code == "effect_candidate_schema_invalid"


# --- 3. self-authorization is impossible --------------------------------------


def test_mint_requires_an_independent_harness_authorization(enabled):
    with pytest.raises(TypeError):
        mint_effect_preflight_grant_v1(candidate=_candidate(), ttl_seconds=5.0)


def test_mint_rejects_a_candidate_as_its_own_authorization(enabled):
    candidate = _candidate()
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate, authorization=candidate, ttl_seconds=5.0
        )
    assert excinfo.value.code == "effect_harness_authorization_invalid"


def test_execute_grant_cannot_authorize_a_preflight_grant(enabled):
    from workflow_kernel.governed_runtime import ExecuteGrant

    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=_candidate(),
            authorization=ExecuteGrant.local_only(),
            ttl_seconds=5.0,
        )
    assert excinfo.value.code == "effect_harness_authorization_invalid"


@pytest.mark.parametrize(
    "overrides",
    [
        {"operation": "delete_note"},
        {"adapter_id": "local.other"},
        {"payload_digest": _OTHER_PAYLOAD_DIGEST},
        {
            "target": {
                "target_kind": "local_private_file",
                "target_ref": "notes/other.json",
            }
        },
        {"side_effect_tier": "local_read"},
    ],
)
def test_authorization_must_match_the_request_exactly(enabled, overrides):
    candidate = _candidate()
    authorization = _authorization(candidate, **overrides)
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate, authorization=authorization, ttl_seconds=5.0
        )
    assert excinfo.value.code == "effect_harness_authorization_mismatch"


def test_broad_authority_request_is_rejected(enabled):
    broad = {
        "request_id": "request:full-local",
        "side_effect_tier": "local_write",
        "requested_operations": ["*"],
        "requested_target_refs": ["*"],
    }
    candidate = _candidate(authority_request=broad)
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate,
            authorization=_authorization(candidate),
            ttl_seconds=5.0,
        )
    assert excinfo.value.code == "effect_authority_request_broad"


def test_operation_outside_the_request_is_rejected(enabled):
    candidate = _candidate(operation="delete_note")
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate,
            authorization=_authorization(candidate),
            ttl_seconds=5.0,
        )
    assert excinfo.value.code == "effect_authority_operation_not_permitted"


def test_target_outside_the_request_is_rejected(enabled):
    candidate = _candidate(
        target={"target_kind": "local_private_file", "target_ref": "notes/other.json"}
    )
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate,
            authorization=_authorization(candidate),
            ttl_seconds=5.0,
        )
    assert excinfo.value.code == "effect_authority_target_not_permitted"


def test_external_side_effect_tiers_remain_closed(enabled):
    request = {
        "request_id": "request:hgwk-precursor-note",
        "side_effect_tier": "external_write",
        "requested_operations": ["write_note"],
        "requested_target_refs": ["notes/hgwk-precursor.json"],
    }
    candidate = _candidate(authority_request=request)
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate,
            authorization=_authorization(candidate),
            ttl_seconds=5.0,
        )
    assert excinfo.value.code == "effect_side_effect_tier_not_open"


# --- 4. forged authorizations and forged grants -------------------------------


def test_forged_harness_authorization_is_rejected(enabled):
    candidate = _candidate()
    forged = _forge(_authorization(candidate))
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate, authorization=forged, ttl_seconds=5.0
        )
    assert excinfo.value.code == "effect_harness_authorization_not_registered"


def test_harness_authorization_is_one_shot(enabled):
    candidate = _candidate()
    authorization = _authorization(candidate)
    mint_effect_preflight_grant_v1(
        candidate=candidate, authorization=authorization, ttl_seconds=5.0
    )
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate, authorization=authorization, ttl_seconds=5.0
        )
    assert excinfo.value.code == "effect_harness_authorization_reused"


def test_expired_harness_authorization_is_rejected(enabled, monkeypatch):
    candidate = _candidate()
    authorization = _authorization(candidate, ttl_seconds=0.001)
    monkeypatch.setattr(
        effect_admission,
        "time",
        types.SimpleNamespace(monotonic=lambda: authorization.expires_at_monotonic + 1),
    )
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=candidate, authorization=authorization, ttl_seconds=5.0
        )
    assert excinfo.value.code == "effect_harness_authorization_expired"


def test_harness_authorization_is_not_serializable(enabled):
    authorization = _authorization(_candidate())
    for attempt in (
        lambda: pickle.dumps(authorization),
        lambda: copy.deepcopy(authorization),
        lambda: copy.copy(authorization),
    ):
        with pytest.raises(EffectAdmissionError) as excinfo:
            attempt()
        assert excinfo.value.code == "effect_harness_authorization_not_serializable"
    assert authorization.nonce not in repr(authorization)


def test_forged_preflight_grant_is_rejected(enabled):
    candidate = _candidate()
    forged = _forge(_grant(candidate))
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, forged)
    assert excinfo.value.code == "effect_preflight_grant_not_registered"


def test_non_grant_object_is_rejected(enabled):
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(_candidate(), object())
    assert excinfo.value.code == "effect_preflight_grant_invalid"


def test_grant_is_not_serializable_and_leaks_no_nonce(enabled):
    grant = _grant(_candidate())
    for attempt in (
        lambda: pickle.dumps(grant),
        lambda: copy.deepcopy(grant),
        lambda: copy.copy(grant),
    ):
        with pytest.raises(EffectAdmissionError) as excinfo:
            attempt()
        assert excinfo.value.code == "effect_preflight_grant_not_serializable"
    assert grant.nonce not in repr(grant)


def test_grant_is_process_bound(enabled):
    candidate = _candidate()
    grant = _grant(candidate)
    object.__setattr__(grant._ticket, "creator_pid", 1)
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_preflight_grant_process_mismatch"


def test_grant_is_one_shot(enabled):
    candidate = _candidate()
    grant = _grant(candidate)
    preflight_effect_admission_v1(candidate, grant)
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_preflight_grant_reused"


def test_grant_is_one_shot_under_twenty_threads(enabled):
    candidate = _candidate()
    grant = _grant(candidate)
    barrier = threading.Barrier(20)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def _probe() -> None:
        barrier.wait()
        try:
            outcome = preflight_effect_admission_v1(candidate, grant).decision
        except EffectAdmissionError as exc:
            outcome = exc.code
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=_probe) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("eligible_pending_authority_and_sink") == 1
    assert set(outcomes) - {"eligible_pending_authority_and_sink"} <= {
        "effect_preflight_grant_reused"
    }
    assert len(outcomes) == 20


# --- 5. clock: the trusted module clock only ----------------------------------


def test_preflight_accepts_no_caller_supplied_clock(enabled):
    parameters = inspect.signature(preflight_effect_admission_v1).parameters
    assert set(parameters) == {"candidate", "grant"}
    assert "now_monotonic" not in parameters

    candidate = _candidate()
    grant = _grant(candidate)
    with pytest.raises(TypeError):
        preflight_effect_admission_v1(  # type: ignore[call-arg]
            candidate, grant, now_monotonic=0.0
        )
    assert grant._ticket.consumed is False
    assert (
        preflight_effect_admission_v1(candidate, grant).decision
        == "eligible_pending_authority_and_sink"
    )


@pytest.mark.parametrize("broken", [math.nan, math.inf, -math.inf])
def test_non_finite_trusted_clock_fails_closed_without_a_receipt(
    enabled, monkeypatch, broken
):
    candidate = _candidate()
    grant = _grant(candidate)
    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(effect_admission.time, "monotonic", lambda: broken)
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_preflight_clock_invalid"
    assert grant._ticket.consumed is False

    monkeypatch.setattr(effect_admission.time, "monotonic", real_monotonic)
    assert (
        preflight_effect_admission_v1(candidate, grant).decision
        == "eligible_pending_authority_and_sink"
    )


def test_expired_grant_is_rejected_and_revoked_under_the_trusted_clock(
    enabled, monkeypatch
):
    candidate = _candidate()
    grant = _grant(candidate, ttl_seconds=1.0)
    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(
        effect_admission.time, "monotonic", lambda: real_monotonic() + 1000.0
    )
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_preflight_grant_expired"
    # an expiry rejection revokes the grant but never spends its one shot
    assert grant._ticket.consumed is False

    monkeypatch.setattr(effect_admission.time, "monotonic", real_monotonic)
    with pytest.raises(EffectAdmissionError) as excinfo:
        preflight_effect_admission_v1(candidate, grant)
    assert excinfo.value.code == "effect_preflight_grant_not_registered"


def test_invalid_ttl_is_rejected(enabled):
    candidate = _candidate()
    for ttl in (0, -1.0, 61.0, math.nan, math.inf, "5", True):
        with pytest.raises(EffectAdmissionError) as excinfo:
            mint_effect_preflight_grant_v1(
                candidate=candidate,
                authorization=_authorization(candidate),
                ttl_seconds=ttl,
            )
        assert excinfo.value.code == "effect_grant_ttl_invalid"


# --- 6. mutation + rehash: no re-mint path ------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "target": {
                "target_kind": "local_private_file",
                "target_ref": "notes/attacker.json",
            }
        },
        {"operation": "delete_note"},
        {"payload_digest": _OTHER_PAYLOAD_DIGEST},
        {
            "authority_request": {
                "request_id": "request:hgwk-precursor-note",
                "side_effect_tier": "external_write",
                "requested_operations": ["write_note"],
                "requested_target_refs": ["notes/hgwk-precursor.json"],
            }
        },
    ],
)
def test_mutated_and_rehashed_candidate_is_rejected_with_zero_state(enabled, overrides):
    authorized = _candidate()
    authorization = _authorization(authorized)
    grant = mint_effect_preflight_grant_v1(
        candidate=authorized, authorization=authorization, ttl_seconds=5.0
    )
    mutated = _candidate(**overrides)
    assert (
        derive_effect_admission_candidate_digest_v1(mutated) != grant.candidate_digest
    )

    for _ in range(3):
        with pytest.raises(EffectAdmissionError) as excinfo:
            preflight_effect_admission_v1(mutated, grant)
        assert excinfo.value.code == "effect_candidate_binding_mismatch"

    # the mutated candidate cannot re-mint from the spent authorization
    with pytest.raises(EffectAdmissionError) as excinfo:
        mint_effect_preflight_grant_v1(
            candidate=mutated, authorization=authorization, ttl_seconds=5.0
        )
    assert excinfo.value.code in {
        "effect_harness_authorization_reused",
        "effect_harness_authorization_mismatch",
        "effect_authority_operation_not_permitted",
        "effect_authority_target_not_permitted",
        "effect_side_effect_tier_not_open",
    }

    # zero state: the grant was never consumed by the rejected probes
    assert (
        preflight_effect_admission_v1(authorized, grant).decision
        == "eligible_pending_authority_and_sink"
    )


def test_mutated_candidate_cannot_mint_without_a_fresh_authorization(enabled):
    with pytest.raises(TypeError):
        mint_effect_preflight_grant_v1(
            candidate=_candidate(operation="delete_note"), ttl_seconds=5.0
        )


# --- 7. eligibility result: never applied, never an admission -----------------


def test_preflight_returns_eligibility_pending_authority_and_sink(enabled):
    candidate = _candidate()
    grant = _grant(candidate)
    receipt = preflight_effect_admission_v1(candidate, grant)

    assert isinstance(receipt, ExperimentalEffectEligibilityReceiptV1)
    assert (
        receipt.schema_version == "effect-preflight-eligibility-receipt-experimental-v1"
    )
    assert receipt.decision == "eligible_pending_authority_and_sink"
    assert receipt.result_classification == "not_applied"
    assert receipt.applied is False
    assert receipt.candidate_digest == grant.candidate_digest
    assert receipt.idempotency_key == candidate.idempotency_key
    assert receipt.compensation_ref == candidate.compensation_ref
    assert receipt.reconcile_ref == candidate.reconcile_ref
    assert receipt.receipt_digest.startswith("sha256:")
    assert not hasattr(receipt, "effect_receipt_id")


def test_result_classification_projection_api_is_withdrawn():
    assert not hasattr(effect_admission, "project_effect_result_classification_v1")
    for banned in ("applied_classification", '"unknown"', "'unknown'", 'reconcile":'):
        assert banned not in MODULE_SOURCE


def test_no_admission_or_applied_claim_survives_in_source():
    # split literals so this guard does not match itself
    for banned in ("admit" + "ted_no_apply", "admit" + "ted", "admission " + "granted"):
        assert banned not in MODULE_SOURCE, f"module must not claim {banned!r}"
        assert banned not in TEST_SOURCE, f"tests must not claim {banned!r}"


def test_source_claims_no_implemented_f4_harness():
    for banned in (
        "F4 local-" + "harness implemented",
        "F4 local " + "harness",
        "local-" + "harness implemented",
        "Governed Side-" + "Effect Freeze",
    ):
        assert banned not in MODULE_SOURCE
        assert banned not in TEST_SOURCE
    assert "experimental" in MODULE_SOURCE.lower()
    assert "does not satisfy the SSOT section 6.4" in MODULE_SOURCE


# --- 8. zero I/O on both the success and the rejection paths ------------------


@pytest.fixture
def no_io(monkeypatch):
    import builtins

    from workflow_kernel import workflow_execution_ledger

    def _no_open(*args, **kwargs):
        raise AssertionError("precursor must not open files")

    def _no_socket(*args, **kwargs):
        raise AssertionError("precursor must not open sockets")

    def _no_ledger(*args, **kwargs):
        raise AssertionError("precursor must not write ledger events")

    monkeypatch.setattr(builtins, "open", _no_open)
    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(workflow_execution_ledger, "append_execution_event", _no_ledger)


def test_success_path_performs_no_file_socket_or_ledger_io(enabled, no_io):
    candidate = _candidate()
    receipt = preflight_effect_admission_v1(candidate, _grant(candidate))
    assert receipt.decision == "eligible_pending_authority_and_sink"


def test_rejection_paths_perform_no_file_socket_or_ledger_io(
    enabled, no_io, monkeypatch
):
    candidate = _candidate()
    grant = _grant(candidate)

    with pytest.raises(EffectAdmissionError):
        preflight_effect_admission_v1(_candidate(operation="delete_note"), grant)
    with pytest.raises(EffectAdmissionError):
        preflight_effect_admission_v1(candidate, _forge(grant))

    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(effect_admission.time, "monotonic", lambda: math.nan)
    with pytest.raises(EffectAdmissionError):
        preflight_effect_admission_v1(candidate, grant)
    monkeypatch.setattr(effect_admission.time, "monotonic", real_monotonic)

    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    with pytest.raises(EffectAdmissionError):
        preflight_effect_admission_v1(candidate, grant)
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    with pytest.raises(EffectAdmissionError):
        _authorization(candidate)


def test_module_source_holds_no_effect_or_ledger_sink():
    for banned in (
        "import subprocess",
        "import socket",
        "import requests",
        "import urllib",
        "import shutil",
        "open(",
        "Path(",
        "append_execution_event",
        "send_message",
        "os.system",
        "eval(",
        "exec(",
    ):
        assert banned not in MODULE_SOURCE, (
            f"precursor module must not reference {banned!r}"
        )


def test_public_api_accepts_no_executor_or_callback_injection():
    for func in (
        issue_experimental_harness_exact_authorization_v1,
        mint_effect_preflight_grant_v1,
        preflight_effect_admission_v1,
        derive_effect_admission_candidate_digest_v1,
    ):
        for name, parameter in inspect.signature(func).parameters.items():
            assert parameter.kind not in (
                parameter.VAR_POSITIONAL,
                parameter.VAR_KEYWORD,
            ), f"{func.__name__} accepts open-ended arguments via {name}"
            if parameter.default is not inspect.Parameter.empty:
                assert not callable(parameter.default), (
                    f"{func.__name__}.{name} defaults to a callable"
                )
            assert "Callable" not in str(parameter.annotation), (
                f"{func.__name__}.{name} accepts a callable"
            )


def test_precursor_is_not_wired_into_the_governed_runtime_or_package():
    runtime_source = Path("workflow_kernel/governed_runtime.py").read_text(
        encoding="utf-8"
    )
    package_source = Path("workflow_kernel/__init__.py").read_text(encoding="utf-8")
    assert "effect_admission" not in runtime_source
    assert "effect_admission" not in package_source


# --- 13. local harness bridge: non-consuming validate vs commit consume --------


_LOCAL_HARNESS_TARGET = {
    "target_kind": "local_private_json",
    "target_ref": "hgwk/local-harness/note",
}


def _local_harness_authorization(
    *, operation: str = "write_json", ttl_seconds: float = 5.0
) -> ExperimentalHarnessExactAuthorizationV1:
    return issue_experimental_harness_exact_authorization_v1(
        adapter_id="experimental.local_private_json_v1",
        operation=operation,
        target=dict(_LOCAL_HARNESS_TARGET),
        payload_digest=_PAYLOAD_DIGEST,
        side_effect_tier="local_write",
        ttl_seconds=ttl_seconds,
    )


def test_local_harness_validation_is_repeatable_and_never_consumes(enabled):
    authorization = _local_harness_authorization()
    first = effect_admission._validate_local_harness_authorization_v1(
        authorization,
        operation="write_json",
        target=dict(_LOCAL_HARNESS_TARGET),
        payload_digest=_PAYLOAD_DIGEST,
    )
    second = effect_admission._validate_local_harness_authorization_v1(
        authorization,
        operation="write_json",
        target=dict(_LOCAL_HARNESS_TARGET),
        payload_digest=_PAYLOAD_DIGEST,
    )
    assert first == second
    assert authorization._ticket.consumed is False

    committed = effect_admission._commit_local_harness_authorization_v1(
        authorization,
        operation="write_json",
        target=dict(_LOCAL_HARNESS_TARGET),
        payload_digest=_PAYLOAD_DIGEST,
    )
    assert committed == first
    assert authorization._ticket.consumed is True


def test_local_harness_commit_accepts_no_caller_supplied_clock(enabled):
    commit = effect_admission._commit_local_harness_authorization_v1
    assert set(inspect.signature(commit).parameters) == {
        "authorization",
        "operation",
        "target",
        "payload_digest",
    }
    authorization = _local_harness_authorization()
    with pytest.raises(TypeError):
        commit(  # type: ignore[call-arg]
            authorization,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
            now=0.0,
        )
    assert authorization._ticket.consumed is False


def test_local_harness_validation_fails_closed_without_consuming(enabled, monkeypatch):
    authorization = _local_harness_authorization()
    for overrides in (
        {"operation": "restore_json"},
        {"payload_digest": _OTHER_PAYLOAD_DIGEST},
        {
            "target": {
                "target_kind": "local_private_json",
                "target_ref": "hgwk/other",
            }
        },
    ):
        request = {
            "operation": "write_json",
            "target": dict(_LOCAL_HARNESS_TARGET),
            "payload_digest": _PAYLOAD_DIGEST,
        }
        request.update(overrides)
        with pytest.raises(EffectAdmissionError) as excinfo:
            effect_admission._validate_local_harness_authorization_v1(
                authorization, **request
            )
        assert excinfo.value.code == "effect_harness_authorization_mismatch"
        assert authorization._ticket.consumed is False

    with pytest.raises(EffectAdmissionError) as excinfo:
        effect_admission._validate_local_harness_authorization_v1(
            _forge(authorization),
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
    assert excinfo.value.code == "effect_harness_authorization_not_registered"

    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    with pytest.raises(EffectAdmissionError) as excinfo:
        effect_admission._validate_local_harness_authorization_v1(
            authorization,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
    assert excinfo.value.code == "effect_precursor_kill_switch_engaged"
    assert authorization._ticket.consumed is False


def test_local_harness_commit_still_checks_every_gate_freshly(enabled, monkeypatch):
    authorization = _local_harness_authorization(ttl_seconds=5.0)
    effect_admission._validate_local_harness_authorization_v1(
        authorization,
        operation="write_json",
        target=dict(_LOCAL_HARNESS_TARGET),
        payload_digest=_PAYLOAD_DIGEST,
    )
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    with pytest.raises(EffectAdmissionError) as excinfo:
        effect_admission._commit_local_harness_authorization_v1(
            authorization,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
    assert excinfo.value.code == "effect_precursor_kill_switch_engaged"

    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    expired = _local_harness_authorization(ttl_seconds=1.0)
    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(
        effect_admission.time, "monotonic", lambda: real_monotonic() + 1000.0
    )
    with pytest.raises(EffectAdmissionError) as excinfo:
        effect_admission._commit_local_harness_authorization_v1(
            expired,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
    assert excinfo.value.code == "effect_harness_authorization_expired"


def test_local_harness_validation_rejects_an_expired_authorization(
    enabled, monkeypatch
):
    """A stale capability must fail closed even on a state-only validation."""
    authorization = _local_harness_authorization(ttl_seconds=1.0)
    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(
        effect_admission.time, "monotonic", lambda: real_monotonic() + 1000.0
    )
    with pytest.raises(EffectAdmissionError) as excinfo:
        effect_admission._validate_local_harness_authorization_v1(
            authorization,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
    assert excinfo.value.code == "effect_harness_authorization_expired"
    assert authorization._ticket.consumed is False


def test_local_harness_validation_rejects_a_non_finite_clock(enabled, monkeypatch):
    authorization = _local_harness_authorization()
    monkeypatch.setattr(effect_admission.time, "monotonic", lambda: float("nan"))
    with pytest.raises(EffectAdmissionError) as excinfo:
        effect_admission._validate_local_harness_authorization_v1(
            authorization,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
    assert excinfo.value.code == "effect_harness_authorization_clock_invalid"
    assert authorization._ticket.consumed is False


def test_local_harness_validation_reads_ticket_state_under_the_ticket_lock(enabled):
    """Consumed, registry and expiry state may not be read without the lock."""
    assert callable(effect_admission._validate_ticket_without_consuming)
    authorization = _local_harness_authorization()
    finished = threading.Event()

    def _validate() -> None:
        effect_admission._validate_local_harness_authorization_v1(
            authorization,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
        finished.set()

    authorization._ticket.lock.acquire()
    worker = threading.Thread(target=_validate)
    worker.start()
    try:
        assert not finished.wait(timeout=0.3), (
            "validation read ticket state without holding the ticket lock"
        )
    finally:
        authorization._ticket.lock.release()
    worker.join(timeout=5.0)
    assert finished.is_set()
    assert authorization._ticket.consumed is False


def test_local_harness_commit_is_one_shot(enabled):
    authorization = _local_harness_authorization()
    effect_admission._commit_local_harness_authorization_v1(
        authorization,
        operation="write_json",
        target=dict(_LOCAL_HARNESS_TARGET),
        payload_digest=_PAYLOAD_DIGEST,
    )
    with pytest.raises(EffectAdmissionError) as excinfo:
        effect_admission._commit_local_harness_authorization_v1(
            authorization,
            operation="write_json",
            target=dict(_LOCAL_HARNESS_TARGET),
            payload_digest=_PAYLOAD_DIGEST,
        )
    assert excinfo.value.code in {
        "effect_harness_authorization_reused",
        "effect_harness_authorization_not_registered",
    }
