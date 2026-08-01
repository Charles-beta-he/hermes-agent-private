"""Experimental local-write effect harness: exact, default-off, private-root only.

These tests bind a standalone harness vertical slice. The harness applies one
kind of real side effect only: an exact JSON file inside a caller-supplied
private root, under the harness's own namespace. It is not wired into the
governed runtime, it issues no production authority, and its receipt store is
not the Workflow Execution Ledger.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import inspect
import json
import os
import re
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from workflow_kernel import effect_admission, local_effect_harness, runtime_state
from workflow_kernel.effect_admission import (
    EffectAdmissionError,
    ExperimentalHarnessExactAuthorizationV1,
)
from workflow_kernel.local_effect_harness import (
    HARNESS_ADAPTER_ID,
    HARNESS_APPLY_OPERATION,
    HARNESS_COMPENSATION_OPERATION,
    HARNESS_SIDE_EFFECT_TIER,
    ExperimentalLocalEffectReceiptV1,
    LocalEffectHarnessError,
    LocalEffectHarnessUnknownError,
    apply_local_json_effect_v1,
    backup_path_v1,
    build_local_json_effect_candidate_v1,
    canonical_payload_digest_v1,
    compensate_local_json_effect_v1,
    compensation_intent_path_v1,
    global_transaction_lock_path_v1,
    intent_path_v1,
    issue_local_json_effect_authorization_v1,
    issue_local_json_effect_compensation_authorization_v1,
    read_local_json_effect_receipt_v1,
    receipt_path_v1,
    reconcile_local_json_compensation_v1,
    reconcile_local_json_effect_v1,
    target_path_v1,
)

HARNESS_ENV = "HGWK_LOCAL_EFFECT_HARNESS_ENABLED"
HARNESS_KILL_SWITCH_ENV = "HGWK_LOCAL_EFFECT_HARNESS_KILL_SWITCH"
PRECURSOR_ENV = "HGWK_EFFECT_PRECURSOR_ENABLED"
PRECURSOR_KILL_SWITCH_ENV = "HGWK_EFFECT_PRECURSOR_KILL_SWITCH"

MODULE_PATH = Path("workflow_kernel/local_effect_harness.py")
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")

TARGET_REF = "hgwk/local-harness/note"
PAYLOAD = {"note": "hgwk", "revision": 1}
OTHER_PAYLOAD = {"note": "hgwk", "revision": 2}
KEY = "hgwk-local-effect-0001"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(HARNESS_ENV, "1")
    monkeypatch.setenv(PRECURSOR_ENV, "1")
    monkeypatch.delenv(HARNESS_KILL_SWITCH_ENV, raising=False)
    monkeypatch.delenv(PRECURSOR_KILL_SWITCH_ENV, raising=False)


@pytest.fixture
def private_root(tmp_path) -> Path:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    return root


def _candidate(
    *, target_ref: str = TARGET_REF, payload=PAYLOAD, idempotency_key: str = KEY
):
    return build_local_json_effect_candidate_v1(
        target_ref=target_ref,
        payload=payload,
        idempotency_key=idempotency_key,
    )


def _authorization(candidate, ttl_seconds: float = 30.0):
    return issue_local_json_effect_authorization_v1(
        candidate=candidate, ttl_seconds=ttl_seconds
    )


def _apply(private_root: Path, *, candidate=None, payload=PAYLOAD, **kwargs):
    candidate = candidate if candidate is not None else _candidate(payload=payload)
    return apply_local_json_effect_v1(
        candidate=candidate,
        authorization=_authorization(candidate),
        payload=payload,
        private_root=private_root,
        **kwargs,
    )


def _snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            snapshot[str(path.relative_to(root))] = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            info = path.stat()
            snapshot[str(path.relative_to(root))] = f"{digest}:{info.st_ino}"
        else:
            snapshot[str(path.relative_to(root))] = "dir"
    return snapshot


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


# --- default-off and kill switch ----------------------------------------------


def test_apply_is_default_off_when_flag_unset(monkeypatch, private_root):
    monkeypatch.delenv(HARNESS_ENV, raising=False)
    monkeypatch.setenv(PRECURSOR_ENV, "1")
    before = _snapshot(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(private_root)
    assert error.value.code == "local_effect_harness_disabled"
    assert _snapshot(private_root) == before


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", " ", "maybe"])
def test_apply_is_default_off_for_falsey_flag(monkeypatch, private_root, value):
    monkeypatch.setenv(HARNESS_ENV, value)
    monkeypatch.setenv(PRECURSOR_ENV, "1")
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(private_root)
    assert error.value.code == "local_effect_harness_disabled"
    assert _snapshot(private_root) == {}


def test_precursor_flag_does_not_open_the_harness(monkeypatch, private_root):
    monkeypatch.setenv(PRECURSOR_ENV, "1")
    monkeypatch.delenv(HARNESS_ENV, raising=False)
    with pytest.raises(LocalEffectHarnessError) as error:
        issue_local_json_effect_authorization_v1(
            candidate=_candidate(), ttl_seconds=30.0
        )
    assert error.value.code == "local_effect_harness_disabled"


def test_kill_switch_closes_every_stage(enabled, monkeypatch, private_root):
    result = _apply(private_root)
    before = _snapshot(private_root)
    monkeypatch.setenv(HARNESS_KILL_SWITCH_ENV, "1")
    for call in (
        lambda: _apply(private_root),
        lambda: read_local_json_effect_receipt_v1(
            idempotency_key=KEY, private_root=private_root
        ),
        lambda: reconcile_local_json_effect_v1(
            idempotency_key=KEY, private_root=private_root
        ),
        lambda: issue_local_json_effect_authorization_v1(
            candidate=_candidate(), ttl_seconds=30.0
        ),
        lambda: issue_local_json_effect_compensation_authorization_v1(
            receipt=result.receipt, ttl_seconds=30.0
        ),
    ):
        with pytest.raises(LocalEffectHarnessError) as error:
            call()
        assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _snapshot(private_root) == before


def test_kill_switch_after_intent_blocks_reconcile_without_mutation(
    enabled, monkeypatch, private_root
):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_intent_before_apply")
    before = _snapshot(private_root)
    monkeypatch.setenv(HARNESS_KILL_SWITCH_ENV, "1")
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _snapshot(private_root) == before


def test_precursor_gates_still_close_the_harness(enabled, monkeypatch, private_root):
    candidate = _candidate()
    authorization = _authorization(candidate)
    for variable, value in (
        (PRECURSOR_ENV, "0"),
        (PRECURSOR_KILL_SWITCH_ENV, "1"),
    ):
        with monkeypatch.context() as context:
            context.setenv(variable, value)
            with pytest.raises(EffectAdmissionError) as error:
                apply_local_json_effect_v1(
                    candidate=candidate,
                    authorization=authorization,
                    payload=PAYLOAD,
                    private_root=private_root,
                )
            assert error.value.code in {
                "effect_precursor_disabled",
                "effect_precursor_kill_switch_engaged",
            }
            assert _snapshot(private_root) == {}


def test_receipt_symlink_is_refused(enabled, private_root):
    receipt = receipt_path_v1(private_root=private_root, idempotency_key=KEY)
    receipt.parent.mkdir(parents=True, mode=0o700)
    (private_root / "outside.json").write_text("{}", encoding="utf-8")
    receipt.symlink_to(private_root / "outside.json")
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(private_root)
    assert error.value.code == "local_effect_private_file_unsafe"
    assert receipt.is_symlink()
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()


# --- fixed adapter, operation and tier ----------------------------------------


def test_fixed_adapter_operation_and_tier(enabled):
    candidate = _candidate()
    assert (
        candidate.adapter_id
        == HARNESS_ADAPTER_ID
        == "experimental.local_private_json_v1"
    )
    assert candidate.operation == HARNESS_APPLY_OPERATION == "write_json"
    assert HARNESS_COMPENSATION_OPERATION == "restore_json"
    assert candidate.authority_request["side_effect_tier"] == HARNESS_SIDE_EFFECT_TIER
    assert HARNESS_SIDE_EFFECT_TIER == "local_write"


@pytest.mark.parametrize(
    "overrides",
    [
        {"adapter_id": "local.other_adapter"},
        {"operation": "delete_json"},
        {
            "authority_request": {
                "request_id": "request:hgwk-local-effect",
                "side_effect_tier": "external_write",
                "requested_operations": ["write_json"],
                "requested_target_refs": [TARGET_REF],
            }
        },
        {
            "authority_request": {
                "request_id": "request:hgwk-local-effect",
                "side_effect_tier": "local_read",
                "requested_operations": ["write_json"],
                "requested_target_refs": [TARGET_REF],
            }
        },
        {"target": {"target_kind": "external_endpoint", "target_ref": TARGET_REF}},
    ],
)
def test_candidate_outside_the_fixed_harness_contract_is_rejected(
    enabled, private_root, overrides
):
    candidate = dataclasses.replace(_candidate(), **overrides)
    with pytest.raises(LocalEffectHarnessError) as error:
        issue_local_json_effect_authorization_v1(candidate=candidate, ttl_seconds=30.0)
    assert error.value.code in {
        "local_effect_adapter_not_supported",
        "local_effect_operation_not_supported",
        "local_effect_side_effect_tier_not_supported",
        "local_effect_target_kind_not_supported",
    }
    assert _snapshot(private_root) == {}


def test_external_tier_authorization_cannot_reach_the_harness(enabled, private_root):
    candidate = _candidate()
    external = effect_admission.issue_experimental_harness_exact_authorization_v1(
        adapter_id=HARNESS_ADAPTER_ID,
        operation=HARNESS_APPLY_OPERATION,
        target=dict(candidate.target),
        payload_digest=candidate.payload_digest,
        side_effect_tier="external_write",
        ttl_seconds=30.0,
    )
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=external,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert "tier" in error.value.code or "mismatch" in error.value.code
    assert _snapshot(private_root) == {}


# --- private root validation ---------------------------------------------------


def test_private_root_must_exist(enabled, tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(missing)
    assert error.value.code == "local_effect_private_root_invalid"
    assert not missing.exists()


def test_private_root_symlink_is_rejected(enabled, tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(link)
    assert error.value.code == "local_effect_private_root_invalid"
    assert _snapshot(real) == {}


def test_private_root_must_be_owner_only(enabled, tmp_path):
    root = tmp_path / "loose"
    root.mkdir(mode=0o755)
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(root)
    assert error.value.code == "local_effect_private_root_invalid"
    assert _snapshot(root) == {}


def test_private_root_must_be_a_directory(enabled, tmp_path):
    root = tmp_path / "file"
    root.write_text("{}", encoding="utf-8")
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(root)
    assert error.value.code == "local_effect_private_root_invalid"


# --- logical target refs never become paths -----------------------------------


@pytest.mark.parametrize(
    "target_ref",
    ["../../escape", "/etc/passwd", "hgwk/local-harness/note", "..", "a\nb"],
)
def test_target_ref_is_a_logical_id_hashed_onto_disk(enabled, private_root, target_ref):
    result = _apply(private_root, candidate=_candidate(target_ref=target_ref))
    expected = (
        private_root
        / ".hgwk-local-effect-harness"
        / "targets"
        / (hashlib.sha256(target_ref.encode("utf-8")).hexdigest() + ".json")
    )
    assert target_path_v1(private_root=private_root, target_ref=target_ref) == expected
    assert expected.is_file()
    assert expected.read_bytes() == _canonical(PAYLOAD)
    assert result.receipt.target_ref == target_ref
    for path in private_root.rglob("*"):
        assert private_root in path.parents or path.parent == private_root
        assert not path.is_symlink()


def test_existing_target_symlink_is_refused(enabled, private_root):
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.parent.mkdir(parents=True, mode=0o700)
    (private_root / "outside.json").write_text("{}", encoding="utf-8")
    target.symlink_to(private_root / "outside.json")
    with pytest.raises((LocalEffectHarnessError, OSError)):
        _apply(private_root)
    assert target.is_symlink()
    assert (private_root / "outside.json").read_text(encoding="utf-8") == "{}"


# --- payload digest ------------------------------------------------------------


def test_payload_digest_must_match_the_candidate_exactly(enabled, private_root):
    candidate = _candidate(payload=PAYLOAD)
    authorization = _authorization(candidate)
    with pytest.raises(LocalEffectHarnessError) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=OTHER_PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_payload_digest_mismatch"
    assert _snapshot(private_root) == {}


@pytest.mark.parametrize("payload", [float("nan"), {"a": float("inf")}, object()])
def test_non_canonical_payload_is_rejected(enabled, private_root, payload):
    with pytest.raises(LocalEffectHarnessError) as error:
        canonical_payload_digest_v1(payload)
    assert error.value.code == "local_effect_payload_not_canonical"
    assert _snapshot(private_root) == {}


# --- authorization is real, exact and one-shot ---------------------------------


def test_apply_requires_a_registry_issued_authorization(enabled, private_root):
    candidate = _candidate()
    forged = ExperimentalHarnessExactAuthorizationV1(
        schema_version="harness-exact-authorization-experimental-v1",
        nonce="f" * 32,
        adapter_id=HARNESS_ADAPTER_ID,
        operation=HARNESS_APPLY_OPERATION,
        target_digest=_authorization(candidate).target_digest,
        payload_digest=candidate.payload_digest,
        side_effect_tier=HARNESS_SIDE_EFFECT_TIER,
        expires_at_monotonic=float("inf"),
        _ticket=effect_admission._OneShotTicket(),
    )
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=forged,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code in {
        "effect_harness_authorization_not_registered",
        "effect_harness_authorization_expired",
        "local_effect_authorization_invalid",
    }
    assert _snapshot(private_root) == {}


def test_authorization_is_one_shot(enabled, private_root):
    candidate = _candidate()
    authorization = _authorization(candidate)
    apply_local_json_effect_v1(
        candidate=candidate,
        authorization=authorization,
        payload=PAYLOAD,
        private_root=private_root,
    )
    before = _snapshot(private_root)
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_reused"
    assert _snapshot(private_root) == before


def test_candidate_cannot_mint_its_own_authorization(enabled, private_root):
    candidate = _candidate()
    with pytest.raises(TypeError):
        apply_local_json_effect_v1(  # type: ignore[call-arg]
            candidate=candidate, payload=PAYLOAD, private_root=private_root
        )
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)):
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=candidate,  # type: ignore[arg-type]
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert _snapshot(private_root) == {}


def test_mutated_and_rehashed_candidate_cannot_reuse_an_old_authorization(
    enabled, private_root
):
    candidate = _candidate()
    authorization = _authorization(candidate)
    mutated = build_local_json_effect_candidate_v1(
        target_ref="hgwk/local-harness/other",
        payload=OTHER_PAYLOAD,
        idempotency_key=KEY,
    )
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        apply_local_json_effect_v1(
            candidate=mutated,
            authorization=authorization,
            payload=OTHER_PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code in {
        "effect_harness_authorization_mismatch",
        "local_effect_authorization_mismatch",
    }
    assert _snapshot(private_root) == {}


def test_public_effect_api_accepts_no_caller_supplied_clock(enabled, private_root):
    for function in (apply_local_json_effect_v1, compensate_local_json_effect_v1):
        assert "now_monotonic" not in inspect.signature(function).parameters
    commit = effect_admission._commit_local_harness_authorization_v1
    assert set(inspect.signature(commit).parameters) == {
        "authorization",
        "operation",
        "target",
        "payload_digest",
    }

    candidate = _candidate()
    with pytest.raises(TypeError):
        apply_local_json_effect_v1(  # type: ignore[call-arg]
            candidate=candidate,
            authorization=_authorization(candidate),
            payload=PAYLOAD,
            private_root=private_root,
            now_monotonic=0.0,
        )
    assert _snapshot(private_root) == {}


def test_expired_authorization_is_rejected_under_the_trusted_clock(
    enabled, private_root, monkeypatch
):
    candidate = _candidate()
    authorization = _authorization(candidate, ttl_seconds=1.0)
    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(
        effect_admission.time, "monotonic", lambda: real_monotonic() + 1000.0
    )
    # A caller cannot rewind the expiry check by supplying its own clock.
    with pytest.raises((TypeError, EffectAdmissionError, LocalEffectHarnessError)):
        apply_local_json_effect_v1(  # type: ignore[call-arg]
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
            now_monotonic=authorization.expires_at_monotonic - 0.5,
        )
    with pytest.raises(EffectAdmissionError) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_expired"
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()
    assert not intent_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert not receipt_path_v1(private_root=private_root, idempotency_key=KEY).exists()


def test_expired_compensation_authorization_is_rejected_under_the_trusted_clock(
    enabled, private_root, monkeypatch
):
    applied = _apply(private_root)
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=applied.receipt, ttl_seconds=1.0
    )
    before = _snapshot(private_root)
    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(
        effect_admission.time, "monotonic", lambda: real_monotonic() + 1000.0
    )
    with pytest.raises((TypeError, EffectAdmissionError, LocalEffectHarnessError)):
        compensate_local_json_effect_v1(  # type: ignore[call-arg]
            receipt=applied.receipt,
            authorization=authorization,
            private_root=private_root,
            now_monotonic=authorization.expires_at_monotonic - 0.5,
        )
    with pytest.raises(EffectAdmissionError) as error:
        compensate_local_json_effect_v1(
            receipt=applied.receipt,
            authorization=authorization,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_expired"
    assert _snapshot(private_root) == before


# --- durable happy path --------------------------------------------------------


def test_apply_writes_backup_intent_target_and_receipt(enabled, private_root):
    candidate = _candidate()
    result = _apply(private_root, candidate=candidate)
    receipt = result.receipt

    assert result.replayed is False
    assert isinstance(receipt, ExperimentalLocalEffectReceiptV1)
    assert receipt.adapter_id == HARNESS_ADAPTER_ID
    assert receipt.operation == HARNESS_APPLY_OPERATION
    assert receipt.target_ref == TARGET_REF
    assert receipt.payload_digest == canonical_payload_digest_v1(PAYLOAD)
    assert receipt.idempotency_key == KEY
    assert receipt.result_classification == "applied"
    assert receipt.applied is True
    assert receipt.next_action == "none"
    assert receipt.pre_readback == {"present": False, "content_digest": None}
    assert receipt.post_readback == {
        "present": True,
        "content_digest": "sha256:" + hashlib.sha256(_canonical(PAYLOAD)).hexdigest(),
    }
    assert receipt.authorization_binding_digest.startswith("sha256:")
    assert receipt.candidate_digest == (
        effect_admission.derive_effect_admission_candidate_digest_v1(candidate)
    )
    assert receipt.backup_ref.startswith("sha256:")
    assert receipt.receipt_digest.startswith("sha256:")

    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    assert target.read_bytes() == _canonical(PAYLOAD)
    for path in (
        target,
        intent_path_v1(private_root=private_root, idempotency_key=KEY),
        receipt_path_v1(private_root=private_root, idempotency_key=KEY),
        backup_path_v1(private_root=private_root, backup_ref=receipt.backup_ref),
    ):
        assert path.is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    persisted = json.loads(
        receipt_path_v1(private_root=private_root, idempotency_key=KEY).read_text(
            encoding="utf-8"
        )
    )
    assert persisted["receipt_digest"] == receipt.receipt_digest
    body = {k: v for k, v in persisted.items() if k != "receipt_digest"}
    assert (
        "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
        == receipt.receipt_digest
    )


def test_apply_over_an_existing_target_backs_up_the_preimage(enabled, private_root):
    first = _apply(private_root)
    second = _apply(
        private_root,
        candidate=_candidate(payload=OTHER_PAYLOAD, idempotency_key="hgwk-second"),
        payload=OTHER_PAYLOAD,
    )
    assert second.receipt.pre_readback == first.receipt.post_readback
    backup = json.loads(
        backup_path_v1(
            private_root=private_root, backup_ref=second.receipt.backup_ref
        ).read_text(encoding="utf-8")
    )
    assert base64.b64decode(backup["content_b64"]) == _canonical(PAYLOAD)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    assert target.read_bytes() == _canonical(OTHER_PAYLOAD)


# --- idempotency ---------------------------------------------------------------


def test_second_apply_replays_without_rewriting_the_target(
    enabled, private_root, monkeypatch
):
    first = _apply(private_root)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    before = target.stat()

    writes: list[str] = []
    original = local_effect_harness._write_atomic

    def _counting(path, data, *, private_root):
        writes.append(str(path))
        return original(path, data, private_root=private_root)

    monkeypatch.setattr(local_effect_harness, "_write_atomic", _counting)
    second = _apply(private_root)

    assert second.replayed is True
    assert second.receipt.receipt_digest == first.receipt.receipt_digest
    assert str(target) not in writes
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_same_key_with_a_different_request_is_rejected(enabled, private_root):
    _apply(private_root)
    before = _snapshot(private_root)
    conflicting = _candidate(payload=OTHER_PAYLOAD, idempotency_key=KEY)
    with pytest.raises(LocalEffectHarnessError) as error:
        apply_local_json_effect_v1(
            candidate=conflicting,
            authorization=_authorization(conflicting),
            payload=OTHER_PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_idempotency_binding_conflict"
    assert _snapshot(private_root) == before


def test_read_only_replay_api_needs_no_authorization(enabled, private_root):
    first = _apply(private_root)
    replayed = read_local_json_effect_receipt_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert replayed == first.receipt
    assert inspect.signature(read_local_json_effect_receipt_v1).parameters.keys() == {
        "idempotency_key",
        "private_root",
    }


def test_read_only_replay_api_rejects_an_unknown_key(enabled, private_root):
    with pytest.raises(LocalEffectHarnessError) as error:
        read_local_json_effect_receipt_v1(
            idempotency_key="absent", private_root=private_root
        )
    assert error.value.code == "local_effect_receipt_missing"


# --- crash injection -----------------------------------------------------------


def test_crash_phase_must_be_a_known_enum(enabled, private_root):
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(private_root, crash_phase="whenever")
    assert error.value.code == "local_effect_crash_phase_invalid"
    with pytest.raises(LocalEffectHarnessError):
        _apply(private_root, crash_phase=lambda: None)
    assert _snapshot(private_root) == {}


def test_crash_after_intent_before_apply_leaves_a_recoverable_journal(
    enabled, private_root
):
    with pytest.raises(LocalEffectHarnessUnknownError) as error:
        _apply(private_root, crash_phase="after_intent_before_apply")
    assert error.value.code == "effect_result_unknown"
    assert error.value.idempotency_key == KEY
    assert error.value.next_action == "manual_reconcile"

    assert intent_path_v1(private_root=private_root, idempotency_key=KEY).is_file()
    assert not receipt_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()


def test_crash_after_apply_before_receipt_leaves_a_recoverable_journal(
    enabled, private_root
):
    with pytest.raises(LocalEffectHarnessUnknownError) as error:
        _apply(private_root, crash_phase="after_apply_before_receipt")
    assert error.value.code == "effect_result_unknown"

    assert intent_path_v1(private_root=private_root, idempotency_key=KEY).is_file()
    assert not receipt_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical(PAYLOAD)


def test_unknown_result_is_never_blindly_retried(enabled, private_root):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_apply_before_receipt")
    before = _snapshot(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(private_root)
    assert error.value.code == "local_effect_unresolved_intent"
    assert _snapshot(private_root) == before


# --- reconcile -----------------------------------------------------------------


def test_reconcile_classifies_a_not_applied_intent(enabled, private_root):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_intent_before_apply")
    receipt = reconcile_local_json_effect_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert receipt.result_classification == "not_applied"
    assert receipt.applied is False
    assert receipt.next_action == "none"
    assert receipt_path_v1(private_root=private_root, idempotency_key=KEY).is_file()
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()
    assert (
        read_local_json_effect_receipt_v1(
            idempotency_key=KEY, private_root=private_root
        )
        == receipt
    )


def test_reconcile_classifies_an_applied_intent(enabled, private_root):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_apply_before_receipt")
    receipt = reconcile_local_json_effect_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert receipt.result_classification == "applied"
    assert receipt.applied is True
    assert receipt.next_action == "none"
    assert receipt.post_readback["content_digest"] == (
        "sha256:" + hashlib.sha256(_canonical(PAYLOAD)).hexdigest()
    )
    assert receipt.resolution == "reconcile"
    assert receipt_path_v1(private_root=private_root, idempotency_key=KEY).is_file()


def test_receipt_records_how_it_was_resolved(enabled, private_root):
    result = _apply(private_root)
    assert result.receipt.resolution == "apply"
    compensation = compensate_local_json_effect_v1(
        receipt=result.receipt,
        authorization=issue_local_json_effect_compensation_authorization_v1(
            receipt=result.receipt, ttl_seconds=30.0
        ),
        private_root=private_root,
    )
    assert compensation.resolution == "compensation"


def test_second_compensation_replays_the_existing_compensation_receipt(
    enabled, private_root
):
    result = _apply(private_root)
    first = compensate_local_json_effect_v1(
        receipt=result.receipt,
        authorization=issue_local_json_effect_compensation_authorization_v1(
            receipt=result.receipt, ttl_seconds=30.0
        ),
        private_root=private_root,
    )
    before = _snapshot(private_root)
    second = compensate_local_json_effect_v1(
        receipt=result.receipt,
        authorization=issue_local_json_effect_compensation_authorization_v1(
            receipt=result.receipt, ttl_seconds=30.0
        ),
        private_root=private_root,
    )
    assert second == first
    assert _snapshot(private_root) == before


def test_reconcile_reports_unknown_and_never_auto_applies(enabled, private_root):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_intent_before_apply")
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.write_bytes(_canonical({"drifted": True}))
    receipt = reconcile_local_json_effect_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert receipt.result_classification == "unknown"
    assert receipt.applied is False
    assert receipt.next_action == "manual_reconcile"
    assert not receipt_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert target.read_bytes() == _canonical({"drifted": True})


def test_reconcile_requires_an_intent(enabled, private_root):
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_effect_v1(
            idempotency_key="absent", private_root=private_root
        )
    assert error.value.code == "local_effect_intent_missing"
    assert _snapshot(private_root) == {}


def test_reconcile_returns_an_existing_terminal_receipt(enabled, private_root):
    first = _apply(private_root)
    before = _snapshot(private_root)
    assert (
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
        == first.receipt
    )
    assert _snapshot(private_root) == before


def test_reconcile_accepts_no_executor_or_callback(enabled):
    assert set(inspect.signature(reconcile_local_json_effect_v1).parameters) == {
        "idempotency_key",
        "private_root",
    }


# --- tamper detection ----------------------------------------------------------


def test_tampered_intent_is_rejected(enabled, private_root):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_apply_before_receipt")
    intent_path = intent_path_v1(private_root=private_root, idempotency_key=KEY)
    document = json.loads(intent_path.read_text(encoding="utf-8"))
    document["desired_content_digest"] = "sha256:" + "00" * 32
    intent_path.write_text(json.dumps(document), encoding="utf-8")
    before = _snapshot(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    assert error.value.code == "local_effect_journal_tampered"
    assert _snapshot(private_root) == before


def test_tampered_receipt_is_rejected(enabled, private_root):
    _apply(private_root)
    receipt_path = receipt_path_v1(private_root=private_root, idempotency_key=KEY)
    document = json.loads(receipt_path.read_text(encoding="utf-8"))
    document["result_classification"] = "not_applied"
    receipt_path.write_text(json.dumps(document), encoding="utf-8")
    for call in (
        lambda: read_local_json_effect_receipt_v1(
            idempotency_key=KEY, private_root=private_root
        ),
        lambda: _apply(private_root),
    ):
        with pytest.raises(LocalEffectHarnessError) as error:
            call()
        assert error.value.code == "local_effect_receipt_tampered"


def test_tampered_backup_blocks_compensation(enabled, private_root):
    result = _apply(private_root)
    backup_path = backup_path_v1(
        private_root=private_root, backup_ref=result.receipt.backup_ref
    )
    document = json.loads(backup_path.read_text(encoding="utf-8"))
    document["present"] = True
    document["content_b64"] = base64.b64encode(b"{}").decode("ascii")
    backup_path.write_text(json.dumps(document), encoding="utf-8")
    before = _snapshot(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=issue_local_json_effect_compensation_authorization_v1(
                receipt=result.receipt, ttl_seconds=30.0
            ),
            private_root=private_root,
        )
    assert error.value.code == "local_effect_backup_tampered"
    assert _snapshot(private_root) == before


def test_tampered_target_blocks_compensation(enabled, private_root):
    result = _apply(private_root)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.write_bytes(_canonical({"drifted": True}))
    before = _snapshot(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=issue_local_json_effect_compensation_authorization_v1(
                receipt=result.receipt, ttl_seconds=30.0
            ),
            private_root=private_root,
        )
    assert error.value.code == "local_effect_target_drifted"
    assert _snapshot(private_root) == before


# --- rollback / compensation ---------------------------------------------------


def test_compensation_deletes_a_target_that_was_absent(enabled, private_root):
    result = _apply(private_root)
    compensation = compensate_local_json_effect_v1(
        receipt=result.receipt,
        authorization=issue_local_json_effect_compensation_authorization_v1(
            receipt=result.receipt, ttl_seconds=30.0
        ),
        private_root=private_root,
    )
    assert compensation.operation == HARNESS_COMPENSATION_OPERATION
    assert compensation.result_classification == "applied"
    assert compensation.compensated_receipt_digest == result.receipt.receipt_digest
    assert compensation.post_readback == {"present": False, "content_digest": None}
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()
    assert receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).is_file()


def test_compensation_restores_an_existing_preimage(enabled, private_root):
    _apply(private_root)
    second = _apply(
        private_root,
        candidate=_candidate(payload=OTHER_PAYLOAD, idempotency_key="hgwk-second"),
        payload=OTHER_PAYLOAD,
    )
    compensation = compensate_local_json_effect_v1(
        receipt=second.receipt,
        authorization=issue_local_json_effect_compensation_authorization_v1(
            receipt=second.receipt, ttl_seconds=30.0
        ),
        private_root=private_root,
    )
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    assert target.read_bytes() == _canonical(PAYLOAD)
    assert compensation.post_readback["content_digest"] == (
        "sha256:" + hashlib.sha256(_canonical(PAYLOAD)).hexdigest()
    )


def test_compensation_requires_a_fresh_exact_authorization(enabled, private_root):
    result = _apply(private_root)
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=30.0
    )
    compensate_local_json_effect_v1(
        receipt=result.receipt,
        authorization=authorization,
        private_root=private_root,
    )
    before = _snapshot(private_root)
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=authorization,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_reused"
    assert _snapshot(private_root) == before


def test_compensation_rejects_an_apply_authorization(enabled, private_root):
    result = _apply(private_root)
    candidate = _candidate()
    wrong = _authorization(candidate)
    before = _snapshot(private_root)
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt, authorization=wrong, private_root=private_root
        )
    assert error.value.code in {
        "local_effect_authorization_mismatch",
        "effect_harness_authorization_mismatch",
    }
    assert _snapshot(private_root) == before


def test_compensation_rejects_a_forged_receipt(enabled, private_root):
    result = _apply(private_root)
    forged = dataclasses.replace(result.receipt, target_ref="hgwk/other")
    before = _snapshot(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=forged,
            authorization=issue_local_json_effect_compensation_authorization_v1(
                receipt=result.receipt, ttl_seconds=30.0
            ),
            private_root=private_root,
        )
    assert error.value.code in {
        "local_effect_receipt_not_persisted",
        "local_effect_authorization_mismatch",
    }
    assert _snapshot(private_root) == before


def test_compensation_refuses_an_unknown_result(enabled, private_root):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_apply_before_receipt")
    intent = json.loads(
        intent_path_v1(private_root=private_root, idempotency_key=KEY).read_text(
            encoding="utf-8"
        )
    )
    unknown = ExperimentalLocalEffectReceiptV1(
        schema_version=intent["schema_version"].replace("intent", "receipt"),
        adapter_id=HARNESS_ADAPTER_ID,
        operation=HARNESS_APPLY_OPERATION,
        target_ref=TARGET_REF,
        target_digest=intent["target_digest"],
        payload_digest=intent["payload_digest"],
        authorization_binding_digest=intent["authorization_binding_digest"],
        idempotency_key=KEY,
        request_binding_digest=intent["request_binding_digest"],
        candidate_digest=intent["candidate_digest"],
        pre_readback=intent["pre_readback"],
        post_readback={"present": True, "content_digest": None},
        backup_ref=intent["backup_ref"],
        result_classification="unknown",
        applied=False,
        next_action="manual_reconcile",
        resolution="reconcile",
        compensated_receipt_digest=None,
        receipt_digest="sha256:" + "ab" * 32,
    )
    before = _snapshot(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=unknown,
            authorization=effect_admission.issue_experimental_harness_exact_authorization_v1(
                adapter_id=HARNESS_ADAPTER_ID,
                operation=HARNESS_COMPENSATION_OPERATION,
                target={
                    "target_kind": "local_private_json",
                    "target_ref": TARGET_REF,
                },
                payload_digest=unknown.receipt_digest,
                side_effect_tier=HARNESS_SIDE_EFFECT_TIER,
                ttl_seconds=30.0,
            ),
            private_root=private_root,
        )
    assert error.value.code == "local_effect_receipt_not_persisted"
    assert _snapshot(private_root) == before


def test_compensation_is_closed_by_the_kill_switch(enabled, monkeypatch, private_root):
    result = _apply(private_root)
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=30.0
    )
    before = _snapshot(private_root)
    monkeypatch.setenv(HARNESS_KILL_SWITCH_ENV, "1")
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=authorization,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _snapshot(private_root) == before


# --- concurrency ---------------------------------------------------------------


def test_twenty_threads_apply_the_target_once(enabled, private_root, monkeypatch):
    writes: list[str] = []
    writes_lock = threading.Lock()
    original = local_effect_harness._write_atomic

    def _counting(path, data, *, private_root):
        with writes_lock:
            writes.append(str(path))
        return original(path, data, private_root=private_root)

    monkeypatch.setattr(local_effect_harness, "_write_atomic", _counting)

    barrier = threading.Barrier(20)
    outcomes: list[object] = []
    outcomes_lock = threading.Lock()

    def _probe() -> None:
        candidate = _candidate()
        authorization = _authorization(candidate)
        barrier.wait()
        try:
            result = apply_local_json_effect_v1(
                candidate=candidate,
                authorization=authorization,
                payload=PAYLOAD,
                private_root=private_root,
            )
            outcome: object = result.replayed
        except Exception as exc:  # noqa: BLE001 - outcome classification only
            outcome = exc
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=_probe) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(False) == 1
    assert outcomes.count(True) == 19
    target = str(target_path_v1(private_root=private_root, target_ref=TARGET_REF))
    assert writes.count(target) == 1
    assert Path(target).read_bytes() == _canonical(PAYLOAD)


_WORKER = """
import json, sys
from pathlib import Path

