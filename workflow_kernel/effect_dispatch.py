"""Authority/dispatch state machine scaffolding for HGWK F4 effects.

Uses injected adapter callables only. Does not connect taskpool, MissionStore,
gateway, network, scheduler, or real external targets.

Threat / capability boundary
----------------------------
Usable authority cannot be created from EffectCommand data, arbitrary
config/policy files presented by a caller, caller-created policy objects, or
any public bootstrap/mint API. Issuance and dispatch consume a plane-held
immutable canonical policy snapshot created only by the runtime-owned
composition root (private/internal).

Public surface proof target: command data + caller policy objects + public
calls are insufficient to bootstrap or mint. This is not a claim of security
against arbitrary hostile same-process Python that imports private names or
forges the internal seal object.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, NoReturn
from weakref import WeakValueDictionary

from workflow_kernel.effect_contracts import (
    EffectCommandV1,
    EffectObservationV1,
    EffectReceiptV1,
    canonical_digest_v1,
    effect_command_body_v1,
    effect_observation_to_dict,
    effect_receipt_to_dict,
    parse_effect_command_v1,
    parse_effect_observation_v1,
    parse_effect_receipt_v1,
    rederive_effect_command_v1,
    thaw_admitted_json_v1,
)
from workflow_kernel import workflow_effect_ledger as effect_ledger


class EffectDispatchError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class EffectDispatchUnknownError(EffectDispatchError):
    def __init__(self, message: str, *, effect_id: str, idempotency_key: str):
        super().__init__("effect_result_unknown", message)
        self.effect_id = effect_id
        self.idempotency_key = idempotency_key
        self.next_action = "manual_reconcile"


def _fail(code: str, message: str) -> NoReturn:
    raise EffectDispatchError(code, message)


# Internal seal: public construction without this exact object fails.
_PLANE_SEAL = object()


@dataclass
class _OneShotTicket:
    lock: Lock = field(default_factory=Lock)
    consumed: bool = False
    creator_pid: int = field(default_factory=os.getpid)
    token: str = field(default_factory=lambda: secrets.token_hex(16))


@dataclass
class _RuntimeKillSwitch:
    """Mutable process-local kill switch; allowlists remain immutable on the plane."""

    engaged: bool = False


_REGISTRY_LOCK = Lock()
_GRANT_TICKETS: dict[str, _OneShotTicket] = {}
# Identity registry: plane_id -> plane (weak). Lookup never uses dataclass equality.
_PLANE_BY_ID: WeakValueDictionary[str, "TrustedAuthorityPlaneV1"] = WeakValueDictionary()
_PLANE_IDENTITY: dict[int, str] = {}
_ISSUANCE_BINDINGS: dict[str, dict[str, Any]] = {}
# Exact issued grant object identity by issue token. Copies/replacements are never accepted.
_ISSUED_GRANTS: dict[str, "AuthorityGrantV1"] = {}


def _register_ticket(nonce: str, ticket: _OneShotTicket) -> None:
    with _REGISTRY_LOCK:
        _GRANT_TICKETS[nonce] = ticket


def _is_registered_ticket(nonce: str, ticket: _OneShotTicket) -> bool:
    with _REGISTRY_LOCK:
        return _GRANT_TICKETS.get(nonce) is ticket


def _revoke_ticket(nonce: str) -> None:
    with _REGISTRY_LOCK:
        _GRANT_TICKETS.pop(nonce, None)


def _register_plane(plane: "TrustedAuthorityPlaneV1") -> None:
    with _REGISTRY_LOCK:
        if plane.plane_id in _PLANE_BY_ID:
            _fail("authority_plane_id_collision", "plane_id already registered")
        _PLANE_BY_ID[plane.plane_id] = plane
        _PLANE_IDENTITY[id(plane)] = plane.plane_id


def _is_registered_plane(plane: "TrustedAuthorityPlaneV1") -> bool:
    with _REGISTRY_LOCK:
        registered = _PLANE_BY_ID.get(plane.plane_id)
        return registered is plane and _PLANE_IDENTITY.get(id(plane)) == plane.plane_id


def _lookup_plane(plane_id: str) -> "TrustedAuthorityPlaneV1":
    with _REGISTRY_LOCK:
        plane = _PLANE_BY_ID.get(plane_id)
    if plane is None:
        _fail("authority_plane_not_registered", "authority plane is not registered")
    return plane


def _record_issuance_binding(binding_id: str, record: Mapping[str, Any]) -> None:
    with _REGISTRY_LOCK:
        if binding_id in _ISSUANCE_BINDINGS:
            _fail("authority_issuance_replay", "issuance binding already exists")
        _ISSUANCE_BINDINGS[binding_id] = dict(record)


def _require_issuance_binding(grant: "AuthorityGrantV1") -> dict[str, Any]:
    """Require the originally issued grant object + complete immutable issuance record.

    dataclasses.replace / manual copies are rejected by object identity even before
    original expiry. Expiry is taken only from the issuance record, never from a
    caller-supplied replacement field.
    """
    with _REGISTRY_LOCK:
        record = _ISSUANCE_BINDINGS.get(grant.issuance_binding_id)
        issued = _ISSUED_GRANTS.get(getattr(grant, "_issue_token", ""))
    if record is None:
        _fail(
            "authority_issuance_binding_missing",
            "issuance_binding_id was not issued by this module",
        )
    if issued is None or issued is not grant:
        _fail(
            "authority_grant_not_issued_object",
            "validation requires the originally issued grant object identity",
        )
    expected = {
        "plane_id": grant._plane_id,
        "command_digest": grant.command_digest,
        "target_kind": grant.target_kind,
        "target_ref": grant.target_ref,
        "target_digest": grant.target_digest,
        "policy_digest": grant.policy_digest,
        "config_digest": grant.config_digest,
        "issuer_id": grant.issuer_id,
        "policy_revision": grant.policy_revision,
        "config_revision": grant.config_revision,
        "adapter_id": grant.adapter_id,
        "operation": grant.operation,
        "payload_digest": grant.payload_digest,
        "nonce": grant.nonce,
        "issue_token": grant._issue_token,
        "ticket_token": grant._ticket_token,
        "expires_at_monotonic": grant.expires_at_monotonic,
        "schema_version": grant.schema_version,
    }
    for key, value in expected.items():
        if record.get(key) != value:
            _fail(
                "authority_issuance_binding_mismatch",
                f"issuance record field {key} does not match grant",
            )
    if grant._ticket is not issued._ticket:
        _fail(
            "authority_grant_ticket_identity_mismatch",
            "grant ticket object identity does not match issuance",
        )
    if getattr(grant._ticket, "token", None) != record.get("ticket_token"):
        _fail(
            "authority_grant_ticket_identity_mismatch",
            "grant ticket token does not match issuance",
        )
    return record


@dataclass(frozen=True)
class AuthorityGrantV1:
    """Opaque, process-local, one-shot grant. Not a serializable bearer token."""

    schema_version: str
    nonce: str = field(repr=False, compare=False)
    issuer_id: str
    policy_revision: str
    config_revision: str
    adapter_id: str
    operation: str
    target_kind: str
    target_ref: str
    target_digest: str
    payload_digest: str
    command_digest: str
    policy_digest: str
    config_digest: str
    issuance_binding_id: str
    expires_at_monotonic: float
    _ticket: _OneShotTicket = field(repr=False, compare=False)
    _plane_id: str = field(repr=False, compare=False)
    _issue_token: str = field(repr=False, compare=False)
    _ticket_token: str = field(repr=False, compare=False)

    def __reduce__(self) -> NoReturn:
        _fail(
            "authority_grant_not_serializable",
            "authority grant cannot be serialized into bearer authority",
        )

    def __getstate__(self) -> NoReturn:
        _fail(
            "authority_grant_not_serializable",
            "authority grant cannot be serialized into bearer authority",
        )

    def __copy__(self) -> NoReturn:
        _fail(
            "authority_grant_not_serializable",
            "authority grant cannot be copied into a second capability",
        )

    def __deepcopy__(self, memo: dict) -> NoReturn:
        _fail(
            "authority_grant_not_serializable",
            "authority grant cannot be copied into a second capability",
        )


@dataclass
class EffectDispatchPolicyV1:
    """Canonical allowlist snapshot. Not a public mint capability by itself."""

    issuer_id: str
    policy_revision: str
    config_revision: str
    allowed_adapter_ids: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    # Exact (target_kind, target_ref) pairs. target_ref alone is never sufficient.
    allowed_targets: tuple[tuple[str, str], ...]
    kill_switch: bool = False

    def __post_init__(self) -> None:
        normalized: list[tuple[str, str]] = []
        for item in self.allowed_targets:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(part, str) and part.strip() for part in item)
            ):
                _fail(
                    "policy_invalid",
                    "allowed_targets must be exact (target_kind, target_ref) pairs",
                )
            normalized.append((item[0], item[1]))
        self.allowed_targets = tuple(normalized)


@dataclass(frozen=True, eq=False)
class TrustedAuthorityPlaneV1:
    """Process-local trusted authority provenance/capability.

    Non-user-constructible at the supported boundary: requires the internal seal.
    Identity-based (eq=False). Cryptographically unique plane_id.
    Holds an immutable canonical policy snapshot for issuance/dispatch.
    """

    plane_id: str = field(repr=False)
    issuer_id: str
    policy_revision: str
    config_revision: str
    policy_digest: str
    config_digest: str
    _seal: object = field(repr=False, compare=False)
    _policy: EffectDispatchPolicyV1 = field(repr=False, compare=False)
    _kill_switch: _RuntimeKillSwitch = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _PLANE_SEAL:
            _fail(
                "authority_plane_unsealed",
                "TrustedAuthorityPlaneV1 is not publicly constructible",
            )

    @property
    def kill_switch(self) -> bool:
        return bool(self._kill_switch.engaged)

    def engage_kill_switch(self) -> None:
        """Monotonic for plane lifetime. No public clear/reset exists."""
        self._kill_switch.engaged = True

    def canonical_policy(self) -> EffectDispatchPolicyV1:
        """Return a defensive copy of the immutable allowlist snapshot."""
        p = self._policy
        return EffectDispatchPolicyV1(
            issuer_id=p.issuer_id,
            policy_revision=p.policy_revision,
            config_revision=p.config_revision,
            allowed_adapter_ids=tuple(p.allowed_adapter_ids),
            allowed_operations=tuple(p.allowed_operations),
            allowed_targets=tuple(p.allowed_targets),
            kill_switch=self.kill_switch,
        )


@dataclass(frozen=True)
class EffectAdapterResultV1:
    classification: str
    adapter_result_digest: str | None
    readback: Mapping[str, Any]


@dataclass(frozen=True)
class EffectDispatchResultV1:
    effect_id: str
    result_classification: str
    fence: int | None
    receipt: EffectReceiptV1 | None
    observation: Mapping[str, Any] | None
    replayed: bool = False


PreflightFn = Callable[[EffectCommandV1], Mapping[str, Any]]
AdapterFn = Callable[[EffectCommandV1, int], EffectAdapterResultV1]


def _policy_digest(policy: EffectDispatchPolicyV1) -> str:
    return canonical_digest_v1(
        {
            "issuer_id": policy.issuer_id,
            "policy_revision": policy.policy_revision,
            "config_revision": policy.config_revision,
            "allowed_adapter_ids": list(policy.allowed_adapter_ids),
            "allowed_operations": list(policy.allowed_operations),
            "allowed_targets": [list(pair) for pair in policy.allowed_targets],
            # kill_switch is runtime-mutable and not part of canonical policy digest
        }
    )


def _policy_from_canonical_document(document: Mapping[str, Any]) -> EffectDispatchPolicyV1:
    if not isinstance(document, Mapping):
        _fail("canonical_config_invalid", "canonical config must be an object")
    required = {
        "issuer_id",
        "policy_revision",
        "config_revision",
        "allowed_adapter_ids",
        "allowed_operations",
        "allowed_targets",
    }
    missing = sorted(required - set(document))
    if missing:
        _fail("canonical_config_invalid", f"canonical config missing keys: {missing}")
    extra = sorted(set(document) - required - {"schema_version", "kill_switch"})
    if extra:
        _fail("canonical_config_invalid", f"canonical config has unknown keys: {extra}")
    adapters = document["allowed_adapter_ids"]
    operations = document["allowed_operations"]
    targets = document["allowed_targets"]
    if not isinstance(adapters, list) or not adapters:
        _fail("canonical_config_invalid", "allowed_adapter_ids must be a non-empty list")
    if not isinstance(operations, list) or not operations:
        _fail("canonical_config_invalid", "allowed_operations must be a non-empty list")
    if not isinstance(targets, list) or not targets:
        _fail("canonical_config_invalid", "allowed_targets must be a non-empty list")
    pairs: list[tuple[str, str]] = []
    for item in targets:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            _fail("canonical_config_invalid", "allowed_targets entries must be [kind, ref]")
        pairs.append((str(item[0]), str(item[1])))
    return EffectDispatchPolicyV1(
        issuer_id=str(document["issuer_id"]),
        policy_revision=str(document["policy_revision"]),
        config_revision=str(document["config_revision"]),
        allowed_adapter_ids=tuple(str(x) for x in adapters),
        allowed_operations=tuple(str(x) for x in operations),
        allowed_targets=tuple(pairs),
        kill_switch=bool(document.get("kill_switch", False)),
    )


def _compose_trusted_authority_plane_from_canonical_config_v1(
    *,
    config_path: Path | str,
    _seal: object = None,
) -> TrustedAuthorityPlaneV1:
    """Runtime-owned composition root. Not part of the public/untrusted API."""
    if _seal is not _PLANE_SEAL:
        _fail(
            "authority_composition_root_private",
            "composition root is private to the trusted runtime",
        )
    path = Path(config_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail("canonical_config_unreadable", f"cannot read canonical config: {exc}")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("canonical_config_invalid", f"canonical config is not valid JSON: {exc}")
    policy = _policy_from_canonical_document(document)
    config_digest = canonical_digest_v1(json.loads(raw.decode("utf-8")))
    policy_digest = _policy_digest(policy)
    plane = TrustedAuthorityPlaneV1(
        plane_id="plane_" + secrets.token_hex(16),
        issuer_id=policy.issuer_id,
        policy_revision=policy.policy_revision,
        config_revision=policy.config_revision,
        policy_digest=policy_digest,
        config_digest=config_digest,
        _seal=_PLANE_SEAL,
        _policy=policy,
        _kill_switch=_RuntimeKillSwitch(engaged=bool(policy.kill_switch)),
    )
    _register_plane(plane)
    return plane


def _test_only_compose_trusted_authority_plane_from_canonical_config_v1(
    *,
    config_path: Path | str,
) -> TrustedAuthorityPlaneV1:
    """Narrow same-process-trusted test helper.

    Documented boundary: tests and the trusted composition root in this process
    may call this helper with a canonical config fixture path. It is not a
    public mint API and does not accept caller-built policy objects.
    """
    return _compose_trusted_authority_plane_from_canonical_config_v1(
        config_path=config_path,
        _seal=_PLANE_SEAL,
    )


def write_canonical_authority_config_fixture_v1(
    path: Path | str,
    *,
    issuer_id: str = "issuer.local-test",
    policy_revision: str = "policy-rev-1",
    config_revision: str = "config-rev-1",
    allowed_adapter_ids: tuple[str, ...] = ("test.injected_adapter",),
    allowed_operations: tuple[str, ...] = ("noop_apply",),
    allowed_targets: tuple[tuple[str, str], ...] = (
        ("injected_callable", "target/alpha"),
    ),
    kill_switch: bool = False,
) -> Path:
    """Test/helper writer for canonical config fixtures consumed by composition root."""
    target = Path(path)
    document = {
        "schema_version": "effect_dispatch_canonical_policy_v1",
        "issuer_id": issuer_id,
        "policy_revision": policy_revision,
        "config_revision": config_revision,
        "allowed_adapter_ids": list(allowed_adapter_ids),
        "allowed_operations": list(allowed_operations),
        "allowed_targets": [list(pair) for pair in allowed_targets],
        "kill_switch": bool(kill_switch),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return target


def derive_effect_id_v1(command: EffectCommandV1) -> str:
    material = {
        "idempotency_key": command.idempotency_key,
        "request_digest": command.request_digest,
        "adapter_id": command.adapter_id,
        "operation": command.operation,
        "target": dict(command.target),
    }
    return "effect_" + hashlib.sha256(
        canonical_digest_v1(material).encode("utf-8")
    ).hexdigest()[:32]


def _target_digest(command: EffectCommandV1) -> str:
    return canonical_digest_v1(dict(command.target))


def _command_digest(command: EffectCommandV1) -> str:
    return canonical_digest_v1(effect_command_body_v1(command))


def _authority_binding_digest(grant: AuthorityGrantV1) -> str:
    return canonical_digest_v1(
        {
            "issuer_id": grant.issuer_id,
            "policy_revision": grant.policy_revision,
            "config_revision": grant.config_revision,
            "adapter_id": grant.adapter_id,
            "operation": grant.operation,
            "target_kind": grant.target_kind,
            "target_ref": grant.target_ref,
            "target_digest": grant.target_digest,
            "payload_digest": grant.payload_digest,
            "command_digest": grant.command_digest,
            "policy_digest": grant.policy_digest,
            "config_digest": grant.config_digest,
            "issuance_binding_id": grant.issuance_binding_id,
        }
    )


def _require_canonical_command(command: EffectCommandV1) -> EffectCommandV1:
    if not isinstance(command, EffectCommandV1):
        _fail("effect_command_invalid", "command must be EffectCommandV1")
    fresh = rederive_effect_command_v1(command)
    if fresh.request_digest != command.request_digest:
        _fail(
            "effect_command_digest_mismatch",
            "command.request_digest does not match canonical body",
        )
    if _command_digest(fresh) != _command_digest(command):
        _fail("effect_command_digest_mismatch", "command body is not canonical")
    return fresh


def issue_authority_grant_v1(
    *,
    command: EffectCommandV1,
    plane: TrustedAuthorityPlaneV1,
    ttl_seconds: float = 30.0,
) -> AuthorityGrantV1:
    """Issue a one-shot grant bound to a trusted plane-held canonical policy.

    No caller policy parameter may override the plane snapshot. Command data
    alone is insufficient: `plane` must be a live registered plane from the
    composition root.
    """
    command = _require_canonical_command(command)
    if not isinstance(plane, TrustedAuthorityPlaneV1):
        _fail("authority_plane_invalid", "plane must be TrustedAuthorityPlaneV1")
    if not _is_registered_plane(plane):
        _fail(
            "authority_plane_not_registered",
            "authority plane was not composed in this process",
        )
    if not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool):
        _fail("authority_grant_ttl_invalid", "ttl_seconds must be a number")
    ttl = float(ttl_seconds)
    if not (0 < ttl <= 60.0):
        _fail("authority_grant_ttl_invalid", "ttl_seconds must be within (0, 60]")

    policy = plane.canonical_policy()
    if plane.kill_switch or policy.kill_switch:
        _fail("kill_switch_engaged", "dispatch kill switch is engaged")
    _validate_policy(command, policy)
    if plane.issuer_id != command.issuer_id:
        _fail("authority_grant_mismatch", "plane issuer does not match command")
    if plane.policy_revision != command.policy_revision:
        _fail("authority_grant_mismatch", "plane policy_revision does not match command")
    if plane.config_revision != command.config_revision:
        _fail("authority_grant_mismatch", "plane config_revision does not match command")

    target_kind = command.target["target_kind"]
    target_ref = command.target["target_ref"]
    target_digest = _target_digest(command)
    command_digest = _command_digest(command)
    ticket = _OneShotTicket()
    nonce = secrets.token_hex(16)
    issue_token = secrets.token_hex(16)
    expires_at = time.monotonic() + ttl
    binding_seed = {
        "plane_id": plane.plane_id,
        "command_digest": command_digest,
        "target_kind": target_kind,
        "target_ref": target_ref,
        "target_digest": target_digest,
        "policy_digest": plane.policy_digest,
        "config_digest": plane.config_digest,
        "issuer_id": plane.issuer_id,
        "policy_revision": plane.policy_revision,
        "config_revision": plane.config_revision,
        "adapter_id": command.adapter_id,
        "operation": command.operation,
        "payload_digest": command.payload_digest,
        "nonce": nonce,
        "issue_token": issue_token,
        "ticket_token": ticket.token,
        "expires_at_monotonic": expires_at,
        "schema_version": "authority_grant_v1",
        "nonce_seed": secrets.token_hex(16),
    }
    # Do not use contract canonical JSON here: monotonic expiry is a raw float.
    binding_id = "issbind_" + hashlib.sha256(
        json.dumps(binding_seed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()[:32]
    grant = AuthorityGrantV1(
        schema_version="authority_grant_v1",
        nonce=nonce,
        issuer_id=plane.issuer_id,
        policy_revision=plane.policy_revision,
        config_revision=plane.config_revision,
        adapter_id=command.adapter_id,
        operation=command.operation,
        target_kind=target_kind,
        target_ref=target_ref,
        target_digest=target_digest,
        payload_digest=command.payload_digest,
        command_digest=command_digest,
        policy_digest=plane.policy_digest,
        config_digest=plane.config_digest,
        issuance_binding_id=binding_id,
        expires_at_monotonic=expires_at,
        _ticket=ticket,
        _plane_id=plane.plane_id,
        _issue_token=issue_token,
        _ticket_token=ticket.token,
    )
    binding_record = {
        **binding_seed,
        "grant_object_id": id(grant),
        "ticket_object_id": id(ticket),
    }
    _record_issuance_binding(binding_id, binding_record)
    with _REGISTRY_LOCK:
        _ISSUED_GRANTS[issue_token] = grant
    _register_ticket(grant.nonce, ticket)
    return grant


def _validate_grant_without_consuming(
    grant: AuthorityGrantV1 | None,
    command: EffectCommandV1,
    plane: TrustedAuthorityPlaneV1,
) -> AuthorityGrantV1:
    if grant is None:
        _fail("authority_grant_missing", "authority grant is required")
    if not isinstance(grant, AuthorityGrantV1):
        _fail("authority_grant_invalid", "grant must be AuthorityGrantV1")
    if grant.schema_version != "authority_grant_v1":
        _fail("authority_grant_invalid", "unsupported grant schema_version")
    if not _is_registered_plane(plane):
        _fail("authority_plane_not_registered", "authority plane was not composed")
    if grant._plane_id != plane.plane_id:
        _fail("authority_plane_mismatch", "grant is not bound to the provided plane")
    _require_issuance_binding(grant)
    policy = plane.canonical_policy()
    matches = (
        (grant.issuer_id, command.issuer_id),
        (grant.policy_revision, command.policy_revision),
        (grant.config_revision, command.config_revision),
        (grant.adapter_id, command.adapter_id),
        (grant.operation, command.operation),
        (grant.target_kind, command.target["target_kind"]),
        (grant.target_ref, command.target["target_ref"]),
        (grant.target_digest, _target_digest(command)),
        (grant.payload_digest, command.payload_digest),
        (grant.command_digest, _command_digest(command)),
        (grant.policy_digest, plane.policy_digest),
        (grant.config_digest, plane.config_digest),
        (grant.policy_digest, _policy_digest(policy)),
    )
    for left, right in matches:
        if left != right:
            _fail("authority_grant_mismatch", "grant does not exactly bind the command/plane")
    with grant._ticket.lock:
        if grant._ticket.creator_pid != os.getpid():
            _fail(
                "authority_grant_process_mismatch",
                "grant cannot be used outside its process",
            )
        if grant._ticket.consumed:
            _fail("authority_grant_reused", "grant has already been consumed")
        if not _is_registered_ticket(grant.nonce, grant._ticket):
            _fail(
                "authority_grant_not_registered",
                "grant was not issued by this module",
            )
        record = _require_issuance_binding(grant)
        original_expiry = record["expires_at_monotonic"]
        if time.monotonic() > float(original_expiry):
            _revoke_ticket(grant.nonce)
            _fail("authority_grant_expired", "grant has expired")
    return grant


def _consume_grant(grant: AuthorityGrantV1, plane: TrustedAuthorityPlaneV1) -> None:
    with grant._ticket.lock:
        if plane.kill_switch:
            _revoke_ticket(grant.nonce)
            _fail("kill_switch_engaged", "dispatch kill switch engaged before consume")
        if grant._ticket.creator_pid != os.getpid():
            _fail(
                "authority_grant_process_mismatch",
                "grant cannot be used outside its process",
            )
        if grant._ticket.consumed:
            _fail("authority_grant_reused", "grant has already been consumed")
        if not _is_registered_ticket(grant.nonce, grant._ticket):
            _fail(
                "authority_grant_not_registered",
                "grant was not issued by this module",
            )
        record = _require_issuance_binding(grant)
        if time.monotonic() > float(record["expires_at_monotonic"]):
            _revoke_ticket(grant.nonce)
            _fail("authority_grant_expired", "grant has expired")
        if grant.policy_digest != plane.policy_digest:
            _fail("authority_grant_mismatch", "grant policy digest drift")
        if grant.config_digest != plane.config_digest:
            _fail("authority_grant_mismatch", "grant config digest drift")
        grant._ticket.consumed = True
        _revoke_ticket(grant.nonce)


def _validate_policy(command: EffectCommandV1, policy: EffectDispatchPolicyV1) -> None:
    if policy.kill_switch:
        _fail("kill_switch_engaged", "dispatch kill switch is engaged")
    if policy.issuer_id != command.issuer_id:
        _fail("authority_grant_mismatch", "policy issuer does not match command")
    if policy.policy_revision != command.policy_revision:
        _fail("authority_grant_mismatch", "policy revision does not match command")
    if policy.config_revision != command.config_revision:
        _fail("authority_grant_mismatch", "config revision does not match command")
    if command.adapter_id not in policy.allowed_adapter_ids:
        _fail("adapter_not_allowlisted", "adapter_id is not allowlisted")
    if command.operation not in policy.allowed_operations:
        _fail("operation_not_allowlisted", "operation is not allowlisted")
    target_pair = (command.target["target_kind"], command.target["target_ref"])
    if target_pair not in set(policy.allowed_targets):
        _fail(
            "target_not_allowlisted",
            "exact (target_kind, target_ref) is not allowlisted",
        )



def _normalize_preflight_readback(readback: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(readback, Mapping):
        _fail("preflight_invalid", "preflight readback must be an object")
    # Canonicalize via JSON round-trip of compact sorted form.
    try:
        normalized = json.loads(
            json.dumps(dict(readback), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        _fail("preflight_invalid", f"preflight readback is not canonical JSON: {exc}")
    if not isinstance(normalized, dict):
        _fail("preflight_invalid", "preflight readback must be an object")
    return normalized


def _preflight_digest(readback: Mapping[str, Any]) -> str:
    return canonical_digest_v1(_normalize_preflight_readback(readback))


def _raise_unknown_from_post_intent(exc: BaseException, *, effect_id: str, idempotency_key: str) -> NoReturn:
    if isinstance(exc, EffectDispatchUnknownError):
        raise exc
    raise EffectDispatchUnknownError(
        f"post-intent failure requires reconcile-only recovery: {exc}",
        effect_id=effect_id,
        idempotency_key=idempotency_key,
    ) from exc


def _thaw_mapping(value: object, *, label: str) -> dict[str, Any]:
    """Thaw a public frozen MappingProxyType/nested projection into plain JSON dict."""
    if not isinstance(value, Mapping):
        _fail("effect_recovery_invalid", f"{label} must be an object")
    thawed = thaw_admitted_json_v1(value)
    if not isinstance(thawed, dict):
        _fail("effect_recovery_invalid", f"{label} must be an object")
    return thawed


def recover_observed_effect_receipt_v1(
    *,
    private_root: Path | str,
    command: EffectCommandV1,
    plane: TrustedAuthorityPlaneV1,
    grant: AuthorityGrantV1 | None = None,
    now: Callable[[], str] | None = None,
) -> EffectDispatchResultV1:
    """Deterministic restart recovery for observed-but-no-receipt.

    Validates durable observation and synthesizes/appends the exact canonical
    receipt without invoking the adapter. Intent-only remains unknown/reconcile-only.

    Public projections are deeply frozen (MappingProxyType/tuple). Recovery must
    consume those immutable views via thaw, never require mutable dict aliases.
    """
    command = _require_canonical_command(command)
    timestamp = now or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    effect_id = derive_effect_id_v1(command)
    root = Path(private_root)
    projection = read_effect_projection_v1(private_root=root, effect_id=effect_id)
    status = projection.get("status")
    if status in {"applied", "not_applied"}:
        return EffectDispatchResultV1(
            effect_id=effect_id,
            result_classification=str(status),
            fence=projection.get("fence"),
            receipt=_receipt_from_projection(projection),
            observation=_observation_from_projection(projection),
            replayed=True,
        )
    if status in {"unknown", "intent_recorded", "absent", "claimed"}:
        _fail(
            "effect_unknown_no_redispatch",
            "intent-only or unresolved effect requires manual reconcile, not receipt recovery",
        )
    if status != "observed":
        _fail("effect_recovery_invalid", f"cannot recover receipt from status {status}")
    observation = projection.get("observation")
    if not isinstance(observation, Mapping):
        _fail("effect_recovery_invalid", "observed effect missing durable observation body")
    # Thaw immutable public projection body before contract parse/seal so nested
    # frozen tuples/maps remain exact durable facts without mutable aliasing.
    observation_body = _thaw_mapping(observation, label="observation")
    parsed_obs = parse_effect_observation_v1(observation_body)
    if parsed_obs.classification not in {"applied", "not_applied"}:
        _fail("effect_unknown_no_redispatch", "unknown observation requires reconcile")
    # Grant is optional for recovery only when authority binding is already durable.
    facts_raw = projection.get("facts") or {}
    facts = _thaw_mapping(facts_raw, label="facts") if isinstance(facts_raw, Mapping) else {}
    target_digest = facts.get("target_digest") or _target_digest(command)
    durable_authority = facts.get("authority_binding_digest")
    if grant is not None:
        checked = _validate_grant_without_consuming(grant, command, plane)
        # Prefer durable intent authority binding so receipt matches ledger facts.
        authority_binding = (
            durable_authority
            if isinstance(durable_authority, str)
            else _authority_binding_digest(checked)
        )
    else:
        authority_binding = durable_authority
        if not isinstance(authority_binding, str):
            _fail("effect_recovery_invalid", "missing durable authority_binding_digest for recovery")
        checked = None
    preflight_raw = facts.get("preflight_readback")
    if isinstance(preflight_raw, Mapping):
        preflight_readback = _thaw_mapping(preflight_raw, label="preflight_readback")
    elif preflight_raw is None:
        # Intent should have stored preflight; keep explicit recovered marker only
        # when durable preflight fact is genuinely absent.
        preflight_readback = {"recovered": True}
    else:
        _fail("effect_recovery_invalid", "durable preflight_readback is not an object")
    post_readback = thaw_admitted_json_v1(parsed_obs.readback)
    if not isinstance(post_readback, dict):
        _fail("effect_recovery_invalid", "observation readback must be an object")

    # Build receipt using durable observation post-readback.
    if checked is not None and authority_binding == _authority_binding_digest(checked):
        receipt = _seal_receipt(
            command=command,
            effect_id=effect_id,
            fence=int(parsed_obs.fence),
            grant=checked,
            preflight_readback=preflight_readback,
            post_readback=post_readback,
            classification=parsed_obs.classification,
        )
        auth_digest = authority_binding
    else:
        # Seal receipt with command-bound fields and durable authority digest.
        body = {
            "schema_version": "effect_receipt_v1",
            "effect_id": effect_id,
            "command_digest": _command_digest(command),
            "adapter_id": command.adapter_id,
            "operation": command.operation,
            "target": {
                "target_kind": command.target["target_kind"],
                "target_ref": command.target["target_ref"],
            },
            "payload_digest": command.payload_digest,
            "authority_binding_digest": authority_binding,
            "idempotency_key": command.idempotency_key,
            "fence": int(parsed_obs.fence),
            "preflight_readback": preflight_readback,
            "post_readback": post_readback,
            "result_classification": parsed_obs.classification,
            "compensation_ref": command.compensation_ref,
            "reconcile_ref": command.reconcile_ref,
        }
        receipt = parse_effect_receipt_v1({**body, "receipt_digest": canonical_digest_v1(body)})
        auth_digest = authority_binding

    try:
        with effect_ledger.hold_target_ownership_v1(
            private_root=root, target_digest=str(target_digest)
        ) as ownership:
            # Re-read only through the descriptor-pinned owned namespace.
            projection = effect_ledger.project_effect_under_ownership_v1(
                ownership=ownership, effect_id=effect_id
            )
            if projection.get("status") in {"applied", "not_applied"}:
                return EffectDispatchResultV1(
                    effect_id=effect_id,
                    result_classification=str(projection["status"]),
                    fence=projection.get("fence"),
                    receipt=_receipt_from_projection(projection),
                    observation=_observation_from_projection(projection),
                    replayed=True,
                )
            if projection.get("status") != "observed":
                _fail(
                    "effect_recovery_invalid",
                    f"status changed before recovery: {projection.get('status')}",
                )
            effect_ledger.append_effect_event_v1(
                private_root=root,
                effect_id=effect_id,
                event_type="receipt_terminal",
                payload={
                    "fence": int(parsed_obs.fence),
                    "result_classification": parsed_obs.classification,
                    "receipt_digest": receipt.receipt_digest,
                    "command_digest": _command_digest(command),
                    "request_digest": command.request_digest,
                    "target_digest": str(target_digest),
                    "authority_binding_digest": auth_digest,
                    "observation_digest": canonical_digest_v1(effect_observation_to_dict(parsed_obs)),
                    "receipt": effect_receipt_to_dict(receipt),
                    "adapter_id": command.adapter_id,
                    "operation": command.operation,
                    "payload_digest": command.payload_digest,
                    "idempotency_key": command.idempotency_key,
                    "effect_id": effect_id,
                    "observation": effect_observation_to_dict(parsed_obs),
                },
                idempotency_key=f"{effect_id}:receipt",
                occurred_at=timestamp(),
                ownership=ownership,
            )
    except effect_ledger.WorkflowEffectLedgerError as exc:
        _raise_unknown_from_post_intent(
            exc, effect_id=effect_id, idempotency_key=command.idempotency_key
        )
    return EffectDispatchResultV1(
        effect_id=effect_id,
        result_classification=parsed_obs.classification,
        fence=int(parsed_obs.fence),
        receipt=receipt,
        observation=effect_observation_to_dict(parsed_obs),
        replayed=False,
    )


def read_effect_projection_v1(*, private_root: Path | str, effect_id: str) -> dict[str, Any]:
    """Public projection/read. Propagates every ledger validation/corruption error.

    Only a genuine normal absent projection is returned as absent.
    """
    try:
        return effect_ledger.project_effect_v1(
            private_root=private_root, effect_id=effect_id
        )
    except effect_ledger.WorkflowEffectLedgerError as exc:
        raise EffectDispatchError(exc.code, str(exc)) from exc


def _seal_receipt(
    *,
    command: EffectCommandV1,
    effect_id: str,
    fence: int,
    grant: AuthorityGrantV1,
    preflight_readback: Mapping[str, Any],
    post_readback: Mapping[str, Any],
    classification: str,
) -> EffectReceiptV1:
    body = {
        "schema_version": "effect_receipt_v1",
        "effect_id": effect_id,
        "command_digest": _command_digest(command),
        "adapter_id": command.adapter_id,
        "operation": command.operation,
        "target": thaw_admitted_json_v1(command.target),
        "payload_digest": command.payload_digest,
        "authority_binding_digest": _authority_binding_digest(grant),
        "idempotency_key": command.idempotency_key,
        "fence": fence,
        "preflight_readback": thaw_admitted_json_v1(preflight_readback),
        "post_readback": thaw_admitted_json_v1(post_readback),
        "result_classification": classification,
        "compensation_ref": command.compensation_ref,
        "reconcile_ref": command.reconcile_ref,
    }
    receipt_digest = canonical_digest_v1(body)
    return parse_effect_receipt_v1({**body, "receipt_digest": receipt_digest})


def _seal_observation(
    *,
    effect_id: str,
    fence: int,
    classification: str,
    adapter_result_digest: str | None,
    readback: Mapping[str, Any],
    observed_at: str,
) -> EffectObservationV1:
    body = {
        "schema_version": "effect_observation_v1",
        "effect_id": effect_id,
        "observation_id": "obs_"
        + hashlib.sha256(
            f"{effect_id}:{fence}:{classification}:{observed_at}".encode("utf-8")
        ).hexdigest()[:32],
        "classification": classification,
        "fence": fence,
        "observed_at": observed_at,
        "adapter_result_digest": adapter_result_digest,
        "readback": thaw_admitted_json_v1(readback),
    }
    return parse_effect_observation_v1(body)


def _receipt_from_projection(projection: Mapping[str, Any]) -> EffectReceiptV1 | None:
    raw = projection.get("terminal_receipt")
    if isinstance(raw, Mapping):
        return parse_effect_receipt_v1(thaw_admitted_json_v1(raw))
    return None


def _observation_from_projection(projection: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = projection.get("observation")
    if isinstance(raw, Mapping):
        return thaw_admitted_json_v1(raw)
    facts_raw = projection.get("facts") or {}
    facts = thaw_admitted_json_v1(facts_raw) if isinstance(facts_raw, Mapping) else {}
    if not isinstance(facts, dict):
        return None
    if isinstance(facts.get("observation"), Mapping):
        return thaw_admitted_json_v1(facts["observation"])
    if facts.get("readback") is not None:
        return {
            "classification": facts.get("classification"),
            "readback": thaw_admitted_json_v1(facts.get("readback")),
        }
    return None


def dispatch_effect_v1(
    *,
    private_root: Path | str,
    command: EffectCommandV1,
    grant: AuthorityGrantV1 | None,
    plane: TrustedAuthorityPlaneV1,
    preflight: PreflightFn,
    adapter: AdapterFn,
    crash_phase: str | None = None,
    now: Callable[[], str] | None = None,
) -> EffectDispatchResultV1:
    """Dispatch under plane-held canonical policy. No caller policy override."""
    command = _require_canonical_command(command)
    if crash_phase is not None and crash_phase not in {
        "before_intent",
        "after_intent_before_dispatch",
        "after_apply_before_observation",
        "after_observation_before_receipt",
        "before_adapter",
    }:
        _fail("crash_phase_invalid", f"unsupported crash_phase: {crash_phase}")

    timestamp = now or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    effect_id = derive_effect_id_v1(command)
    root = Path(private_root)
    target_digest = _target_digest(command)
    policy = plane.canonical_policy()
    # Stage memory replaces unsafe pathname reprojection after an owned failure.
    durable_intent_recorded = False

    existing = read_effect_projection_v1(private_root=root, effect_id=effect_id)
    if existing["status"] in {"unknown", "observed"}:
        _fail(
            "effect_unknown_no_redispatch",
            "unknown effect may only enter reconcile, never redispatch",
        )
    if existing["status"] == "intent_recorded":
        _fail(
            "effect_unknown_no_redispatch",
            "intent without terminal observation is unknown; no redispatch",
        )
    if existing["status"] in {"applied", "not_applied"}:
        return EffectDispatchResultV1(
            effect_id=effect_id,
            result_classification=str(existing["status"]),
            fence=existing.get("fence"),
            receipt=_receipt_from_projection(existing),
            observation=_observation_from_projection(existing),
            replayed=True,
        )

    checked_grant = _validate_grant_without_consuming(grant, command, plane)
    if plane.kill_switch:
        _fail("kill_switch_engaged", "dispatch kill switch is engaged")
    _validate_policy(command, policy)
    preflight_readback = _normalize_preflight_readback(preflight(command))

    try:
        with effect_ledger.hold_target_ownership_v1(
            private_root=root, target_digest=target_digest
        ) as ownership:
            existing = effect_ledger.project_effect_under_ownership_v1(
                ownership=ownership, effect_id=effect_id
            )
            if existing["status"] in {"unknown", "observed"}:
                _fail(
                    "effect_unknown_no_redispatch",
                    "unknown effect may only enter reconcile, never redispatch",
                )
            if existing["status"] == "intent_recorded":
                _fail(
                    "effect_unknown_no_redispatch",
                    "intent without terminal observation is unknown; no redispatch",
                )
            if existing["status"] in {"applied", "not_applied"}:
                return EffectDispatchResultV1(
                    effect_id=effect_id,
                    result_classification=str(existing["status"]),
                    fence=existing.get("fence"),
                    receipt=_receipt_from_projection(existing),
                    observation=_observation_from_projection(existing),
                    replayed=True,
                )

            try:
                claim = effect_ledger.claim_effect_v1(
                    private_root=root,
                    effect_id=effect_id,
                    payload={
                        "command_digest": _command_digest(command),
                        "target_digest": target_digest,
                        "owner": f"pid:{os.getpid()}",
                        "request_digest": command.request_digest,
                        "idempotency_key": command.idempotency_key,
                        "adapter_id": command.adapter_id,
                        "operation": command.operation,
                        "payload_digest": command.payload_digest,
                        "effect_id": effect_id,
                    },
                    idempotency_key=f"{effect_id}:claim",
                    occurred_at=timestamp(),
                    ownership=ownership,
                )
            except effect_ledger.WorkflowEffectLedgerError as exc:
                raise EffectDispatchError(exc.code, str(exc)) from exc

            if claim.get("replayed") and claim.get("status") in {"applied", "not_applied"}:
                return EffectDispatchResultV1(
                    effect_id=effect_id,
                    result_classification=str(claim["status"]),
                    fence=claim.get("fence"),
                    receipt=_receipt_from_projection(claim),
                    observation=_observation_from_projection(claim),
                    replayed=True,
                )
            if claim.get("status") == "unknown" or (
                claim.get("replayed") and claim.get("status") in {"intent_recorded", "observed"}
            ):
                _fail(
                    "effect_unknown_no_redispatch",
                    "existing unresolved intent blocks redispatch",
                )
            if claim.get("replayed") and claim.get("status") == "claimed":
                owner = (claim.get("facts") or {}).get("owner")
                if owner != f"pid:{os.getpid()}":
                    _fail(
                        "effect_claim_owned",
                        "effect claim is owned by another dispatcher",
                    )

            if plane.kill_switch:
                _fail("kill_switch_engaged", "dispatch kill switch engaged under ownership")
            _validate_policy(command, plane.canonical_policy())
            _validate_grant_without_consuming(checked_grant, command, plane)
            # Second normalized preflight under ownership immediately before intent/consume.
            owned_preflight = _normalize_preflight_readback(preflight(command))
            if (
                owned_preflight != preflight_readback
                or _preflight_digest(owned_preflight) != _preflight_digest(preflight_readback)
            ):
                _fail("preflight_drift", "preflight readback drifted under ownership")
            preflight_readback = owned_preflight
            _consume_grant(checked_grant, plane)

            fence = int(claim["fence"])
            if crash_phase == "before_intent":
                _fail("not_dispatched", "crash injected before durable dispatch intent")

            try:
                effect_ledger.append_effect_event_v1(
                    private_root=root,
                    effect_id=effect_id,
                    event_type="intent_recorded",
                    payload={
                        "fence": fence,
                        "command_digest": _command_digest(command),
                        "request_digest": command.request_digest,
                        "target_digest": target_digest,
                        "authority_binding_digest": _authority_binding_digest(checked_grant),
                        "preflight_readback": preflight_readback,
                        "preflight_readback_digest": _preflight_digest(preflight_readback),
                        "adapter_id": command.adapter_id,
                        "operation": command.operation,
                        "payload_digest": command.payload_digest,
                        "idempotency_key": command.idempotency_key,
                        "effect_id": effect_id,
                    },
                    idempotency_key=f"{effect_id}:intent",
                    occurred_at=timestamp(),
                    ownership=ownership,
                )
                durable_intent_recorded = True
            except effect_ledger.WorkflowEffectLedgerError as exc:
                if getattr(exc, "code", None) == "durability_unknown" or getattr(
                    exc, "phase", None
                ) == "post_event_fsync":
                    # The append reached its durability boundary. Preserve this
                    # stage fact even if ownership-exit identity verification
                    # replaces the in-flight unknown exception while unwinding.
                    durable_intent_recorded = True
                    _raise_unknown_from_post_intent(
                        exc, effect_id=effect_id, idempotency_key=command.idempotency_key
                    )
                # Confirm whether intent became durable despite the error, but
                # never escape the descriptor-owned namespace to do so.
                try:
                    proj = effect_ledger.project_effect_under_ownership_v1(
                        ownership=ownership, effect_id=effect_id
                    )
                except effect_ledger.WorkflowEffectLedgerError as identity_exc:
                    raise EffectDispatchError(
                        identity_exc.code, str(identity_exc)
                    ) from identity_exc
                if proj.get("status") in {"intent_recorded", "unknown", "observed"}:
                    durable_intent_recorded = True
                    _raise_unknown_from_post_intent(
                        exc, effect_id=effect_id, idempotency_key=command.idempotency_key
                    )
                raise EffectDispatchError(exc.code, str(exc)) from exc

            # Durable intent_recorded is committed. Every subsequent ambiguous failure is
            # reconcile-only unknown. Do not catch BaseException.
            post_intent_terminal_recorded = False
            try:
                if crash_phase == "after_intent_before_dispatch":
                    raise EffectDispatchUnknownError(
                        "crash injected after durable intent and before dispatch",
                        effect_id=effect_id,
                        idempotency_key=command.idempotency_key,
                    )

                if plane.kill_switch:
                    observed_at = timestamp()
                    observation = _seal_observation(
                        effect_id=effect_id,
                        fence=fence,
                        classification="not_applied",
                        adapter_result_digest=None,
                        readback={
                            "present": False,
                            "content_digest": None,
                            "reason": "kill_switch",
                        },
                        observed_at=observed_at,
                    )
                    effect_ledger.append_effect_event_v1(
                        private_root=root,
                        effect_id=effect_id,
                        event_type="observation_recorded",
                        payload={
                            "fence": fence,
                            "classification": "not_applied",
                            "adapter_result_digest": None,
                            "readback": dict(observation.readback),
                            "readback_digest": canonical_digest_v1(dict(observation.readback)),
                            "observation": effect_observation_to_dict(observation),
                            "command_digest": _command_digest(command),
                            "request_digest": command.request_digest,
                            "target_digest": target_digest,
                            "authority_binding_digest": _authority_binding_digest(
                                checked_grant
                            ),
                            "adapter_id": command.adapter_id,
                            "operation": command.operation,
                            "payload_digest": command.payload_digest,
                            "idempotency_key": command.idempotency_key,
                            "effect_id": effect_id,
                        },
                        idempotency_key=f"{effect_id}:obs-kill",
                        occurred_at=observed_at,
                        ownership=ownership,
                    )
                    receipt = _seal_receipt(
                        command=command,
                        effect_id=effect_id,
                        fence=fence,
                        grant=checked_grant,
                        preflight_readback=preflight_readback,
                        post_readback=dict(observation.readback),
                        classification="not_applied",
                    )
                    effect_ledger.append_effect_event_v1(
                        private_root=root,
                        effect_id=effect_id,
                        event_type="receipt_terminal",
                        payload={
                            "fence": fence,
                            "result_classification": "not_applied",
                            "receipt_digest": receipt.receipt_digest,
                            "command_digest": _command_digest(command),
                            "request_digest": command.request_digest,
                            "target_digest": target_digest,
                            "authority_binding_digest": _authority_binding_digest(
                                checked_grant
                            ),
                            "observation_digest": canonical_digest_v1(
                                effect_observation_to_dict(observation)
                            ),
                            "receipt": effect_receipt_to_dict(receipt),
                            "adapter_id": command.adapter_id,
                            "operation": command.operation,
                            "payload_digest": command.payload_digest,
                            "idempotency_key": command.idempotency_key,
                            "effect_id": effect_id,
                            "observation": effect_observation_to_dict(observation),
                        },
                        idempotency_key=f"{effect_id}:receipt",
                        occurred_at=timestamp(),
                        ownership=ownership,
                    )
                    post_intent_terminal_recorded = True
                    _fail(
                        "kill_switch_engaged",
                        "dispatch kill switch engaged before adapter call",
                    )

                if crash_phase == "before_adapter":
                    raise EffectDispatchUnknownError(
                        "crash injected after durable intent and before adapter",
                        effect_id=effect_id,
                        idempotency_key=command.idempotency_key,
                    )

                adapter_result = adapter(command, fence)
                if not isinstance(adapter_result, EffectAdapterResultV1):
                    raise EffectDispatchError(
                        "adapter_result_invalid",
                        "adapter must return EffectAdapterResultV1",
                    )
                classification = adapter_result.classification
                if classification not in {"applied", "not_applied", "unknown"}:
                    raise EffectDispatchError(
                        "adapter_result_invalid",
                        f"unsupported adapter classification: {classification}",
                    )

                if crash_phase == "after_apply_before_observation":
                    raise EffectDispatchUnknownError(
                        "crash injected after adapter apply and before durable observation",
                        effect_id=effect_id,
                        idempotency_key=command.idempotency_key,
                    )

                observed_at = timestamp()
                # Sealing/canonicalization/readback construction is post-intent.
                if not isinstance(adapter_result.readback, Mapping):
                    raise EffectDispatchError(
                        "adapter_result_invalid",
                        "adapter readback must be an object",
                    )
                observation = _seal_observation(
                    effect_id=effect_id,
                    fence=fence,
                    classification=classification,
                    adapter_result_digest=adapter_result.adapter_result_digest,
                    readback=dict(adapter_result.readback),
                    observed_at=observed_at,
                )
                effect_ledger.append_effect_event_v1(
                    private_root=root,
                    effect_id=effect_id,
                    event_type="observation_recorded",
                    payload={
                        "fence": fence,
                        "classification": classification,
                        "adapter_result_digest": adapter_result.adapter_result_digest,
                        "readback": dict(adapter_result.readback),
                        "readback_digest": canonical_digest_v1(dict(adapter_result.readback)),
                        "observation": effect_observation_to_dict(observation),
                        "command_digest": _command_digest(command),
                        "request_digest": command.request_digest,
                        "target_digest": target_digest,
                        "authority_binding_digest": _authority_binding_digest(checked_grant),
                        "adapter_id": command.adapter_id,
                        "operation": command.operation,
                        "payload_digest": command.payload_digest,
                        "idempotency_key": command.idempotency_key,
                        "effect_id": effect_id,
                    },
                    idempotency_key=f"{effect_id}:obs",
                    occurred_at=observed_at,
                    ownership=ownership,
                )

                if classification == "unknown":
                    return EffectDispatchResultV1(
                        effect_id=effect_id,
                        result_classification="unknown",
                        fence=fence,
                        receipt=None,
                        observation=effect_observation_to_dict(observation),
                        replayed=False,
                    )

                if crash_phase == "after_observation_before_receipt":
                    raise EffectDispatchUnknownError(
                        "crash injected after durable observation and before receipt",
                        effect_id=effect_id,
                        idempotency_key=command.idempotency_key,
                    )

                receipt = _seal_receipt(
                    command=command,
                    effect_id=effect_id,
                    fence=fence,
                    grant=checked_grant,
                    preflight_readback=preflight_readback,
                    post_readback=dict(adapter_result.readback),
                    classification=classification,
                )
                effect_ledger.append_effect_event_v1(
                    private_root=root,
                    effect_id=effect_id,
                    event_type="receipt_terminal",
                    payload={
                        "fence": fence,
                        "result_classification": classification,
                        "receipt_digest": receipt.receipt_digest,
                        "command_digest": _command_digest(command),
                        "request_digest": command.request_digest,
                        "target_digest": target_digest,
                        "authority_binding_digest": _authority_binding_digest(checked_grant),
                        "observation_digest": canonical_digest_v1(
                            effect_observation_to_dict(observation)
                        ),
                        "receipt": effect_receipt_to_dict(receipt),
                        "adapter_id": command.adapter_id,
                        "operation": command.operation,
                        "payload_digest": command.payload_digest,
                        "idempotency_key": command.idempotency_key,
                        "effect_id": effect_id,
                        "observation": effect_observation_to_dict(observation),
                    },
                    idempotency_key=f"{effect_id}:receipt",
                    occurred_at=timestamp(),
                    ownership=ownership,
                )
                post_intent_terminal_recorded = True
                return EffectDispatchResultV1(
                    effect_id=effect_id,
                    result_classification=classification,
                    fence=fence,
                    receipt=receipt,
                    observation=effect_observation_to_dict(observation),
                    replayed=False,
                )
            except EffectDispatchUnknownError:
                raise
            except Exception as exc:
                # Terminal kill-switch / applied|not_applied receipt already sealed:
                # preserve the ordinary terminal error. Everything else is unknown.
                if post_intent_terminal_recorded and isinstance(exc, EffectDispatchError):
                    if exc.code == "kill_switch_engaged":
                        raise
                _raise_unknown_from_post_intent(
                    exc, effect_id=effect_id, idempotency_key=command.idempotency_key
                )
    except EffectDispatchUnknownError:
        raise
    except EffectDispatchError:
        raise
    except effect_ledger.WorkflowEffectLedgerError as exc:
        # Never re-open the public pathname after owned namespace failure: it may
        # now name attacker replacement bytes. Stage memory is sufficient to
        # preserve post-intent unknown classification without trusting that path.
        if durable_intent_recorded:
            raise EffectDispatchUnknownError(
                f"post-intent ownership ambiguity: {exc}",
                effect_id=effect_id,
                idempotency_key=command.idempotency_key,
            ) from exc
        raise EffectDispatchError(exc.code, str(exc)) from exc
    except Exception as exc:
        if durable_intent_recorded:
            _raise_unknown_from_post_intent(
                exc, effect_id=effect_id, idempotency_key=command.idempotency_key
            )
        raise


def _forge_self_minted_grant_for_test(command: EffectCommandV1) -> AuthorityGrantV1:
    """Test-only counterfeit grant that was never registered."""
    ticket = _OneShotTicket()
    return AuthorityGrantV1(
        schema_version="authority_grant_v1",
        nonce=secrets.token_hex(16),
        issuer_id=command.issuer_id,
        policy_revision=command.policy_revision,
        config_revision=command.config_revision,
        adapter_id=command.adapter_id,
        operation=command.operation,
        target_kind=command.target["target_kind"],
        target_ref=command.target["target_ref"],
        target_digest=_target_digest(command),
        payload_digest=command.payload_digest,
        command_digest=_command_digest(command),
        policy_digest=canonical_digest_v1({"forged": True}),
        config_digest=canonical_digest_v1({"forged": True}),
        issuance_binding_id="issbind_forged",
        expires_at_monotonic=time.monotonic() + 30.0,
        _ticket=ticket,
        _plane_id="plane_forged",
        _issue_token=secrets.token_hex(16),
        _ticket_token=ticket.token,
    )


def _forge_widened_grant_for_test(
    grant: AuthorityGrantV1, *, allowed_extra_target: str
) -> AuthorityGrantV1:
    """Test-only widened counterfeit that keeps public bindings but new ticket."""
    del allowed_extra_target
    return AuthorityGrantV1(
        schema_version=grant.schema_version,
        nonce=grant.nonce,
        issuer_id=grant.issuer_id,
        policy_revision=grant.policy_revision,
        config_revision=grant.config_revision,
        adapter_id=grant.adapter_id,
        operation=grant.operation,
        target_kind=grant.target_kind,
        target_ref=grant.target_ref,
        target_digest=grant.target_digest,
        payload_digest=grant.payload_digest,
        command_digest=grant.command_digest,
        policy_digest=grant.policy_digest,
        config_digest=grant.config_digest,
        issuance_binding_id=grant.issuance_binding_id,
        expires_at_monotonic=grant.expires_at_monotonic,
        _ticket=_OneShotTicket(),
        _plane_id=grant._plane_id,
        _issue_token=secrets.token_hex(16),
        _ticket_token=secrets.token_hex(16),
    )
