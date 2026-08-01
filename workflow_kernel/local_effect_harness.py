"""Experimental local-write effect harness for exact private JSON targets.

Scope. This module is a standalone, default-off harness slice. It applies one
kind of real side effect only: an exact JSON document written into a caller
supplied private root, inside the harness's own namespace, addressed by the
sha256 of a logical target reference. There is no adapter registry, no callable
injection, no path parameter, no network access and no external tier. The
harness is not reachable from the governed runtime, a manifest or a grant.

Default-off. Every entry point is closed unless
``HGWK_LOCAL_EFFECT_HARNESS_ENABLED`` is explicitly truthy, and
``HGWK_LOCAL_EFFECT_HARNESS_KILL_SWITCH`` closes every stage independently. The
effect precursor's own flag never opens this harness. Because exact harness
authorizations are issued by the precursor's process-local registry, that
module's flag and kill switch must also be open; either one closed fails
closed here too.

Authority. Callers must supply an exact, one-shot
``ExperimentalHarnessExactAuthorizationV1`` issued by the precursor registry
and consumed through its package-private local-harness bridge. That
authorization is a test/local harness input; it does not satisfy the SSOT
section 6.4 production authority chain. Entry validates it without consuming
it, so a state-only rejection (replay, unresolved intent, idempotency binding
conflict, drift) never spends the one-shot capability, while a forged or
mismatched authorization still fails closed before any receipt is returned.
The consume itself happens inside the transaction, after every state conflict
and readback, immediately before the first mutation.

Gates. ``_reject_if_disabled`` runs at entry and again inside the transaction
before every filesystem mutation, so a kill switch engaged while a caller
waited for the lock still closes the effect. These environment flags are
cooperative, in-process control for trusted local callers; they are not a
defence against a hostile same-UID thread or process that can edit the
environment, the private root or these files directly.

Receipts. ``ExperimentalLocalEffectReceiptV1`` is a standalone harness
artifact and effect journal. It is not the canonical production EffectReceiptV1
and its store is not the Workflow Execution Ledger. Production integration
still owes a projection of a canonical EffectReceiptV1 into the Workflow
Execution Ledger; nothing here provides or replaces that.

Durable order under one lock: pre-read, content-addressed preimage backup,
intent journal, target apply, post-readback, terminal receipt. A crash after
the intent journal exists leaves a recoverable journal and yields
``effect_result_unknown``, which may only enter reconcile, never a blind retry.
Only those phases are injectable: reconcile classifies from the intent, so a
crash before the intent is durable is not reconcilable and is not offered.

Compensation follows the same protocol with its own journal: read the backup,
check drift, consume the fresh authorization, write a compensation intent that
binds the applied receipt digest, the backup, the pre-compensation readback and
the expected restored readback, restore or remove the target, read back, then
seal a terminal compensation receipt. ``COMPENSATION_CRASH_PHASES`` injects the
two phases that leave that journal behind, and
``reconcile_local_json_compensation_v1`` classifies them from readback only. A
compensation classified ``not_applied`` is terminal with
``manual_compensation_required``: this harness never retries a compensation on
its own, and an unresolved compensation intent blocks a second compensate.

Locking. Two different idempotency keys may address one target, so every entry
point takes one root-scoped global transaction lock first and the per-key lock
second. That fixed order trades throughput for a total order per private root
and cannot deadlock. Different private roots never block each other.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, NoReturn

from workflow_kernel import runtime_state
from workflow_kernel.effect_admission import (
    EffectAdmissionCandidateV1,
    ExperimentalHarnessExactAuthorizationV1,
    _commit_local_harness_authorization_v1,
    _validate_local_harness_authorization_v1,
    derive_effect_admission_candidate_digest_v1,
    issue_experimental_harness_exact_authorization_v1,
)

HARNESS_ADAPTER_ID = "experimental.local_private_json_v1"
HARNESS_APPLY_OPERATION = "write_json"
HARNESS_COMPENSATION_OPERATION = "restore_json"
HARNESS_TARGET_KIND = "local_private_json"
HARNESS_SIDE_EFFECT_TIER = "local_write"
HARNESS_NAMESPACE = ".hgwk-local-effect-harness"

# Only phases that leave a durable intent are offered: reconcile classifies from
# the intent journal, so a crash before the intent exists could never resolve to
# anything but `intent_missing` and must never be presented as recoverable.
CRASH_PHASES = frozenset({
    "after_intent_before_apply",
    "after_apply_before_receipt",
})

# Compensation has its own journal and its own reconcile, so it has its own
# finite phase enum. Reusing the apply phases would make an injected crash
# ambiguous about which journal it left behind.
COMPENSATION_CRASH_PHASES = frozenset({
    "after_compensation_intent_before_restore",
    "after_compensation_restore_before_receipt",
})

_ENABLED_ENV = "HGWK_LOCAL_EFFECT_HARNESS_ENABLED"
_ENABLED_ON_VALUES = frozenset({"1", "true", "yes", "on"})
_KILL_SWITCH_ENV = "HGWK_LOCAL_EFFECT_HARNESS_KILL_SWITCH"
_KILL_SWITCH_OFF_VALUES = frozenset({"", "0", "false", "no", "off"})

_CANDIDATE_SCHEMA_VERSION = "effect-admission-candidate-experimental-v1"
_INTENT_SCHEMA_VERSION = "experimental-local-effect-intent-v1"
_COMPENSATION_INTENT_SCHEMA_VERSION = "experimental-local-effect-compensation-intent-v1"
_RECEIPT_SCHEMA_VERSION = "experimental-local-effect-receipt-v1"
_BACKUP_SCHEMA_VERSION = "experimental-local-effect-backup-v1"

_TARGETS_DIRECTORY = "targets"
_INTENTS_DIRECTORY = "intents"
_RECEIPTS_DIRECTORY = "receipts"
_BACKUPS_DIRECTORY = "backups"
_LOCKS_DIRECTORY = "locks"
_GLOBAL_TRANSACTION_LEAF = "transaction"

_ABSENT_READBACK: Mapping[str, Any] = {"present": False, "content_digest": None}


class LocalEffectHarnessError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LocalEffectHarnessUnknownError(LocalEffectHarnessError):
    """An effect whose applied state is unknown. Only reconcile may resolve it."""

    def __init__(
        self,
        message: str,
        *,
        idempotency_key: str,
        next_action: str = "manual_reconcile",
    ):
        super().__init__("effect_result_unknown", message)
        self.idempotency_key = idempotency_key
        self.next_action = next_action


def _fail(code: str, message: str) -> NoReturn:
    raise LocalEffectHarnessError(code, message)


# --- canonical encoding --------------------------------------------------------


def _canonical_bytes(value: object) -> bytes:
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
            "local_effect_payload_not_canonical",
            f"payload is not canonical JSON: {exc}",
        )


def _digest_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _canonical_digest(value: object) -> str:
    return _digest_bytes(_canonical_bytes(value))


def canonical_payload_digest_v1(payload: object) -> str:
    """Digest one canonical JSON payload exactly as the harness will store it."""
    return _canonical_digest(payload)


# --- gates ---------------------------------------------------------------------


def _reject_if_disabled() -> None:
    if os.environ.get(_ENABLED_ENV, "").strip().lower() not in _ENABLED_ON_VALUES:
        _fail(
            "local_effect_harness_disabled",
            f"local effect harness is off unless {_ENABLED_ENV} is explicitly enabled",
        )
    if (
        os.environ.get(_KILL_SWITCH_ENV, "").strip().lower()
        not in _KILL_SWITCH_OFF_VALUES
    ):
        _fail(
            "local_effect_harness_kill_switch_engaged",
            "local effect harness is disabled by kill switch",
        )


# --- contracts -----------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentalLocalEffectReceiptV1:
    """Standalone harness receipt. Not the canonical production EffectReceiptV1."""

    schema_version: str
    adapter_id: str
    operation: str
    target_ref: str
    target_digest: str
    payload_digest: str
    authorization_binding_digest: str
    idempotency_key: str
    request_binding_digest: str
    candidate_digest: str | None
    pre_readback: Mapping[str, Any]
    post_readback: Mapping[str, Any]
    backup_ref: str
    result_classification: str
    applied: bool
    next_action: str
    resolution: str
    receipt_digest: str
    compensated_receipt_digest: str | None = None


@dataclass(frozen=True)
class ExperimentalLocalEffectApplyResultV1:
    receipt: ExperimentalLocalEffectReceiptV1
    replayed: bool


# --- paths ---------------------------------------------------------------------


def _name_for(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _harness_directory(private_root: Path | str, kind: str) -> Path:
    return Path(private_root) / HARNESS_NAMESPACE / kind


def target_path_v1(*, private_root: Path | str, target_ref: str) -> Path:
    """Content-addressed on-disk location of one logical target reference."""
    return (
        _harness_directory(private_root, _TARGETS_DIRECTORY)
        / f"{_name_for(target_ref)}.json"
    )


def intent_path_v1(*, private_root: Path | str, idempotency_key: str) -> Path:
    return (
        _harness_directory(private_root, _INTENTS_DIRECTORY)
        / f"{_name_for(idempotency_key)}.json"
    )


def compensation_intent_path_v1(
    *, private_root: Path | str, idempotency_key: str
) -> Path:
    """Compensation journals live beside apply journals but never collide."""
    return (
        _harness_directory(private_root, _INTENTS_DIRECTORY)
        / f"{_name_for(idempotency_key)}.compensation.json"
    )


def global_transaction_lock_path_v1(*, private_root: Path | str) -> Path:
    """Fixed root-scoped anchor every harness transaction serializes on."""
    return _harness_directory(private_root, _LOCKS_DIRECTORY) / _GLOBAL_TRANSACTION_LEAF


def receipt_path_v1(
    *, private_root: Path | str, idempotency_key: str, compensation: bool = False
) -> Path:
    suffix = ".compensation.json" if compensation else ".json"
    return (
        _harness_directory(private_root, _RECEIPTS_DIRECTORY)
        / f"{_name_for(idempotency_key)}{suffix}"
    )


def backup_path_v1(*, private_root: Path | str, backup_ref: str) -> Path:
    return (
        _harness_directory(private_root, _BACKUPS_DIRECTORY)
        / f"{_hex_of(backup_ref)}.json"
    )


def _hex_of(digest: str) -> str:
    body = digest[len("sha256:") :] if digest.startswith("sha256:") else ""
    if len(body) != 64 or any(
        character not in "0123456789abcdef" for character in body
    ):
        _fail(
            "local_effect_digest_invalid",
            f"expected a sha256:<64 hex> digest: {digest!r}",
        )
    return body


def _validated_private_root(private_root: object) -> Path:
    if not isinstance(private_root, (str, Path)):
        _fail("local_effect_private_root_invalid", "private_root must be a path")
    pinned = runtime_state.pin_runtime_path(Path(private_root))
    try:
        info = os.lstat(pinned)
    except OSError as exc:
        _fail(
            "local_effect_private_root_invalid",
            f"private_root must be an existing private directory: {exc}",
        )
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        _fail(
            "local_effect_private_root_invalid",
            "private_root must be a real directory, not a symlink",
        )
    if stat.S_IMODE(info.st_mode) & 0o077:
        _fail(
            "local_effect_private_root_invalid",
            "private_root must be owner-only (no group or other access)",
        )
    return pinned


# --- candidates and authorizations ---------------------------------------------


def build_local_json_effect_candidate_v1(
    *, target_ref: str, payload: object, idempotency_key: str
) -> EffectAdmissionCandidateV1:
    """Build the only candidate shape this harness accepts."""
    if not isinstance(target_ref, str) or not target_ref.strip():
        _fail("local_effect_candidate_invalid", "target_ref must be a non-empty string")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        _fail(
            "local_effect_candidate_invalid",
            "idempotency_key must be a non-empty string",
        )
    return EffectAdmissionCandidateV1(
        schema_version=_CANDIDATE_SCHEMA_VERSION,
        adapter_id=HARNESS_ADAPTER_ID,
        operation=HARNESS_APPLY_OPERATION,
        target={"target_kind": HARNESS_TARGET_KIND, "target_ref": target_ref},
        payload_digest=canonical_payload_digest_v1(payload),
        authority_request={
            "request_id": f"request:{HARNESS_ADAPTER_ID}:{idempotency_key}",
            "side_effect_tier": HARNESS_SIDE_EFFECT_TIER,
            "requested_operations": [HARNESS_APPLY_OPERATION],
            "requested_target_refs": [target_ref],
        },
        idempotency_key=idempotency_key,
        compensation_ref=f"compensation:{HARNESS_ADAPTER_ID}.{HARNESS_COMPENSATION_OPERATION}",
        reconcile_ref=f"reconcile:{HARNESS_ADAPTER_ID}.readback",
    )


def _validated_candidate(candidate: object) -> dict[str, Any]:
    if not isinstance(candidate, EffectAdmissionCandidateV1):
        _fail(
            "local_effect_candidate_invalid",
            "candidate must be an EffectAdmissionCandidateV1",
        )
    candidate_digest = derive_effect_admission_candidate_digest_v1(candidate)
    if candidate.adapter_id != HARNESS_ADAPTER_ID:
        _fail(
            "local_effect_adapter_not_supported",
            f"this harness only applies {HARNESS_ADAPTER_ID}",
        )
    if candidate.operation != HARNESS_APPLY_OPERATION:
        _fail(
            "local_effect_operation_not_supported",
            f"this harness only applies {HARNESS_APPLY_OPERATION}",
        )
    if candidate.authority_request["side_effect_tier"] != HARNESS_SIDE_EFFECT_TIER:
        _fail(
            "local_effect_side_effect_tier_not_supported",
            f"this harness only applies the {HARNESS_SIDE_EFFECT_TIER} tier",
        )
    if candidate.target["target_kind"] != HARNESS_TARGET_KIND:
        _fail(
            "local_effect_target_kind_not_supported",
            f"this harness only applies {HARNESS_TARGET_KIND} targets",
        )
    request = candidate.authority_request
    if candidate.operation not in request["requested_operations"]:
        _fail(
            "local_effect_operation_not_supported",
            "operation is outside the candidate authority request",
        )
    if candidate.target["target_ref"] not in request["requested_target_refs"]:
        _fail(
            "local_effect_candidate_invalid",
            "target_ref is outside the candidate authority request",
        )
    return {
        "candidate_digest": candidate_digest,
        "target": dict(candidate.target),
        "target_ref": candidate.target["target_ref"],
        "target_digest": _canonical_digest(dict(candidate.target)),
        "payload_digest": candidate.payload_digest,
        "idempotency_key": candidate.idempotency_key,
    }


def issue_local_json_effect_authorization_v1(
    *, candidate: EffectAdmissionCandidateV1, ttl_seconds: float = 30.0
) -> ExperimentalHarnessExactAuthorizationV1:
    """Issue the exact one-shot authorization this harness will consume."""
    _reject_if_disabled()
    body = _validated_candidate(candidate)
    return issue_experimental_harness_exact_authorization_v1(
        adapter_id=HARNESS_ADAPTER_ID,
        operation=HARNESS_APPLY_OPERATION,
        target=body["target"],
        payload_digest=body["payload_digest"],
        side_effect_tier=HARNESS_SIDE_EFFECT_TIER,
        ttl_seconds=ttl_seconds,
    )


def issue_local_json_effect_compensation_authorization_v1(
    *, receipt: ExperimentalLocalEffectReceiptV1, ttl_seconds: float = 30.0
) -> ExperimentalHarnessExactAuthorizationV1:
    """Issue a fresh exact authorization bound to one applied receipt."""
    _reject_if_disabled()
    if not isinstance(receipt, ExperimentalLocalEffectReceiptV1):
        _fail(
            "local_effect_receipt_invalid",
            "receipt must be an ExperimentalLocalEffectReceiptV1",
        )
    return issue_experimental_harness_exact_authorization_v1(
        adapter_id=HARNESS_ADAPTER_ID,
        operation=HARNESS_COMPENSATION_OPERATION,
        target={"target_kind": HARNESS_TARGET_KIND, "target_ref": receipt.target_ref},
        payload_digest=receipt.receipt_digest,
        side_effect_tier=HARNESS_SIDE_EFFECT_TIER,
        ttl_seconds=ttl_seconds,
    )


# --- filesystem primitives -----------------------------------------------------


def _write_atomic(path: Path, data: bytes, *, private_root: Path) -> None:
    runtime_state.write_private_file_atomic(path, data, private_root=private_root)


def _read_optional_bytes(path: Path, *, private_root: Path) -> bytes | None:
    try:
        return runtime_state.read_existing_private_file_bytes(
            path, private_root=private_root
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail(
            "local_effect_private_file_unsafe",
            f"refusing an unsafe or unreadable private file: {exc}",
        )


def _unlink_private_file(path: Path, *, private_root: Path) -> None:
    """Remove one private file through an anchored no-follow parent descriptor."""
    try:
        runtime_state.unlink_private_file_anchored(path, private_root=private_root)
    except (OSError, ValueError) as exc:
        _fail(
            "local_effect_private_file_unsafe",
            f"refusing to remove an unsafe file: {exc}",
        )


def _readback(data: bytes | None) -> dict[str, Any]:
    if data is None:
        return dict(_ABSENT_READBACK)
    return {"present": True, "content_digest": _digest_bytes(data)}


def _validated_crash_phase(crash_phase: object) -> str | None:
    if crash_phase is None:
        return None
    if not isinstance(crash_phase, str) or crash_phase not in CRASH_PHASES:
        _fail(
            "local_effect_crash_phase_invalid",
            f"crash_phase must be one of {sorted(CRASH_PHASES)}",
        )
    return crash_phase


def _validated_compensation_crash_phase(crash_phase: object) -> str | None:
    if crash_phase is None:
        return None
    if not isinstance(crash_phase, str) or crash_phase not in COMPENSATION_CRASH_PHASES:
        _fail(
            "local_effect_compensation_crash_phase_invalid",
            "compensation_crash_phase must be one of "
            f"{sorted(COMPENSATION_CRASH_PHASES)}",
        )
    return crash_phase


# --- documents -----------------------------------------------------------------


def _self_digested_document(body: Mapping[str, Any], *, key: str) -> bytes:
    return _canonical_bytes({**body, key: _canonical_digest(body)})


def _verified_document(
    data: bytes | None, *, key: str, code: str
) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(code, f"unreadable harness document: {exc}")
    if not isinstance(document, dict) or key not in document:
        _fail(code, "harness document is missing its self digest")
    body = {name: value for name, value in document.items() if name != key}
    if _canonical_digest(body) != document[key]:
        _fail(code, "harness document does not match its own digest")
    return document


def _receipt_from_document(
    document: Mapping[str, Any],
) -> ExperimentalLocalEffectReceiptV1:
    try:
        return ExperimentalLocalEffectReceiptV1(**document)
    except TypeError as exc:
        _fail("local_effect_receipt_tampered", f"receipt document is malformed: {exc}")


def _receipt_document(receipt: ExperimentalLocalEffectReceiptV1) -> bytes:
    body = {
        name: value
        for name, value in asdict(receipt).items()
        if name != "receipt_digest"
    }
    return _canonical_bytes({**body, "receipt_digest": receipt.receipt_digest})


def _sealed_receipt(body: Mapping[str, Any]) -> ExperimentalLocalEffectReceiptV1:
    return ExperimentalLocalEffectReceiptV1(
        **body, receipt_digest=_canonical_digest(body)
    )


def _read_receipt(
    private_root: Path, idempotency_key: str, *, compensation: bool = False
) -> ExperimentalLocalEffectReceiptV1 | None:
    path = receipt_path_v1(
        private_root=private_root,
        idempotency_key=idempotency_key,
        compensation=compensation,
    )
    document = _verified_document(
        _read_optional_bytes(path, private_root=private_root),
        key="receipt_digest",
        code="local_effect_receipt_tampered",
    )
    return None if document is None else _receipt_from_document(document)


def _read_intent(private_root: Path, idempotency_key: str) -> dict[str, Any] | None:
    path = intent_path_v1(private_root=private_root, idempotency_key=idempotency_key)
    return _verified_document(
        _read_optional_bytes(path, private_root=private_root),
        key="document_digest",
        code="local_effect_journal_tampered",
    )


def _replayed_apply_receipt(
    private_root: Path, existing: ExperimentalLocalEffectReceiptV1
) -> ExperimentalLocalEffectReceiptV1:
    """Return an existing terminal apply receipt only if it still describes disk.

    A terminal receipt claims a target state, whether it classified ``applied``
    or ``not_applied``. Returning it after the target drifted would report a
    state the disk contradicts, so the readback is re-checked first and a
    mismatch fails closed without mutating anything or spending an
    authorization. Apply replay and reconcile both route through this one check
    so the two paths cannot answer the same question differently.
    """
    target_path = target_path_v1(
        private_root=private_root, target_ref=existing.target_ref
    )
    current = _readback(_read_optional_bytes(target_path, private_root=private_root))
    if current != dict(existing.post_readback):
        _fail(
            "local_effect_replay_readback_mismatch",
            "target no longer matches the terminal receipt readback",
        )
    return existing


def _read_compensation_intent(
    private_root: Path, idempotency_key: str
) -> dict[str, Any] | None:
    path = compensation_intent_path_v1(
        private_root=private_root, idempotency_key=idempotency_key
    )
    return _verified_document(
        _read_optional_bytes(path, private_root=private_root),
        key="document_digest",
        code="local_effect_journal_tampered",
    )


def _sealed_compensation_receipt(
    intent: Mapping[str, Any],
    *,
    post_readback: Mapping[str, Any],
    classification: str,
    applied: bool,
    next_action: str,
    resolution: str,
) -> ExperimentalLocalEffectReceiptV1:
    """Seal one compensation receipt from its own compensation intent body.

    Every binding field describes the compensation, never the apply it rolls
    back. ``payload_digest`` is the applied receipt digest the compensation
    authorization was issued over, so the receipt's payload binding is exactly
    what was authorized; ``request_binding_digest`` is the compensation's own;
    and ``candidate_digest`` is ``None`` because a compensation has no
    candidate of its own. The restore itself is bound by three other fields:
    ``compensated_receipt_digest`` names the applied effect being undone,
    ``backup_ref`` content-addresses the restore payload, and ``post_readback``
    records the state that restore actually produced.

    Direct compensation and compensation reconcile both seal from this one
    body, so the two receipts differ only in ``resolution``.
    """
    return _sealed_receipt({
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "adapter_id": HARNESS_ADAPTER_ID,
        "operation": HARNESS_COMPENSATION_OPERATION,
        "target_ref": intent["target_ref"],
        "target_digest": intent["target_digest"],
        "payload_digest": intent["payload_digest"],
        "authorization_binding_digest": intent["authorization_binding_digest"],
        "idempotency_key": intent["idempotency_key"],
        "request_binding_digest": intent["request_binding_digest"],
        "candidate_digest": intent["candidate_digest"],
        "pre_readback": dict(intent["apply_post_readback"]),
        "post_readback": dict(post_readback),
        "backup_ref": intent["backup_ref"],
        "result_classification": classification,
        "applied": applied,
        "next_action": next_action,
        "resolution": resolution,
        "compensated_receipt_digest": intent["compensated_receipt_digest"],
    })


def _replayed_compensation_receipt(
    private_root: Path,
    existing: ExperimentalLocalEffectReceiptV1,
    *,
    compensated_receipt_digest: str | None,
) -> ExperimentalLocalEffectReceiptV1:
    """Return an existing compensation receipt only if it still describes disk.

    A compensation receipt claims a restored or removed target. Replaying it
    after the target drifted would report a rollback that no longer holds, so
    the readback is re-checked first and a mismatch fails closed without
    mutating anything or spending the caller's authorization.
    """
    if (
        compensated_receipt_digest is not None
        and existing.compensated_receipt_digest != compensated_receipt_digest
    ):
        _fail(
            "local_effect_compensation_receipt_conflict",
            "an existing compensation receipt is bound to a different applied receipt",
        )
    target_path = target_path_v1(
        private_root=private_root, target_ref=existing.target_ref
    )
    current = _readback(_read_optional_bytes(target_path, private_root=private_root))
    if current != dict(existing.post_readback):
        _fail(
            "local_effect_compensation_replay_readback_mismatch",
            "target no longer matches the compensation receipt readback",
        )
    return existing


# --- root-global then per-idempotency serialization -----------------------------

_KEY_LOCKS_GUARD = Lock()
_KEY_LOCKS: dict[str, Lock] = {}
_ROOT_LOCKS_GUARD = Lock()
_ROOT_LOCKS: dict[str, Lock] = {}


def _key_lock(idempotency_key: str) -> Lock:
    with _KEY_LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(_name_for(idempotency_key), Lock())


def _root_lock(private_root: Path) -> Lock:
    with _ROOT_LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(_name_for(str(private_root)), Lock())


@dataclass
class _Transaction:
    """Serialize one private root globally, then one idempotency key.

    Two different idempotency keys may address the same target, so a per-key
    lock alone would let their preimage read, apply and receipt interleave and
    produce receipts no serial order could explain. Every entry point takes the
    root-scoped global lock first and the per-key lock second, in that fixed
    order, so throughput is traded for a total order per private root and the
    ordering cannot deadlock. Both levels are held as a thread lock and an
    anchored no-follow file lock, so other processes serialize too. Different
    private roots use different anchors and never block each other.
    """

    private_root: Path
    idempotency_key: str
    _entered: list = field(default_factory=list)

    def _acquire(self, thread_lock: Lock, anchor: Path) -> None:
        thread_lock.acquire()
        self._entered.append(thread_lock.release)
        file_lock = runtime_state.exclusive_ledger_lock(
            anchor, private_root=self.private_root
        )
        file_lock.__enter__()
        self._entered.append(lambda: file_lock.__exit__(None, None, None))

    def __enter__(self) -> "_Transaction":
        try:
            self._acquire(
                _root_lock(self.private_root),
                global_transaction_lock_path_v1(private_root=self.private_root),
            )
            self._acquire(
                _key_lock(self.idempotency_key),
                receipt_path_v1(
                    private_root=self.private_root, idempotency_key=self.idempotency_key
                ),
            )
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exception: object) -> None:
        for release in reversed(self._entered):
            release()
        self._entered.clear()


# --- apply ---------------------------------------------------------------------


def apply_local_json_effect_v1(
    *,
    candidate: EffectAdmissionCandidateV1,
    authorization: ExperimentalHarnessExactAuthorizationV1,
    payload: object,
    private_root: Path | str,
    crash_phase: str | None = None,
) -> ExperimentalLocalEffectApplyResultV1:
    """Apply one exact local JSON effect, or replay its existing receipt."""
    _reject_if_disabled()
    body = _validated_candidate(candidate)
    payload_bytes = _canonical_bytes(payload)
    if _digest_bytes(payload_bytes) != body["payload_digest"]:
        _fail(
            "local_effect_payload_digest_mismatch",
            "payload does not match the candidate payload digest exactly",
        )
    root = _validated_private_root(private_root)
    phase = _validated_crash_phase(crash_phase)

    # Validation only: a forged, mismatched, reused or out-of-process
    # authorization fails closed before any receipt is read or returned, and a
    # rejection that changes no state never spends the one-shot capability.
    authorization_binding_digest = _validate_local_harness_authorization_v1(
        authorization,
        operation=HARNESS_APPLY_OPERATION,
        target=body["target"],
        payload_digest=body["payload_digest"],
    )

    idempotency_key = body["idempotency_key"]
    request_binding_digest = _canonical_digest({
        "adapter_id": HARNESS_ADAPTER_ID,
        "operation": HARNESS_APPLY_OPERATION,
        "candidate_digest": body["candidate_digest"],
        "target_digest": body["target_digest"],
        "payload_digest": body["payload_digest"],
        "idempotency_key": idempotency_key,
        "authorization_binding_digest": authorization_binding_digest,
    })
    target_path = target_path_v1(private_root=root, target_ref=body["target_ref"])
    desired_digest = _digest_bytes(payload_bytes)

    with _Transaction(root, idempotency_key):
        existing = _read_receipt(root, idempotency_key)
        if existing is not None:
            if existing.request_binding_digest != request_binding_digest:
                _fail(
                    "local_effect_idempotency_binding_conflict",
                    "idempotency key is already bound to a different exact request",
                )
            return ExperimentalLocalEffectApplyResultV1(
                receipt=_replayed_apply_receipt(root, existing), replayed=True
            )

        if _read_intent(root, idempotency_key) is not None:
            _fail(
                "local_effect_unresolved_intent",
                "an unresolved intent exists for this key; reconcile before retrying",
            )

        preimage = _read_optional_bytes(target_path, private_root=root)
        pre_readback = _readback(preimage)
        backup_body = {
            "schema_version": _BACKUP_SCHEMA_VERSION,
            "target_ref": body["target_ref"],
            "target_digest": body["target_digest"],
            "idempotency_key": idempotency_key,
            "present": preimage is not None,
            "content_b64": None
            if preimage is None
            else base64.b64encode(preimage).decode("ascii"),
            "content_digest": pre_readback["content_digest"],
        }
        backup_ref = _canonical_digest(backup_body)

        # Every state-only conflict is settled above without spending the
        # authorization. From here the transaction mutates, so the harness gate
        # is rechecked freshly and the one-shot authorization is consumed
        # before the first write.
        _reject_if_disabled()
        if (
            _commit_local_harness_authorization_v1(
                authorization,
                operation=HARNESS_APPLY_OPERATION,
                target=body["target"],
                payload_digest=body["payload_digest"],
            )
            != authorization_binding_digest
        ):
            _fail(
                "local_effect_authorization_mismatch",
                "authorization binding changed between validation and commit",
            )

        _reject_if_disabled()
        _write_atomic(
            backup_path_v1(private_root=root, backup_ref=backup_ref),
            _canonical_bytes(backup_body),
            private_root=root,
        )

        intent_body = {
            "schema_version": _INTENT_SCHEMA_VERSION,
            "adapter_id": HARNESS_ADAPTER_ID,
            "operation": HARNESS_APPLY_OPERATION,
            "target_ref": body["target_ref"],
            "target_digest": body["target_digest"],
            "payload_digest": body["payload_digest"],
            "authorization_binding_digest": authorization_binding_digest,
            "idempotency_key": idempotency_key,
            "request_binding_digest": request_binding_digest,
            "candidate_digest": body["candidate_digest"],
            "pre_readback": pre_readback,
            "backup_ref": backup_ref,
            "desired_content_digest": desired_digest,
        }
        _reject_if_disabled()
        _write_atomic(
            intent_path_v1(private_root=root, idempotency_key=idempotency_key),
            _self_digested_document(intent_body, key="document_digest"),
            private_root=root,
        )
        if phase == "after_intent_before_apply":
            _crash(idempotency_key, phase)

        _reject_if_disabled()
        _write_atomic(target_path, payload_bytes, private_root=root)
        if phase == "after_apply_before_receipt":
            _crash(idempotency_key, phase)

        post_readback = _readback(_read_optional_bytes(target_path, private_root=root))
        if post_readback["content_digest"] != desired_digest:
            _crash(idempotency_key, "post_readback_mismatch")

        receipt = _sealed_receipt({
            "schema_version": _RECEIPT_SCHEMA_VERSION,
            "adapter_id": HARNESS_ADAPTER_ID,
            "operation": HARNESS_APPLY_OPERATION,
            "target_ref": body["target_ref"],
            "target_digest": body["target_digest"],
            "payload_digest": body["payload_digest"],
            "authorization_binding_digest": authorization_binding_digest,
            "idempotency_key": idempotency_key,
            "request_binding_digest": request_binding_digest,
            "candidate_digest": body["candidate_digest"],
            "pre_readback": pre_readback,
            "post_readback": post_readback,
            "backup_ref": backup_ref,
            "result_classification": "applied",
            "applied": True,
            "next_action": "none",
            "resolution": "apply",
            "compensated_receipt_digest": None,
        })
        _reject_if_disabled()
        _write_atomic(
            receipt_path_v1(private_root=root, idempotency_key=idempotency_key),
            _receipt_document(receipt),
            private_root=root,
        )
        return ExperimentalLocalEffectApplyResultV1(receipt=receipt, replayed=False)


def _crash(idempotency_key: str, phase: str) -> NoReturn:
    raise LocalEffectHarnessUnknownError(
        f"effect result is unknown at {phase}; reconcile is the only resolution",
        idempotency_key=idempotency_key,
    )


def _compensation_crash(idempotency_key: str, phase: str) -> NoReturn:
    raise LocalEffectHarnessUnknownError(
        f"compensation result is unknown at {phase}; "
        "reconcile_local_json_compensation_v1 is the only resolution",
        idempotency_key=idempotency_key,
        next_action="manual_compensation_reconcile",
    )


# --- read-only replay ----------------------------------------------------------


def read_local_json_effect_receipt_v1(
    *, idempotency_key: str, private_root: Path | str
) -> ExperimentalLocalEffectReceiptV1:
    """Read one terminal receipt without authorization and without mutation."""
    _reject_if_disabled()
    root = _validated_private_root(private_root)
    receipt = _read_receipt(root, idempotency_key)
    if receipt is None:
        _fail("local_effect_receipt_missing", "no terminal receipt exists for this key")
    return receipt


# --- reconcile -----------------------------------------------------------------


def reconcile_local_json_effect_v1(
    *, idempotency_key: str, private_root: Path | str
) -> ExperimentalLocalEffectReceiptV1:
    """Classify one journalled effect from readback only. Never applies anything."""
    _reject_if_disabled()
    root = _validated_private_root(private_root)

    # An existing terminal receipt is not answered here: it still claims a
    # target state, and only a locked readback may confirm that claim.
    if (
        _read_receipt(root, idempotency_key) is None
        and _read_intent(root, idempotency_key) is None
    ):
        _fail("local_effect_intent_missing", "no intent journal exists for this key")

    with _Transaction(root, idempotency_key):
        existing = _read_receipt(root, idempotency_key)
        if existing is not None:
            return _replayed_apply_receipt(root, existing)
        intent = _read_intent(root, idempotency_key)
        if intent is None:
            _fail(
                "local_effect_intent_missing", "no intent journal exists for this key"
            )

        target_path = target_path_v1(private_root=root, target_ref=intent["target_ref"])
        post_readback = _readback(_read_optional_bytes(target_path, private_root=root))
        if post_readback["content_digest"] == intent["desired_content_digest"]:
            classification, applied, next_action = "applied", True, "none"
        elif post_readback == dict(intent["pre_readback"]):
            classification, applied, next_action = "not_applied", False, "none"
        else:
            classification, applied, next_action = "unknown", False, "manual_reconcile"

        receipt = _sealed_receipt({
            "schema_version": _RECEIPT_SCHEMA_VERSION,
            "adapter_id": intent["adapter_id"],
            "operation": intent["operation"],
            "target_ref": intent["target_ref"],
            "target_digest": intent["target_digest"],
            "payload_digest": intent["payload_digest"],
            "authorization_binding_digest": intent["authorization_binding_digest"],
            "idempotency_key": intent["idempotency_key"],
            "request_binding_digest": intent["request_binding_digest"],
            "candidate_digest": intent["candidate_digest"],
            "pre_readback": intent["pre_readback"],
            "post_readback": post_readback,
            "backup_ref": intent["backup_ref"],
            "result_classification": classification,
            "applied": applied,
            "next_action": next_action,
            "resolution": "reconcile",
            "compensated_receipt_digest": None,
        })
        if classification == "unknown":
            # An unknown effect has no terminal state to persist; it stays open
            # for manual reconcile and must never be resolved by re-applying.
            return receipt

        # Classification is terminal, so persisting it mutates. A kill switch
        # engaged while this caller waited for the lock still closes the write.
        _reject_if_disabled()
        _write_atomic(
            receipt_path_v1(private_root=root, idempotency_key=idempotency_key),
            _receipt_document(receipt),
            private_root=root,
        )
        return receipt


# --- compensation --------------------------------------------------------------


def _compensation_request_binding_digest(
    *,
    target_digest: str,
    payload_digest: str,
    idempotency_key: str,
    authorization_binding_digest: str,
) -> str:
    """Bind one compensation request exactly, never reusing the apply binding."""
    return _canonical_digest({
        "adapter_id": HARNESS_ADAPTER_ID,
        "operation": HARNESS_COMPENSATION_OPERATION,
        "target_digest": target_digest,
        "payload_digest": payload_digest,
        "idempotency_key": idempotency_key,
        "authorization_binding_digest": authorization_binding_digest,
    })


def compensate_local_json_effect_v1(
    *,
    receipt: ExperimentalLocalEffectReceiptV1,
    authorization: ExperimentalHarnessExactAuthorizationV1,
    private_root: Path | str,
    compensation_crash_phase: str | None = None,
) -> ExperimentalLocalEffectReceiptV1:
    """Restore one applied effect's preimage under a fresh exact authorization."""
    _reject_if_disabled()
    if not isinstance(receipt, ExperimentalLocalEffectReceiptV1):
        _fail(
            "local_effect_receipt_invalid",
            "receipt must be an ExperimentalLocalEffectReceiptV1",
        )
    root = _validated_private_root(private_root)
    phase = _validated_compensation_crash_phase(compensation_crash_phase)

    persisted = _read_receipt(root, receipt.idempotency_key)
    if persisted is None or persisted != receipt:
        _fail(
            "local_effect_receipt_not_persisted",
            "compensation requires the exact persisted terminal receipt",
        )
    if persisted.operation != HARNESS_APPLY_OPERATION or not (
        persisted.applied and persisted.result_classification == "applied"
    ):
        _fail(
            "local_effect_receipt_not_applied",
            "only an applied receipt can be compensated; unknown must reconcile first",
        )

    compensation_target = {
        "target_kind": HARNESS_TARGET_KIND,
        "target_ref": persisted.target_ref,
    }
    authorization_binding_digest = _validate_local_harness_authorization_v1(
        authorization,
        operation=HARNESS_COMPENSATION_OPERATION,
        target=compensation_target,
        payload_digest=persisted.receipt_digest,
    )
    request_binding_digest = _compensation_request_binding_digest(
        target_digest=persisted.target_digest,
        payload_digest=persisted.receipt_digest,
        idempotency_key=persisted.idempotency_key,
        authorization_binding_digest=authorization_binding_digest,
    )

    with _Transaction(root, persisted.idempotency_key):
        existing = _read_receipt(root, persisted.idempotency_key, compensation=True)
        if existing is not None:
            return _replayed_compensation_receipt(
                root,
                existing,
                compensated_receipt_digest=persisted.receipt_digest,
            )
        if _read_compensation_intent(root, persisted.idempotency_key) is not None:
            _fail(
                "local_effect_compensation_unresolved_intent",
                "an unresolved compensation intent exists for this key; "
                "reconcile_local_json_compensation_v1 is the only resolution and a "
                "compensation is never blindly retried",
            )

        backup_document = _read_backup(root, persisted.backup_ref)

        target_path = target_path_v1(private_root=root, target_ref=persisted.target_ref)
        current = _readback(_read_optional_bytes(target_path, private_root=root))
        if current != dict(persisted.post_readback):
            _fail(
                "local_effect_target_drifted",
                "target no longer matches the receipt post readback; reconcile first",
            )
        expected_restored_readback = (
            {"present": True, "content_digest": backup_document["content_digest"]}
            if backup_document["present"]
            else dict(_ABSENT_READBACK)
        )

        # Drift is settled above without spending the authorization. The
        # restore below mutates, so the harness gate is rechecked freshly and
        # the one-shot authorization is consumed before the first write.
        _reject_if_disabled()
        if (
            _commit_local_harness_authorization_v1(
                authorization,
                operation=HARNESS_COMPENSATION_OPERATION,
                target=compensation_target,
                payload_digest=persisted.receipt_digest,
            )
            != authorization_binding_digest
        ):
            _fail(
                "local_effect_authorization_mismatch",
                "authorization binding changed between validation and commit",
            )

        intent_body = {
            "schema_version": _COMPENSATION_INTENT_SCHEMA_VERSION,
            "adapter_id": HARNESS_ADAPTER_ID,
            "operation": HARNESS_COMPENSATION_OPERATION,
            "target_ref": persisted.target_ref,
            "target_digest": persisted.target_digest,
            "payload_digest": persisted.receipt_digest,
            "authorization_binding_digest": authorization_binding_digest,
            "idempotency_key": persisted.idempotency_key,
            "request_binding_digest": request_binding_digest,
            "candidate_digest": None,
            "backup_ref": persisted.backup_ref,
            "compensated_receipt_digest": persisted.receipt_digest,
            "apply_post_readback": dict(persisted.post_readback),
            "expected_restored_readback": expected_restored_readback,
        }
        _reject_if_disabled()
        _write_atomic(
            compensation_intent_path_v1(
                private_root=root, idempotency_key=persisted.idempotency_key
            ),
            _self_digested_document(intent_body, key="document_digest"),
            private_root=root,
        )
        if phase == "after_compensation_intent_before_restore":
            _compensation_crash(persisted.idempotency_key, phase)

        _reject_if_disabled()
        if backup_document["present"]:
            _write_atomic(
                target_path,
                base64.b64decode(backup_document["content_b64"]),
                private_root=root,
            )
        else:
            _unlink_private_file(target_path, private_root=root)
        if phase == "after_compensation_restore_before_receipt":
            _compensation_crash(persisted.idempotency_key, phase)

        post_readback = _readback(_read_optional_bytes(target_path, private_root=root))
        if post_readback != expected_restored_readback:
            _compensation_crash(
                persisted.idempotency_key, "compensation_post_readback_mismatch"
            )

        compensation = _sealed_compensation_receipt(
            intent_body,
            post_readback=post_readback,
            classification="applied",
            applied=True,
            next_action="none",
            resolution="compensation",
        )
        _reject_if_disabled()
        _write_atomic(
            receipt_path_v1(
                private_root=root,
                idempotency_key=persisted.idempotency_key,
                compensation=True,
            ),
            _receipt_document(compensation),
            private_root=root,
        )
        return compensation