from workflow_kernel.local_effect_harness import (
    apply_local_json_effect_v1,
    build_local_json_effect_candidate_v1,
    issue_local_json_effect_authorization_v1,
)

root = Path(sys.argv[1])
payload = {"note": "hgwk", "revision": 1}
candidate = build_local_json_effect_candidate_v1(
    target_ref="hgwk/local-harness/note",
    payload=payload,
    idempotency_key="hgwk-local-effect-0001",
)
authorization = issue_local_json_effect_authorization_v1(
    candidate=candidate, ttl_seconds=30.0
)
result = apply_local_json_effect_v1(
    candidate=candidate,
    authorization=authorization,
    payload=payload,
    private_root=root,
)
print(json.dumps({
    "replayed": result.replayed,
    "receipt_digest": result.receipt.receipt_digest,
}))
"""


def test_two_processes_apply_the_target_once(enabled, private_root):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(private_root)],
            cwd=str(REPO_ROOT),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        outputs.append(json.loads(stdout.strip()))

    assert [output["replayed"] for output in outputs].count(False) == 1
    assert len({output["receipt_digest"] for output in outputs}) == 1
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    assert target.read_bytes() == _canonical(PAYLOAD)


# --- scope guards --------------------------------------------------------------


def test_public_api_accepts_no_executor_or_callback_injection():
    banned = {"executor", "callback", "sink", "writer", "hook", "path", "target_path"}
    for name, function in vars(local_effect_harness).items():
        if name.startswith("_") or not callable(function):
            continue
        if not getattr(function, "__module__", "") == local_effect_harness.__name__:
            continue
        if not inspect.isfunction(function):
            continue
        for parameter in inspect.signature(function).parameters.values():
            assert parameter.name not in banned, f"{name} exposes {parameter.name}"


def test_crash_injection_is_a_test_only_enum_not_a_callback():
    parameter = inspect.signature(apply_local_json_effect_v1).parameters["crash_phase"]
    assert parameter.default is None
    assert local_effect_harness.CRASH_PHASES == frozenset({
        "after_intent_before_apply",
        "after_apply_before_receipt",
    })


def test_module_opens_no_network_and_no_legacy_effect_sink():
    for banned in (
        "import socket",
        "import requests",
        "urllib",
        "httpx",
        "subprocess",
        "open_gate",
        "target_registry",
        "workflow_execution_ledger",
        "governed_runtime",
        "taskpool",
    ):
        assert banned not in MODULE_SOURCE, f"harness must not reach {banned!r}"
    for banned_word in ("mission", "deploy", "push", "socket", "connect"):
        assert not re.search(rf"\b{banned_word}\w*\b", MODULE_SOURCE), (
            f"harness must not reach {banned_word!r}"
        )


def test_receipt_store_does_not_claim_to_be_the_execution_ledger():
    assert not hasattr(local_effect_harness, "EffectReceiptV1")
    assert "EffectReceiptV1" in MODULE_SOURCE
    assert "Execution Ledger" in MODULE_SOURCE
    assert "not the Workflow Execution Ledger" in MODULE_SOURCE
    assert "experimental" in MODULE_SOURCE.lower()


def test_harness_is_not_wired_into_the_governed_runtime_or_package():
    for path in (
        Path("workflow_kernel/governed_runtime.py"),
        Path("workflow_kernel/__init__.py"),
        Path("workflow_kernel/runtime.py"),
        Path("workflow_kernel/workflow_execution_ledger.py"),
    ):
        assert "local_effect_harness" not in path.read_text(encoding="utf-8")


# --- in-lock gates, authorization economy and anchored removal -----------------


def _effect_artifacts(root: Path) -> dict[str, str]:
    """Snapshot durable harness artifacts only, ignoring lock leaves and dirs."""
    return {
        name: value
        for name, value in _snapshot(root).items()
        if value != "dir" and not Path(name).name.endswith(".lock")
    }


@pytest.fixture
def kill_switch_inside_the_transaction(monkeypatch):
    """Engage the kill switch after the transaction lock has been acquired."""
    original = local_effect_harness._Transaction.__enter__

    def _entered(self):
        transaction = original(self)
        os.environ[HARNESS_KILL_SWITCH_ENV] = "1"
        return transaction

    monkeypatch.setattr(local_effect_harness._Transaction, "__enter__", _entered)


def test_kill_switch_engaged_inside_the_lock_blocks_apply_mutation(
    enabled, private_root, kill_switch_inside_the_transaction
):
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        _apply(private_root)
    assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _effect_artifacts(private_root) == before
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()
    assert not intent_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert not receipt_path_v1(private_root=private_root, idempotency_key=KEY).exists()


def test_kill_switch_engaged_inside_the_lock_blocks_compensation_mutation(
    enabled, private_root, monkeypatch, request
):
    result = _apply(private_root)
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=30.0
    )
    before = _effect_artifacts(private_root)
    request.getfixturevalue("kill_switch_inside_the_transaction")
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=authorization,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _effect_artifacts(private_root) == before
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical(PAYLOAD)
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).exists()


def test_kill_switch_engaged_inside_the_lock_blocks_reconcile_receipt(
    enabled, private_root, request
):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_apply_before_receipt")
    before = _effect_artifacts(private_root)
    request.getfixturevalue("kill_switch_inside_the_transaction")
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _effect_artifacts(private_root) == before
    assert not receipt_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert intent_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical(PAYLOAD)


def test_disabled_inside_the_lock_blocks_reconcile_receipt(
    enabled, private_root, monkeypatch
):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_intent_before_apply")
    before = _effect_artifacts(private_root)
    original = local_effect_harness._Transaction.__enter__

    def _entered(self):
        transaction = original(self)
        os.environ[HARNESS_ENV] = "0"
        return transaction

    monkeypatch.setattr(local_effect_harness._Transaction, "__enter__", _entered)
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    assert error.value.code == "local_effect_harness_disabled"
    assert _effect_artifacts(private_root) == before
    assert not receipt_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert intent_path_v1(private_root=private_root, idempotency_key=KEY).exists()
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()


def test_unresolved_intent_rejection_does_not_consume_the_authorization(
    enabled, private_root
):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_intent_before_apply")

    candidate = _candidate()
    authorization = _authorization(candidate)
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_unresolved_intent"
    assert _effect_artifacts(private_root) == before

    reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    resumed = _candidate(idempotency_key=f"{KEY}-resumed")
    result = apply_local_json_effect_v1(
        candidate=resumed,
        authorization=authorization,
        payload=PAYLOAD,
        private_root=private_root,
    )
    assert result.replayed is False
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical(PAYLOAD)


def test_replay_does_not_consume_the_authorization(enabled, private_root):
    _apply(private_root)
    candidate = _candidate()
    authorization = _authorization(candidate)
    replay = apply_local_json_effect_v1(
        candidate=candidate,
        authorization=authorization,
        payload=PAYLOAD,
        private_root=private_root,
    )
    assert replay.replayed is True

    resumed = _candidate(idempotency_key=f"{KEY}-resumed")
    result = apply_local_json_effect_v1(
        candidate=resumed,
        authorization=authorization,
        payload=PAYLOAD,
        private_root=private_root,
    )
    assert result.replayed is False


def test_idempotency_binding_conflict_does_not_consume_the_authorization(
    enabled, private_root
):
    _apply(private_root)
    conflicting = _candidate(payload=OTHER_PAYLOAD)
    authorization = _authorization(conflicting)
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        apply_local_json_effect_v1(
            candidate=conflicting,
            authorization=authorization,
            payload=OTHER_PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_idempotency_binding_conflict"
    assert _effect_artifacts(private_root) == before

    resumed = _candidate(payload=OTHER_PAYLOAD, idempotency_key=f"{KEY}-resumed")
    result = apply_local_json_effect_v1(
        candidate=resumed,
        authorization=authorization,
        payload=OTHER_PAYLOAD,
        private_root=private_root,
    )
    assert result.replayed is False


def test_forged_authorization_fails_closed_before_any_receipt_is_returned(
    enabled, private_root
):
    _apply(private_root)
    candidate = _candidate()
    forged = ExperimentalHarnessExactAuthorizationV1(
        schema_version="harness-exact-authorization-experimental-v1",
        nonce="00" * 16,
        adapter_id=HARNESS_ADAPTER_ID,
        operation=HARNESS_APPLY_OPERATION,
        target_digest=_authorization(candidate).target_digest,
        payload_digest=canonical_payload_digest_v1(PAYLOAD),
        side_effect_tier=HARNESS_SIDE_EFFECT_TIER,
        expires_at_monotonic=float("inf"),
        _ticket=effect_admission._OneShotTicket(),
    )
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=forged,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_not_registered"


# --- C: crash phases that reconcile can never resolve --------------------------


def test_after_backup_before_intent_is_not_an_offered_crash_phase(
    enabled, private_root
):
    assert "after_backup_before_intent" not in local_effect_harness.CRASH_PHASES
    candidate = _candidate()
    authorization = _authorization(candidate)
    with pytest.raises(LocalEffectHarnessError) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
            crash_phase="after_backup_before_intent",
        )
    assert error.value.code == "local_effect_crash_phase_invalid"
    assert _snapshot(private_root) == {}

    result = apply_local_json_effect_v1(
        candidate=candidate,
        authorization=authorization,
        payload=PAYLOAD,
        private_root=private_root,
    )
    assert result.replayed is False
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical(PAYLOAD)


def test_every_offered_crash_phase_is_reconcilable(enabled, tmp_path):
    for index, phase in enumerate(sorted(local_effect_harness.CRASH_PHASES)):
        root = tmp_path / f"private-{index}"
        root.mkdir(mode=0o700)
        with pytest.raises(LocalEffectHarnessUnknownError):
            _apply(root, crash_phase=phase)
        receipt = reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=root)
        assert receipt.result_classification in {"applied", "not_applied"}
        assert receipt.next_action == "none"


# --- B: descriptor-relative compensation removal -------------------------------


def test_compensation_removal_is_anchored_against_a_swapped_targets_directory(
    enabled, private_root, tmp_path, monkeypatch
):
    result = _apply(private_root, payload=PAYLOAD, candidate=_candidate())
    target_path = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    # The applied target has no preimage, so compensation removes it.
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    external = outside / target_path.name
    external.write_text('{"outside": true}', encoding="utf-8")

    original_unlink = os.unlink
    swapped: list[str] = []

    def _swapping_unlink(name, *args, **kwargs):
        if Path(str(name)).name == target_path.name and not swapped:
            swapped.append(str(name))
            targets = target_path.parent
            original_unlink(target_path)
            targets.rmdir()
            targets.symlink_to(outside, target_is_directory=True)
        return original_unlink(name, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", _swapping_unlink)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=issue_local_json_effect_compensation_authorization_v1(
                receipt=result.receipt, ttl_seconds=30.0
            ),
            private_root=private_root,
        )
    assert error.value.code == "local_effect_private_file_unsafe"
    assert swapped, "the removal window was never exercised"
    assert external.read_text(encoding="utf-8") == '{"outside": true}'
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).exists()


# --- P1-A: expiry is checked on state-only replay paths ------------------------

SECOND_KEY = "hgwk-second"
COMPENSATION_UNKNOWN_ACTION = "manual_compensation_reconcile"


def _compensate(private_root: Path, receipt, **kwargs):
    return compensate_local_json_effect_v1(
        receipt=receipt,
        authorization=issue_local_json_effect_compensation_authorization_v1(
            receipt=receipt, ttl_seconds=30.0
        ),
        private_root=private_root,
        **kwargs,
    )


def _applied_over_an_existing_preimage(private_root: Path):
    """Apply twice on one target so compensation has a preimage to restore."""
    _apply(private_root)
    return _apply(
        private_root,
        candidate=_candidate(payload=OTHER_PAYLOAD, idempotency_key=SECOND_KEY),
        payload=OTHER_PAYLOAD,
    )


def _wind_the_trusted_clock_forward(monkeypatch) -> None:
    real_monotonic = effect_admission.time.monotonic
    monkeypatch.setattr(
        effect_admission.time, "monotonic", lambda: real_monotonic() + 1000.0
    )


def test_apply_replay_rejects_an_expired_fresh_authorization(
    enabled, private_root, monkeypatch
):
    """A replay is still an authorized entry point: an expired ticket fails closed."""
    _apply(private_root)
    candidate = _candidate()
    authorization = _authorization(candidate, ttl_seconds=1.0)
    before = _effect_artifacts(private_root)
    _wind_the_trusted_clock_forward(monkeypatch)
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_expired"
    assert _effect_artifacts(private_root) == before
    assert authorization._ticket.consumed is False


def test_compensation_replay_rejects_an_expired_fresh_authorization(
    enabled, private_root, monkeypatch
):
    result = _apply(private_root)
    _compensate(private_root, result.receipt)
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=1.0
    )
    before = _effect_artifacts(private_root)
    _wind_the_trusted_clock_forward(monkeypatch)
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=authorization,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_expired"
    assert _effect_artifacts(private_root) == before
    assert authorization._ticket.consumed is False


def test_expired_authorization_never_leaks_a_receipt_on_an_unresolved_intent(
    enabled, private_root, monkeypatch
):
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_intent_before_apply")
    candidate = _candidate()
    authorization = _authorization(candidate, ttl_seconds=1.0)
    before = _effect_artifacts(private_root)
    _wind_the_trusted_clock_forward(monkeypatch)
    with pytest.raises((LocalEffectHarnessError, EffectAdmissionError)) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "effect_harness_authorization_expired"
    assert _effect_artifacts(private_root) == before


# --- P1-B: a compensation replay must still match the target -------------------


def test_compensation_replay_rejects_a_drifted_target(enabled, private_root):
    result = _apply(private_root)
    compensation = _compensate(private_root, result.receipt)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.write_bytes(_canonical({"drifted": True}))

    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=30.0
    )
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=authorization,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_compensation_replay_readback_mismatch"
    assert _effect_artifacts(private_root) == before
    assert authorization._ticket.consumed is False

    # The state-only rejection spent nothing, so repairing the drift lets the
    # same authorization replay the existing compensation receipt.
    target.unlink()
    replayed = compensate_local_json_effect_v1(
        receipt=result.receipt,
        authorization=authorization,
        private_root=private_root,
    )
    assert replayed == compensation


def test_compensation_replay_readback_mismatch_never_returns_the_receipt(
    enabled, private_root
):
    second = _applied_over_an_existing_preimage(private_root)
    _compensate(private_root, second.receipt)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.write_bytes(_canonical({"drifted": True}))
    with pytest.raises(LocalEffectHarnessError) as error:
        _compensate(private_root, second.receipt)
    assert error.value.code == "local_effect_compensation_replay_readback_mismatch"
    assert target.read_bytes() == _canonical({"drifted": True})


# --- P1-C: compensation intent, crash protocol and compensation reconcile ------


def test_compensation_crash_phases_are_a_separate_finite_enum():
    assert local_effect_harness.COMPENSATION_CRASH_PHASES == frozenset({
        "after_compensation_intent_before_restore",
        "after_compensation_restore_before_receipt",
    })
    assert not (
        local_effect_harness.COMPENSATION_CRASH_PHASES
        & local_effect_harness.CRASH_PHASES
    )
    parameter = inspect.signature(compensate_local_json_effect_v1).parameters[
        "compensation_crash_phase"
    ]
    assert parameter.default is None


@pytest.mark.parametrize(
    "phase", ["whenever", "after_intent_before_apply", "after_apply_before_receipt"]
)
def test_compensation_crash_phase_is_rejected_before_any_consume_or_write(
    enabled, private_root, phase
):
    result = _apply(private_root)
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=30.0
    )
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=authorization,
            private_root=private_root,
            compensation_crash_phase=phase,
        )
    assert error.value.code == "local_effect_compensation_crash_phase_invalid"
    assert _effect_artifacts(private_root) == before
    assert authorization._ticket.consumed is False
    assert not compensation_intent_path_v1(
        private_root=private_root, idempotency_key=KEY
    ).exists()


def test_compensation_intent_binds_the_applied_receipt_and_both_states(
    enabled, private_root
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError) as error:
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_intent_before_restore",
        )
    assert error.value.code == "effect_result_unknown"
    assert error.value.next_action == COMPENSATION_UNKNOWN_ACTION

    path = compensation_intent_path_v1(private_root=private_root, idempotency_key=KEY)
    intent = json.loads(path.read_text(encoding="utf-8"))
    body = {name: value for name, value in intent.items() if name != "document_digest"}
    assert intent["document_digest"] == (
        "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    )
    assert intent["operation"] == HARNESS_COMPENSATION_OPERATION
    assert intent["target_ref"] == TARGET_REF
    assert intent["idempotency_key"] == KEY
    assert intent["backup_ref"] == result.receipt.backup_ref
    assert intent["compensated_receipt_digest"] == result.receipt.receipt_digest
    assert intent["authorization_binding_digest"].startswith("sha256:")
    assert intent["apply_post_readback"] == dict(result.receipt.post_readback)
    assert intent["expected_restored_readback"] == {
        "present": False,
        "content_digest": None,
    }
    # The compensation was journalled but never ran.
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical(PAYLOAD)
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).exists()


def test_compensation_crash_after_restore_leaves_a_recoverable_journal(
    enabled, private_root
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError) as error:
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_restore_before_receipt",
        )
    assert error.value.idempotency_key == KEY
    assert compensation_intent_path_v1(
        private_root=private_root, idempotency_key=KEY
    ).is_file()
    assert not target_path_v1(private_root=private_root, target_ref=TARGET_REF).exists()
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).exists()


def test_compensation_reconcile_classifies_a_restored_compensation(
    enabled, private_root
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_restore_before_receipt",
        )
    receipt = reconcile_local_json_compensation_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert receipt.operation == HARNESS_COMPENSATION_OPERATION
    assert receipt.result_classification == "applied"
    assert receipt.applied is True
    assert receipt.next_action == "none"
    assert receipt.resolution == "compensation_reconcile"
    assert receipt.compensated_receipt_digest == result.receipt.receipt_digest
    assert receipt.post_readback == {"present": False, "content_digest": None}
    assert receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).is_file()


def test_compensation_reconcile_classifies_a_not_applied_compensation(
    enabled, private_root
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_intent_before_restore",
        )
    receipt = reconcile_local_json_compensation_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert receipt.result_classification == "not_applied"
    assert receipt.applied is False
    assert receipt.next_action == "manual_compensation_required"
    assert receipt.resolution == "compensation_reconcile"
    assert receipt.post_readback == dict(result.receipt.post_readback)
    assert receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).is_file()
    # Terminal and never retried: the target is still the applied payload.
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical(PAYLOAD)
    assert (
        reconcile_local_json_compensation_v1(
            idempotency_key=KEY, private_root=private_root
        )
        == receipt
    )


def test_compensation_reconcile_reports_unknown_without_a_terminal_receipt(
    enabled, private_root, monkeypatch
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_intent_before_restore",
        )
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.write_bytes(_canonical({"drifted": True}))
    before = _effect_artifacts(private_root)

    writes: list[str] = []
    original = local_effect_harness._write_atomic

    def _counting(path, data, *, private_root):
        writes.append(str(path))
        return original(path, data, private_root=private_root)

    monkeypatch.setattr(local_effect_harness, "_write_atomic", _counting)
    receipt = reconcile_local_json_compensation_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert receipt.result_classification == "unknown"
    assert receipt.applied is False
    assert receipt.next_action == "manual_reconcile"
    assert writes == []
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).exists()
    assert _effect_artifacts(private_root) == before
    assert target.read_bytes() == _canonical({"drifted": True})


def test_compensation_reconcile_never_restores_deletes_or_retries(enabled):
    assert set(inspect.signature(reconcile_local_json_compensation_v1).parameters) == {
        "idempotency_key",
        "private_root",
    }


def test_compensation_reconcile_requires_a_compensation_intent(enabled, private_root):
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_compensation_v1(
            idempotency_key="absent", private_root=private_root
        )
    assert error.value.code == "local_effect_compensation_intent_missing"
    assert _snapshot(private_root) == {}


def test_compensation_reconcile_rejects_a_tampered_compensation_intent(
    enabled, private_root
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_restore_before_receipt",
        )
    path = compensation_intent_path_v1(private_root=private_root, idempotency_key=KEY)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["expected_restored_readback"] = {"present": True, "content_digest": None}
    path.write_text(json.dumps(document), encoding="utf-8")
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_compensation_v1(
            idempotency_key=KEY, private_root=private_root
        )
    assert error.value.code == "local_effect_journal_tampered"
    assert _effect_artifacts(private_root) == before


def test_compensation_reconcile_replays_only_a_consistent_receipt(
    enabled, private_root
):
    result = _apply(private_root)
    compensation = _compensate(private_root, result.receipt)
    assert (
        reconcile_local_json_compensation_v1(
            idempotency_key=KEY, private_root=private_root
        )
        == compensation
    )
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.write_bytes(_canonical({"drifted": True}))
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_compensation_v1(
            idempotency_key=KEY, private_root=private_root
        )
    assert error.value.code == "local_effect_compensation_replay_readback_mismatch"


def test_compensation_receipt_conflict_is_rejected(enabled, private_root):
    result = _apply(private_root)
    compensation = _compensate(private_root, result.receipt)
    forged = local_effect_harness._sealed_receipt({
        name: value
        for name, value in dataclasses.asdict(
            dataclasses.replace(
                compensation, compensated_receipt_digest="sha256:" + "cd" * 32
            )
        ).items()
        if name != "receipt_digest"
    })
    local_effect_harness._write_atomic(
        receipt_path_v1(
            private_root=private_root, idempotency_key=KEY, compensation=True
        ),
        local_effect_harness._receipt_document(forged),
        private_root=private_root,
    )
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_compensation_v1(
            idempotency_key=KEY, private_root=private_root
        )
    assert error.value.code == "local_effect_compensation_receipt_conflict"
    with pytest.raises(LocalEffectHarnessError) as error:
        _compensate(private_root, result.receipt)
    assert error.value.code == "local_effect_compensation_receipt_conflict"


def test_compensate_refuses_an_unresolved_compensation_intent(enabled, private_root):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_intent_before_restore",
        )
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=30.0
    )
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        compensate_local_json_effect_v1(
            receipt=result.receipt,
            authorization=authorization,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_compensation_unresolved_intent"
    assert "reconcile_local_json_compensation_v1" in str(error.value)
    assert _effect_artifacts(private_root) == before
    assert authorization._ticket.consumed is False


def test_compensation_post_readback_mismatch_is_resolvable_by_reconcile(
    enabled, private_root, monkeypatch
):
    second = _applied_over_an_existing_preimage(private_root)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    original = local_effect_harness._write_atomic

    def _corrupting(path, data, *, private_root):
        if Path(path) == target:
            data = _canonical({"corrupt": True})
        return original(path, data, private_root=private_root)

    monkeypatch.setattr(local_effect_harness, "_write_atomic", _corrupting)
    with pytest.raises(LocalEffectHarnessUnknownError) as error:
        _compensate(private_root, second.receipt)
    assert error.value.code == "effect_result_unknown"
    assert error.value.next_action == COMPENSATION_UNKNOWN_ACTION
    monkeypatch.setattr(local_effect_harness, "_write_atomic", original)

    unresolved = reconcile_local_json_compensation_v1(
        idempotency_key=SECOND_KEY, private_root=private_root
    )
    assert unresolved.result_classification == "unknown"
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=SECOND_KEY, compensation=True
    ).exists()

    # Once the operator repairs the target by hand, reconcile is terminal.
    target.write_bytes(_canonical(PAYLOAD))
    resolved = reconcile_local_json_compensation_v1(
        idempotency_key=SECOND_KEY, private_root=private_root
    )
    assert resolved.result_classification == "applied"
    assert resolved.applied is True
    assert receipt_path_v1(
        private_root=private_root, idempotency_key=SECOND_KEY, compensation=True
    ).is_file()


def test_apply_reconcile_never_handles_a_compensation_intent(enabled, private_root):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_intent_before_restore",
        )
    compensation_intent = compensation_intent_path_v1(
        private_root=private_root, idempotency_key=KEY
    )
    before = _effect_artifacts(private_root)
    assert (
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
        == result.receipt
    )
    assert _effect_artifacts(private_root) == before
    assert compensation_intent.is_file()
    assert compensation_intent_path_v1(
        private_root=private_root, idempotency_key=KEY
    ) != intent_path_v1(private_root=private_root, idempotency_key=KEY)
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).exists()


def test_compensation_reconcile_is_closed_by_the_kill_switch(
    enabled, private_root, monkeypatch
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_restore_before_receipt",
        )
    before = _effect_artifacts(private_root)
    monkeypatch.setenv(HARNESS_KILL_SWITCH_ENV, "1")
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_compensation_v1(
            idempotency_key=KEY, private_root=private_root
        )
    assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _effect_artifacts(private_root) == before


def test_kill_switch_inside_the_lock_blocks_the_compensation_reconcile_receipt(
    enabled, private_root, request
):
    result = _apply(private_root)
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_restore_before_receipt",
        )
    before = _effect_artifacts(private_root)
    request.getfixturevalue("kill_switch_inside_the_transaction")
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_compensation_v1(
            idempotency_key=KEY, private_root=private_root
        )
    assert error.value.code == "local_effect_harness_kill_switch_engaged"
    assert _effect_artifacts(private_root) == before
    assert not receipt_path_v1(
        private_root=private_root, idempotency_key=KEY, compensation=True
    ).exists()


def test_compensation_receipt_binding_is_documented():
    assert "restore payload" in MODULE_SOURCE
    assert "manual_compensation_required" in MODULE_SOURCE
    assert "compensated_receipt_digest" in MODULE_SOURCE


def _compensation_request_binding(receipt, authorization_binding_digest: str) -> str:
    """The exact request binding a compensation must derive for itself."""
    return (
        "sha256:"
        + hashlib.sha256(
            _canonical({
                "adapter_id": HARNESS_ADAPTER_ID,
                "operation": HARNESS_COMPENSATION_OPERATION,
                "target_digest": receipt.target_digest,
                "payload_digest": receipt.receipt_digest,
                "idempotency_key": receipt.idempotency_key,
                "authorization_binding_digest": authorization_binding_digest,
            })
        ).hexdigest()
    )


def test_compensation_receipt_binds_the_authorized_restore_exactly(
    enabled, private_root
):
    """Every binding field must describe the restore that was authorized."""
    result = _apply(private_root)
    authorization = issue_local_json_effect_compensation_authorization_v1(
        receipt=result.receipt, ttl_seconds=30.0
    )
    binding = effect_admission._local_harness_binding_digest(authorization)
    compensation = compensate_local_json_effect_v1(
        receipt=result.receipt,
        authorization=authorization,
        private_root=private_root,
    )

    # The authorization was issued over the applied receipt digest, so that is
    # the only payload digest this receipt may claim.
    assert compensation.payload_digest == result.receipt.receipt_digest
    assert compensation.payload_digest != result.receipt.payload_digest
    assert compensation.authorization_binding_digest == binding
    assert compensation.request_binding_digest == _compensation_request_binding(
        result.receipt, binding
    )
    assert compensation.request_binding_digest != result.receipt.request_binding_digest
    # A compensation has no candidate of its own; the applied receipt digest is
    # the only link back to the effect it rolled back.
    assert compensation.candidate_digest is None
    assert compensation.compensated_receipt_digest == result.receipt.receipt_digest
    assert compensation.backup_ref == result.receipt.backup_ref

    intent = json.loads(
        compensation_intent_path_v1(
            private_root=private_root, idempotency_key=KEY
        ).read_text(encoding="utf-8")
    )
    assert intent["payload_digest"] == result.receipt.receipt_digest
    assert intent["candidate_digest"] is None
    assert intent["request_binding_digest"] == compensation.request_binding_digest
    assert intent["authorization_binding_digest"] == binding


def test_direct_and_reconciled_compensation_receipts_are_isomorphic(enabled, tmp_path):
    roots = []
    for index in range(2):
        root = tmp_path / f"private-{index}"
        root.mkdir(mode=0o700)
        roots.append(root)

    applied = _apply(roots[0])
    direct = _compensate(roots[0], applied.receipt)

    crashed = _apply(roots[1])
    assert crashed.receipt == applied.receipt
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            roots[1],
            crashed.receipt,
            compensation_crash_phase="after_compensation_restore_before_receipt",
        )
    reconciled = reconcile_local_json_compensation_v1(
        idempotency_key=KEY, private_root=roots[1]
    )

    varying = {"resolution", "receipt_digest"}
    assert {
        name: value
        for name, value in dataclasses.asdict(direct).items()
        if name not in varying
    } == {
        name: value
        for name, value in dataclasses.asdict(reconciled).items()
        if name not in varying
    }
    assert direct.resolution == "compensation"
    assert reconciled.resolution == "compensation_reconcile"


# --- P1-D: one root-scoped transaction lock, same order everywhere -------------


@pytest.fixture
def lock_order(monkeypatch) -> list[str]:
    acquired: list[str] = []
    original = runtime_state.exclusive_ledger_lock

    @contextlib.contextmanager
    def _recording(ledger_path, *, private_root=None):
        acquired.append(str(ledger_path))
        with original(ledger_path, private_root=private_root) as anchor:
            yield anchor

    monkeypatch.setattr(runtime_state, "exclusive_ledger_lock", _recording)
    return acquired


def test_every_entry_point_locks_global_before_key(enabled, private_root, lock_order):
    global_anchor = str(global_transaction_lock_path_v1(private_root=private_root))
    key_anchor = str(receipt_path_v1(private_root=private_root, idempotency_key=KEY))

    result = _apply(private_root)
    assert lock_order == [global_anchor, key_anchor]

    lock_order.clear()
    with pytest.raises(LocalEffectHarnessUnknownError):
        _compensate(
            private_root,
            result.receipt,
            compensation_crash_phase="after_compensation_restore_before_receipt",
        )
    assert lock_order == [global_anchor, key_anchor]

    lock_order.clear()
    reconcile_local_json_compensation_v1(idempotency_key=KEY, private_root=private_root)
    assert lock_order == [global_anchor, key_anchor]

    lock_order.clear()
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(
            private_root,
            candidate=_candidate(idempotency_key=SECOND_KEY),
            crash_phase="after_intent_before_apply",
        )
    reconcile_local_json_effect_v1(
        idempotency_key=SECOND_KEY, private_root=private_root
    )
    second_anchor = str(
        receipt_path_v1(private_root=private_root, idempotency_key=SECOND_KEY)
    )
    assert lock_order == [global_anchor, second_anchor] * 2


@contextlib.contextmanager
def _no_parent_coordination(*arguments, **keywords):
    """Drop the ledger lock's incidental parent-directory serialization."""
    yield


def _interleaving_probe(monkeypatch, target: Path, rendezvous: threading.Barrier):
    """Record durable steps per thread and rendezvous on the first target read."""
    events: list[str] = []
    guard = threading.Lock()
    arrived: set[str] = set()
    read_original = local_effect_harness._read_optional_bytes
    write_original = local_effect_harness._write_atomic

    def _record(step: str) -> None:
        with guard:
            events.append(f"{threading.current_thread().name}:{step}")

    def _reading(path, *, private_root):
        data = read_original(path, private_root=private_root)
        if Path(path) == target:
            _record("read-target")
            name = threading.current_thread().name
            with guard:
                first = name not in arrived
                arrived.add(name)
            if first:
                with contextlib.suppress(threading.BrokenBarrierError):
                    rendezvous.wait(timeout=1.0)
        return data

    def _writing(path, data, *, private_root):
        _record("write")
        return write_original(path, data, private_root=private_root)

    monkeypatch.setattr(local_effect_harness, "_read_optional_bytes", _reading)
    monkeypatch.setattr(local_effect_harness, "_write_atomic", _writing)
    return events


def _run_named_threads(bodies: dict[str, object]) -> None:
    threads = [
        threading.Thread(target=body, name=name) for name, body in bodies.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60.0)
        assert not thread.is_alive()