def reconcile_local_json_compensation_v1(
    *, idempotency_key: str, private_root: Path | str
) -> ExperimentalLocalEffectReceiptV1:
    """Classify one journalled compensation from readback only.

    This never restores, removes or retries anything. ``applied`` means the
    target already matches the intent's expected restored state.
    ``not_applied`` is terminal with ``manual_compensation_required``: this
    harness does not re-run a compensation on its own, so an operator owns the
    next step. Any third state is ``unknown``, persists no terminal receipt and
    stays open for manual reconcile.
    """
    _reject_if_disabled()
    root = _validated_private_root(private_root)

    if (
        _read_compensation_intent(root, idempotency_key) is None
        and _read_receipt(root, idempotency_key, compensation=True) is None
    ):
        _fail(
            "local_effect_compensation_intent_missing",
            "no compensation intent journal exists for this key",
        )

    with _Transaction(root, idempotency_key):
        intent = _read_compensation_intent(root, idempotency_key)
        existing = _read_receipt(root, idempotency_key, compensation=True)
        if existing is not None:
            return _replayed_compensation_receipt(
                root,
                existing,
                compensated_receipt_digest=None
                if intent is None
                else intent["compensated_receipt_digest"],
            )
        if intent is None:
            _fail(
                "local_effect_compensation_intent_missing",
                "no compensation intent journal exists for this key",
            )

        target_path = target_path_v1(private_root=root, target_ref=intent["target_ref"])
        post_readback = _readback(_read_optional_bytes(target_path, private_root=root))
        if post_readback == dict(intent["expected_restored_readback"]):
            classification, applied, next_action = "applied", True, "none"
        elif post_readback == dict(intent["apply_post_readback"]):
            # Terminal, and deliberately not retried here: the compensation
            # never ran, and only an operator may decide to run it again.
            classification, applied, next_action = (
                "not_applied",
                False,
                "manual_compensation_required",
            )
        else:
            classification, applied, next_action = "unknown", False, "manual_reconcile"

        receipt = _sealed_compensation_receipt(
            intent,
            post_readback=post_readback,
            classification=classification,
            applied=applied,
            next_action=next_action,
            resolution="compensation_reconcile",
        )
        if classification == "unknown":
            # An unknown compensation has no terminal state to persist; it
            # stays open and must never be resolved by restoring again.
            return receipt

        # Classification is terminal, so persisting it mutates. A kill switch
        # engaged while this caller waited for the lock still closes the write.
        _reject_if_disabled()
        _write_atomic(
            receipt_path_v1(
                private_root=root,
                idempotency_key=idempotency_key,
                compensation=True,
            ),
            _receipt_document(receipt),
            private_root=root,
        )
        return receipt


def _read_backup(private_root: Path, backup_ref: str) -> dict[str, Any]:
    path = backup_path_v1(private_root=private_root, backup_ref=backup_ref)
    data = _read_optional_bytes(path, private_root=private_root)
    if data is None:
        _fail(
            "local_effect_backup_missing", "the referenced preimage backup is missing"
        )
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("local_effect_backup_tampered", f"unreadable preimage backup: {exc}")
    if not isinstance(document, dict) or _canonical_digest(document) != backup_ref:
        _fail(
            "local_effect_backup_tampered",
            "preimage backup does not match its content address",
        )
    return document