def test_different_keys_on_one_target_never_interleave(
    enabled, private_root, monkeypatch
):
    """Serialization must be the harness's own root-scoped transaction lock.

    The ledger lock's parent-directory coordination happens to serialize two
    different keys today; it is disabled here so the harness has to own the
    guarantee itself.
    """
    monkeypatch.setattr(
        runtime_state, "_exclusive_parent_coordination", _no_parent_coordination
    )
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    rendezvous = threading.Barrier(2)
    events = _interleaving_probe(monkeypatch, target, rendezvous)
    receipts: dict[str, object] = {}

    def _body(key: str, payload: object):
        def _run() -> None:
            candidate = _candidate(payload=payload, idempotency_key=key)
            receipts[key] = apply_local_json_effect_v1(
                candidate=candidate,
                authorization=_authorization(candidate),
                payload=payload,
                private_root=private_root,
            ).receipt

        return _run

    _run_named_threads({
        "first": _body(KEY, PAYLOAD),
        "second": _body(SECOND_KEY, OTHER_PAYLOAD),
    })

    owners = [event.split(":", 1)[0] for event in events]
    runs = [
        owner
        for index, owner in enumerate(owners)
        if index == 0 or owners[index - 1] != owner
    ]
    assert runs == sorted(set(owners), key=owners.index), f"interleaved: {events}"
    assert len(runs) == 2

    first, second = (receipts[KEY], receipts[SECOND_KEY])
    ordered = (first, second) if runs[0] == "first" else (second, first)
    assert ordered[0].pre_readback == {"present": False, "content_digest": None}
    assert ordered[1].pre_readback == dict(ordered[0].post_readback)
    assert target.read_bytes() == _canonical(
        PAYLOAD if ordered[1] is first else OTHER_PAYLOAD
    )


def test_different_private_roots_are_not_serialized_against_each_other(
    enabled, tmp_path, monkeypatch
):
    roots = []
    for index in range(2):
        root = tmp_path / f"private-{index}"
        root.mkdir(mode=0o700)
        roots.append(root)
    rendezvous = threading.Barrier(2)
    target = target_path_v1(private_root=roots[0], target_ref=TARGET_REF)
    _interleaving_probe(monkeypatch, target, rendezvous)
    met: list[bool] = []

    def _body(root: Path):
        def _run() -> None:
            candidate = _candidate()
            apply_local_json_effect_v1(
                candidate=candidate,
                authorization=_authorization(candidate),
                payload=PAYLOAD,
                private_root=root,
            )
            met.append(True)

        return _run

    # Both roots rendezvous on their own target read; a process-wide lock would
    # deadlock one of them behind the other.
    rendezvous_targets = {
        "first": _body(roots[0]),
        "second": _body(roots[1]),
    }
    _run_named_threads(rendezvous_targets)
    assert len(met) == 2
    for root in roots:
        assert target_path_v1(
            private_root=root, target_ref=TARGET_REF
        ).read_bytes() == _canonical(PAYLOAD)


_CROSS_KEY_WORKER = """
import json, sys
from pathlib import Path

from workflow_kernel.local_effect_harness import (
    apply_local_json_effect_v1,
    build_local_json_effect_candidate_v1,
    issue_local_json_effect_authorization_v1,
)

root, key, revision = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
payload = {"note": "hgwk", "revision": revision}
candidate = build_local_json_effect_candidate_v1(
    target_ref="hgwk/local-harness/note",
    payload=payload,
    idempotency_key=key,
)
result = apply_local_json_effect_v1(
    candidate=candidate,
    authorization=issue_local_json_effect_authorization_v1(
        candidate=candidate, ttl_seconds=30.0
    ),
    payload=payload,
    private_root=root,
)
print(json.dumps({
    "key": key,
    "pre": dict(result.receipt.pre_readback),
    "post": dict(result.receipt.post_readback),
}))
"""


def test_two_processes_with_different_keys_apply_one_target_serially(
    enabled, private_root
):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _CROSS_KEY_WORKER, str(private_root), key, revision],
            cwd=str(REPO_ROOT),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for key, revision in ((KEY, "1"), (SECOND_KEY, "2"))
    ]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        outputs.append(json.loads(stdout.strip()))

    absent = {"present": False, "content_digest": None}
    first = [output for output in outputs if output["pre"] == absent]
    assert len(first) == 1, outputs
    later = [output for output in outputs if output is not first[0]][0]
    assert later["pre"] == first[0]["post"]
    assert target_path_v1(
        private_root=private_root, target_ref=TARGET_REF
    ).read_bytes() == _canonical({
        "note": "hgwk",
        "revision": 2 if later["key"] == SECOND_KEY else 1,
    })


# --- P1-E: an apply-receipt replay must still match the target -----------------


def test_apply_replay_rejects_a_drifted_target(enabled, private_root):
    """The apply replay path already owns this rule; it is the shared contract."""
    first = _apply(private_root)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.write_bytes(_canonical({"drifted": True}))

    candidate = _candidate()
    authorization = _authorization(candidate)
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        apply_local_json_effect_v1(
            candidate=candidate,
            authorization=authorization,
            payload=PAYLOAD,
            private_root=private_root,
        )
    assert error.value.code == "local_effect_replay_readback_mismatch"
    assert _effect_artifacts(private_root) == before
    assert authorization._ticket.consumed is False
    assert target.read_bytes() == _canonical({"drifted": True})

    # Repairing the drift lets the same unspent authorization replay.
    target.write_bytes(_canonical(PAYLOAD))
    replayed = apply_local_json_effect_v1(
        candidate=candidate,
        authorization=authorization,
        payload=PAYLOAD,
        private_root=private_root,
    )
    assert replayed.replayed is True
    assert replayed.receipt == first.receipt


def test_reconcile_replay_rejects_a_drifted_target(enabled, private_root):
    """A reconcile may not return an applied receipt the disk contradicts."""
    first = _apply(private_root)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.write_bytes(_canonical({"drifted": True}))
    before = _effect_artifacts(private_root)

    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    assert error.value.code == "local_effect_replay_readback_mismatch"
    assert _effect_artifacts(private_root) == before
    assert target.read_bytes() == _canonical({"drifted": True})

    target.write_bytes(_canonical(PAYLOAD))
    assert (
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
        == first.receipt
    )


def test_reconcile_replay_rejects_drift_after_a_not_applied_receipt(
    enabled, private_root
):
    """`not_applied` is a claim about the target too, so drift rejects it as well."""
    with pytest.raises(LocalEffectHarnessUnknownError):
        _apply(private_root, crash_phase="after_intent_before_apply")
    receipt = reconcile_local_json_effect_v1(
        idempotency_key=KEY, private_root=private_root
    )
    assert receipt.result_classification == "not_applied"
    assert receipt.post_readback == {"present": False, "content_digest": None}

    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.write_bytes(_canonical({"drifted": True}))
    before = _effect_artifacts(private_root)
    with pytest.raises(LocalEffectHarnessError) as error:
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    assert error.value.code == "local_effect_replay_readback_mismatch"
    assert _effect_artifacts(private_root) == before
    assert target.read_bytes() == _canonical({"drifted": True})

    target.unlink()
    assert (
        reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
        == receipt
    )


def test_reconcile_of_an_existing_receipt_still_takes_the_root_global_lock(
    enabled, private_root, lock_order
):
    """No receipt fast return outside the lock: the readback is a locked read."""
    _apply(private_root)
    lock_order.clear()
    reconcile_local_json_effect_v1(idempotency_key=KEY, private_root=private_root)
    assert lock_order == [
        str(global_transaction_lock_path_v1(private_root=private_root)),
        str(receipt_path_v1(private_root=private_root, idempotency_key=KEY)),
    ]


def test_read_only_receipt_api_stays_a_history_read_under_drift(enabled, private_root):
    """The read-only API reports the stored receipt, never current target state."""
    first = _apply(private_root)
    target = target_path_v1(private_root=private_root, target_ref=TARGET_REF)
    target.write_bytes(_canonical({"drifted": True}))
    assert (
        read_local_json_effect_receipt_v1(
            idempotency_key=KEY, private_root=private_root
        )
        == first.receipt
    )


def test_apply_and_reconcile_share_one_replay_readback_check(enabled):
    """One helper owns the rule so the two replay paths cannot drift apart."""
    helper = local_effect_harness._replayed_apply_receipt
    source = inspect.getsource(local_effect_harness)
    assert source.count("local_effect_replay_readback_mismatch") == 1
    assert inspect.getsource(helper).count("local_effect_replay_readback_mismatch") == 1
    for function in (apply_local_json_effect_v1, reconcile_local_json_effect_v1):
        assert "_replayed_apply_receipt" in inspect.getsource(function)
