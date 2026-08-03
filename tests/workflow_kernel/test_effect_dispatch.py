"""Slice 3: authority/dispatch state machine scaffolding with injected adapters."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import dataclasses
import json
import multiprocessing as mp
import os
import pickle
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from workflow_kernel import effect_contracts, effect_dispatch


DIGEST = "sha256:" + ("ab" * 32)


def _command(**overrides: object) -> effect_contracts.EffectCommandV1:
    body: dict[str, object] = {
        "schema_version": "effect_command_v1",
        "command_id": "cmd-1",
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "target": {
            "target_kind": "injected_callable",
            "target_ref": "target/alpha",
        },
        "payload_digest": DIGEST,
        "idempotency_key": "dispatch-cmd-1",
        "issuer_id": "issuer.local-test",
        "policy_revision": "policy-rev-1",
        "config_revision": "config-rev-1",
        "side_effect_tier": "local_write",
        "compensation_ref": "compensation:test.restore",
        "reconcile_ref": "reconcile:test.readback",
    }
    body.update(overrides)
    return effect_contracts.parse_effect_command_v1(body)


def _canonical_config(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "canonical-authority-policy.json"
    kwargs = {
        "issuer_id": "issuer.local-test",
        "policy_revision": "policy-rev-1",
        "config_revision": "config-rev-1",
        "allowed_adapter_ids": ("test.injected_adapter",),
        "allowed_operations": ("noop_apply",),
        "allowed_targets": (("injected_callable", "target/alpha"),),
        "kill_switch": False,
    }
    kwargs.update(overrides)
    return effect_dispatch.write_canonical_authority_config_fixture_v1(path, **kwargs)


def _plane(tmp_path: Path, **overrides: object):
    config = _canonical_config(tmp_path, **overrides)
    return effect_dispatch._test_only_compose_trusted_authority_plane_from_canonical_config_v1(
        config_path=config
    )


def _grant(tmp_path: Path, command: effect_contracts.EffectCommandV1, **overrides: object):
    plane = overrides.pop("plane", None) or _plane(tmp_path)
    kwargs = {
        "command": command,
        "plane": plane,
        "ttl_seconds": 5.0,
    }
    kwargs.update(overrides)
    return effect_dispatch.issue_authority_grant_v1(**kwargs), plane


def _adapter_applied(command, fence: int) -> effect_dispatch.EffectAdapterResultV1:
    return effect_dispatch.EffectAdapterResultV1(
        classification="applied",
        adapter_result_digest=DIGEST,
        readback={"present": True, "content_digest": DIGEST, "fence": fence},
    )


def _adapter_unknown(command, fence: int) -> effect_dispatch.EffectAdapterResultV1:
    return effect_dispatch.EffectAdapterResultV1(
        classification="unknown",
        adapter_result_digest=None,
        readback={"present": False, "content_digest": None, "fence": fence},
    )


def test_grant_is_opaque_non_serializable_and_one_shot(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as ser:
        pickle.dumps(grant)
    assert ser.value.code == "authority_grant_not_serializable"
    with pytest.raises(effect_dispatch.EffectDispatchError):
        copy.copy(grant)
    with pytest.raises(effect_dispatch.EffectDispatchError):
        copy.deepcopy(grant)

    first = effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private",
        command=command,
        grant=grant,
        plane=plane,
        preflight=lambda _command: {"ok": True},
        adapter=_adapter_applied,
    )
    assert first.result_classification == "applied"
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private-reuse",
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    # Outside 4A.4 correction scope: authority grant identity matrix (dispatch 4B/authority lane).
    assert raised.value.code in {
        "authority_grant_reused",
        "authority_grant_not_registered",
    }


@pytest.mark.parametrize(
    "mutator,code",
    [
        (lambda c: dataclasses.replace(c, issuer_id="other"), "authority_grant_mismatch"),
        (
            lambda c: dataclasses.replace(c, policy_revision="other"),
            "authority_grant_mismatch",
        ),
        (
            lambda c: dataclasses.replace(c, config_revision="other"),
            "authority_grant_mismatch",
        ),
        (
            lambda c: dataclasses.replace(c, adapter_id="other.adapter"),
            "authority_grant_mismatch",
        ),
        (
            lambda c: dataclasses.replace(c, operation="other_op"),
            "authority_grant_mismatch",
        ),
        (
            lambda c: dataclasses.replace(
                c,
                target={"target_kind": "injected_callable", "target_ref": "other"},
            ),
            "authority_grant_mismatch",
        ),
        (
            lambda c: dataclasses.replace(c, payload_digest="sha256:" + ("cd" * 32)),
            "authority_grant_mismatch",
        ),
    ],
)
def test_grant_binding_mismatches_fail_closed(tmp_path: Path, mutator, code: str) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    bad = mutator(command)
    body = {
        "schema_version": bad.schema_version,
        "command_id": bad.command_id,
        "adapter_id": bad.adapter_id,
        "operation": bad.operation,
        "target": dict(bad.target),
        "payload_digest": bad.payload_digest,
        "idempotency_key": bad.idempotency_key,
        "issuer_id": bad.issuer_id,
        "policy_revision": bad.policy_revision,
        "config_revision": bad.config_revision,
        "side_effect_tier": bad.side_effect_tier,
        "compensation_ref": bad.compensation_ref,
        "reconcile_ref": bad.reconcile_ref,
    }
    bad = effect_contracts.parse_effect_command_v1(body)
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=bad,
            grant=grant,
            plane=plane,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    assert raised.value.code == code


def test_public_api_caller_policy_bootstrap_issue_dispatch_fails(tmp_path: Path) -> None:
    """P0: public caller policy -> bootstrap -> issue -> dispatch must fail closed."""
    assert not hasattr(effect_dispatch, "bootstrap_trusted_authority_plane_v1")
    attacker_command = _command(
        target={"target_kind": "unapproved", "target_ref": "victim/42"},
        idempotency_key="attacker-self-mint",
        command_id="cmd-attacker",
    )
    attacker_policy = effect_dispatch.EffectDispatchPolicyV1(
        issuer_id=attacker_command.issuer_id,
        policy_revision=attacker_command.policy_revision,
        config_revision=attacker_command.config_revision,
        allowed_adapter_ids=(attacker_command.adapter_id,),
        allowed_operations=(attacker_command.operation,),
        allowed_targets=(("unapproved", "victim/42"),),
    )
    # No public bootstrap exists.
    with pytest.raises(AttributeError):
        getattr(effect_dispatch, "bootstrap_trusted_authority_plane_v1")(policy=attacker_policy)
    # Old issuer/revision kwargs API is gone.
    with pytest.raises(TypeError):
        effect_dispatch.issue_authority_grant_v1(  # type: ignore[call-arg]
            command=attacker_command,
            issuer_id=attacker_command.issuer_id,
            policy_revision=attacker_command.policy_revision,
            config_revision=attacker_command.config_revision,
        )
    # Caller policy parameter on issue is rejected.
    plane = _plane(tmp_path)
    with pytest.raises(TypeError):
        effect_dispatch.issue_authority_grant_v1(  # type: ignore[call-arg]
            command=_command(),
            plane=plane,
            policy=attacker_policy,
        )
    # Public construction without seal fails.
    with pytest.raises(effect_dispatch.EffectDispatchError) as unsealed:
        effect_dispatch.TrustedAuthorityPlaneV1(
            plane_id="plane_forged",
            issuer_id="issuer.local-test",
            policy_revision="policy-rev-1",
            config_revision="config-rev-1",
            policy_digest="sha256:" + ("00" * 32),
            config_digest="sha256:" + ("00" * 32),
            _seal=object(),
            _policy=attacker_policy,
            _kill_switch=effect_dispatch._RuntimeKillSwitch(),
        )
    assert unsealed.value.code == "authority_plane_unsealed"


def test_forged_equal_plane_and_unique_plane_ids(tmp_path: Path) -> None:
    plane_a = _plane(tmp_path / "a")
    # Equal-field reconstruction without seal fails; with stolen seal still not registered.
    with pytest.raises(effect_dispatch.EffectDispatchError):
        effect_dispatch.TrustedAuthorityPlaneV1(
            plane_id=plane_a.plane_id,
            issuer_id=plane_a.issuer_id,
            policy_revision=plane_a.policy_revision,
            config_revision=plane_a.config_revision,
            policy_digest=plane_a.policy_digest,
            config_digest=plane_a.config_digest,
            _seal=object(),
            _policy=plane_a.canonical_policy(),
            _kill_switch=effect_dispatch._RuntimeKillSwitch(),
        )
    ids = {
        _plane(tmp_path / f"p{i}").plane_id
        for i in range(20)
    }
    assert len(ids) == 20


def test_substituted_issuance_binding_fails(tmp_path: Path) -> None:
    import dataclasses

    command = _command()
    grant, plane = _grant(tmp_path, command)
    # Dispatch the actual dataclasses.replace grant (do not manually reconstruct fields).
    forged = dataclasses.replace(grant, issuance_binding_id="issbind_" + ("cd" * 16))
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=forged,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=_adapter_applied,
        )
    # Outside 4A.4 correction scope: forged issuance-binding identity matrix.
    assert raised.value.code in {
        "authority_issuance_binding_missing",
        "authority_grant_not_issued_object",
    }


def test_positive_path_crosses_internal_composition_root(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    result = effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private",
        command=command,
        grant=grant,
        plane=plane,
        preflight=lambda _c: {"ok": True},
        adapter=_adapter_applied,
    )
    assert result.result_classification == "applied"
    assert result.receipt is not None


def test_missing_expired_replayed_widened_grants_fail(tmp_path: Path) -> None:
    command = _command()
    plane = _plane(tmp_path)
    with pytest.raises(effect_dispatch.EffectDispatchError) as missing:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=None,  # type: ignore[arg-type]
            plane=plane,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    assert missing.value.code == "authority_grant_missing"

    expired, plane2 = _grant(tmp_path / "e", command, ttl_seconds=0.01)
    time.sleep(0.02)
    with pytest.raises(effect_dispatch.EffectDispatchError) as exp:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=expired,
            plane=plane2,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    assert exp.value.code == "authority_grant_expired"

    grant, plane3 = _grant(tmp_path / "g", command)
    effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private-a",
        command=command,
        grant=grant,
        plane=plane3,
        preflight=lambda _command: {"ok": True},
        adapter=_adapter_applied,
    )
    with pytest.raises(effect_dispatch.EffectDispatchError) as replayed:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private-b",
            command=command,
            grant=grant,
            plane=plane3,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    assert replayed.value.code in {
        "authority_grant_reused",
        "authority_grant_not_registered",
    }

    grant2, plane4 = _grant(tmp_path / "w", command)
    forged = effect_dispatch._forge_widened_grant_for_test(
        grant2, allowed_extra_target="target/beta"
    )
    with pytest.raises(effect_dispatch.EffectDispatchError) as wide:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private-c",
            command=command,
            grant=forged,
            plane=plane4,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    # Outside 4A.4 correction scope: widened-grant adversarial identity matrix.
    assert wide.value.code in {
        "authority_grant_not_registered",
        "authority_grant_widened",
        "authority_grant_mismatch",
        "authority_issuance_binding_missing",
        "authority_grant_not_issued_object",
        "authority_issuance_binding_mismatch",
        "authority_grant_ticket_identity_mismatch",
    }


def test_happy_path_persists_intent_before_dispatch_and_returns_receipt(
    tmp_path: Path,
) -> None:
    command = _command()
    seen: dict[str, Any] = {"calls": 0}

    def adapter(cmd, fence: int):
        seen["calls"] += 1
        projection = effect_dispatch.read_effect_projection_v1(
            private_root=tmp_path / "private",
            effect_id=seen["effect_id"],
        )
        # Intent without observation projects as unknown for redispatch; under the
        # in-flight owner the durable phase remains intent_recorded in facts.
        assert projection["status"] == "unknown"
        assert projection["fence"] == fence
        return _adapter_applied(cmd, fence)

    grant, plane = _grant(tmp_path, command)
    effect_id = effect_dispatch.derive_effect_id_v1(command)
    seen["effect_id"] = effect_id
    result = effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private",
        command=command,
        grant=grant,
        plane=plane,
        preflight=lambda _command: {"ok": True, "target": "target/alpha"},
        adapter=adapter,
    )
    assert seen["calls"] == 1
    assert result.result_classification == "applied"
    assert result.receipt is not None
    assert result.receipt.result_classification == "applied"
    projection = effect_dispatch.read_effect_projection_v1(
        private_root=tmp_path / "private",
        effect_id=effect_id,
    )
    assert projection["status"] == "applied"
    assert projection["terminal_receipt"] is not None
    assert projection["terminal_receipt"]["receipt_digest"] == result.receipt.receipt_digest


def test_restart_recovers_exact_receipt_and_rejects_tamper(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    result = effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private",
        command=command,
        grant=grant,
        plane=plane,
        preflight=lambda _command: {"ok": True},
        adapter=_adapter_applied,
    )
    assert result.receipt is not None
    effect_id = result.effect_id
    restarted = effect_dispatch.read_effect_projection_v1(
        private_root=tmp_path / "private", effect_id=effect_id
    )
    recovered = effect_contracts.parse_effect_receipt_v1(restarted["terminal_receipt"])
    assert recovered == result.receipt

    from workflow_kernel import workflow_effect_ledger as ledger

    path = ledger.effect_ledger_path_v1(private_root=tmp_path / "private", effect_id=effect_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines
    last = json.loads(lines[-1])
    receipt = last["payload"]["receipt"]
    receipt["post_readback"] = {"present": False, "content_digest": None, "tampered": True}
    lines[-1] = json.dumps(last, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(effect_dispatch.EffectDispatchError):
        effect_dispatch.read_effect_projection_v1(
            private_root=tmp_path / "private", effect_id=effect_id
        )


def test_crash_before_intent_is_not_dispatched(tmp_path: Path) -> None:
    command = _command()
    calls = {"n": 0}

    def adapter(cmd, fence: int):
        calls["n"] += 1
        return _adapter_applied(cmd, fence)

    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _command: {"ok": True},
            adapter=adapter,
            crash_phase="before_intent",
        )
    assert raised.value.code == "not_dispatched"
    assert calls["n"] == 0
    effect_id = effect_dispatch.derive_effect_id_v1(command)
    projection = effect_dispatch.read_effect_projection_v1(
        private_root=tmp_path / "private",
        effect_id=effect_id,
    )
    assert projection["status"] in {"claimed", "absent", "not_dispatched"}


def test_crash_after_intent_is_unknown_and_never_redispatches(tmp_path: Path) -> None:
    command = _command()
    calls = {"n": 0}

    def adapter(cmd, fence: int):
        calls["n"] += 1
        return _adapter_applied(cmd, fence)

    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _command: {"ok": True},
            adapter=adapter,
            crash_phase="after_intent_before_dispatch",
        )
    assert raised.value.code == "effect_result_unknown"
    assert calls["n"] == 0
    effect_id = effect_dispatch.derive_effect_id_v1(command)
    projection = effect_dispatch.read_effect_projection_v1(
        private_root=tmp_path / "private", effect_id=effect_id
    )
    assert projection["status"] == "unknown"

    grant2, plane2 = _grant(tmp_path / "r", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as blocked:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=grant2,
            plane=plane2,
            preflight=lambda _command: {"ok": True},
            adapter=adapter,
        )
    assert blocked.value.code in {
        "effect_unknown_no_redispatch",
        "idempotency_conflict",
        "unresolved_intent",
    }
    assert calls["n"] == 0


def test_apply_then_crash_before_observation_is_unknown_reconcile_only(
    tmp_path: Path,
) -> None:
    command = _command()
    calls = {"n": 0}

    def adapter(cmd, fence: int):
        calls["n"] += 1
        return _adapter_applied(cmd, fence)

    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError):
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
            crash_phase="after_apply_before_observation",
        )
    assert calls["n"] == 1
    effect_id = effect_dispatch.derive_effect_id_v1(command)
    projection = effect_dispatch.read_effect_projection_v1(
        private_root=tmp_path / "private", effect_id=effect_id
    )
    assert projection["status"] == "unknown"
    grant2, plane2 = _grant(tmp_path / "x", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as blocked:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=grant2,
            plane=plane2,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert blocked.value.code == "effect_unknown_no_redispatch"
    assert calls["n"] == 1


def test_unknown_observation_never_redispatches(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    result = effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private",
        command=command,
        grant=grant,
        plane=plane,
        preflight=lambda _command: {"ok": True},
        adapter=_adapter_unknown,
    )
    assert result.result_classification == "unknown"
    assert result.receipt is None
    grant2, plane2 = _grant(tmp_path / "u", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=grant2,
            plane=plane2,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    assert raised.value.code == "effect_unknown_no_redispatch"


def test_kill_switch_race_after_ownership_under_dispatch_lock(tmp_path: Path) -> None:
    command = _command()
    plane = _plane(tmp_path)
    grant = effect_dispatch.issue_authority_grant_v1(command=command, plane=plane)
    calls = {"n": 0}

    def adapter(cmd, fence: int):
        calls["n"] += 1
        return _adapter_applied(cmd, fence)

    original_claim = effect_dispatch.effect_ledger.claim_effect_v1

    def claim_and_flip(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        plane.engage_kill_switch()
        return result

    effect_dispatch.effect_ledger.claim_effect_v1 = claim_and_flip  # type: ignore[assignment]
    try:
        with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
            effect_dispatch.dispatch_effect_v1(
                private_root=tmp_path / "private",
                command=command,
                grant=grant,
                plane=plane,
                preflight=lambda _command: {"ok": True},
                adapter=adapter,
            )
        assert raised.value.code == "kill_switch_engaged"
        assert calls["n"] == 0
    finally:
        effect_dispatch.effect_ledger.claim_effect_v1 = original_claim  # type: ignore[assignment]


def test_duplicate_request_digest_conflict_and_stale_fence(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    first = effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private",
        command=command,
        grant=grant,
        plane=plane,
        preflight=lambda _command: {"ok": True},
        adapter=_adapter_applied,
    )
    assert first.result_classification == "applied"
    altered = _command(payload_digest="sha256:" + ("ef" * 32), command_id="cmd-2")
    grant2, plane2 = _grant(tmp_path / "d", altered)
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=altered,
            grant=grant2,
            plane=plane2,
            preflight=lambda _command: {"ok": True},
            adapter=_adapter_applied,
        )
    assert raised.value.code == "idempotency_conflict"


def test_public_projection_propagates_corruption_errors(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    result = effect_dispatch.dispatch_effect_v1(
        private_root=tmp_path / "private",
        command=command,
        grant=grant,
        plane=plane,
        preflight=lambda _c: {"ok": True},
        adapter=_adapter_applied,
    )
    from workflow_kernel import workflow_effect_ledger as ledger

    path = ledger.effect_ledger_path_v1(
        private_root=tmp_path / "private", effect_id=result.effect_id
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    # Break previous_event_id linkage on last event.
    last = json.loads(lines[-1])
    last["previous_event_id"] = "wel_" + ("11" * 32)
    # Recompute event_id over tampered body so failure is linkage, not id mismatch alone.
    body = {k: v for k, v in last.items() if k != "event_id"}
    last["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
    lines[-1] = json.dumps(last, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.read_effect_projection_v1(
            private_root=tmp_path / "private", effect_id=result.effect_id
        )
    assert raised.value.code == "broken_link"
    # Genuine absent remains absent.
    absent = effect_dispatch.read_effect_projection_v1(
        private_root=tmp_path / "private", effect_id="effect_missing_totally"
    )
    assert absent["status"] == "absent"


def _mp_dispatch_worker(root: str, config: str, ready: Any, go: Any, counter: Any, errors: Any) -> None:
    try:
        from workflow_kernel import effect_contracts as ec
        from workflow_kernel import effect_dispatch as ed

        body = {
            "schema_version": "effect_command_v1",
            "command_id": "cmd-1",
            "adapter_id": "test.injected_adapter",
            "operation": "noop_apply",
            "target": {
                "target_kind": "injected_callable",
                "target_ref": "target/alpha",
            },
            "payload_digest": "sha256:" + ("ab" * 32),
            "idempotency_key": "dispatch-cmd-1",
            "issuer_id": "issuer.local-test",
            "policy_revision": "policy-rev-1",
            "config_revision": "config-rev-1",
            "side_effect_tier": "local_write",
            "compensation_ref": "compensation:test.restore",
            "reconcile_ref": "reconcile:test.readback",
        }
        command = ec.parse_effect_command_v1(body)
        plane = ed._test_only_compose_trusted_authority_plane_from_canonical_config_v1(
            config_path=config
        )
        grant = ed.issue_authority_grant_v1(command=command, plane=plane)

        def adapter(cmd, fence: int):
            with counter.get_lock():
                counter.value += 1
            return ed.EffectAdapterResultV1(
                classification="applied",
                adapter_result_digest="sha256:" + ("ab" * 32),
                readback={
                    "present": True,
                    "content_digest": "sha256:" + ("ab" * 32),
                    "fence": fence,
                },
            )

        ready.set()
        go.wait(timeout=10)
        try:
            ed.dispatch_effect_v1(
                private_root=root,
                command=command,
                grant=grant,
                plane=plane,
                preflight=lambda _c: {"ok": True},
                adapter=adapter,
            )
        except ed.EffectDispatchError:
            pass
    except BaseException as exc:  # pragma: no cover
        errors.append(f"{type(exc).__name__}:{exc}")


def test_two_thread_barrier_exactly_one_adapter_invocation(tmp_path: Path) -> None:
    command = _command()
    root = tmp_path / "private"
    barrier = threading.Barrier(2)
    calls = {"n": 0}
    lock = threading.Lock()
    errors: list[BaseException] = []

    def adapter(cmd, fence: int):
        with lock:
            calls["n"] += 1
        return _adapter_applied(cmd, fence)

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            grant, plane = _grant(tmp_path / f"t{threading.get_ident()}", command)
            effect_dispatch.dispatch_effect_v1(
                private_root=root,
                command=command,
                grant=grant,
                plane=plane,
                preflight=lambda _c: {"ok": True},
                adapter=adapter,
            )
        except effect_dispatch.EffectDispatchError:
            pass
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not errors
    assert calls["n"] == 1
    projection = effect_dispatch.read_effect_projection_v1(
        private_root=root, effect_id=effect_dispatch.derive_effect_id_v1(command)
    )
    assert projection["status"] == "applied"
    assert projection["fence"] == 1


def test_two_process_barrier_exactly_one_adapter_invocation(tmp_path: Path) -> None:
    root = str(tmp_path / "private")
    config = str(_canonical_config(tmp_path))
    ctx = mp.get_context("spawn")
    ready1 = ctx.Event()
    ready2 = ctx.Event()
    go = ctx.Event()
    counter = ctx.Value("i", 0)
    manager = ctx.Manager()
    errors = manager.list()
    p1 = ctx.Process(
        target=_mp_dispatch_worker, args=(root, config, ready1, go, counter, errors)
    )
    p2 = ctx.Process(
        target=_mp_dispatch_worker, args=(root, config, ready2, go, counter, errors)
    )
    p1.start()
    p2.start()
    assert ready1.wait(timeout=10)
    assert ready2.wait(timeout=10)
    go.set()
    p1.join(timeout=30)
    p2.join(timeout=30)
    assert p1.exitcode == 0
    assert p2.exitcode == 0
    assert list(errors) == []
    assert counter.value == 1
    command = _command()
    effect_id = effect_dispatch.derive_effect_id_v1(command)
    projection = effect_dispatch.read_effect_projection_v1(private_root=root, effect_id=effect_id)
    assert projection["status"] == "applied"
    assert projection["fence"] == 1
    assert projection.get("terminal_receipt") or projection.get("terminal_receipt_digest")



def test_expired_original_and_expiry_extended_replace_fail_public_dispatch(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command, ttl_seconds=0.05)
    time.sleep(0.06)
    with pytest.raises(effect_dispatch.EffectDispatchError) as expired:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private-expired",
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=_adapter_applied,
        )
    assert expired.value.code == "authority_grant_expired"

    grant2, plane2 = _grant(tmp_path / "g2", command, ttl_seconds=0.05)
    time.sleep(0.06)
    extended = dataclasses.replace(grant2, expires_at_monotonic=time.monotonic() + 30)
    with pytest.raises(effect_dispatch.EffectDispatchError) as replaced:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private-extended",
            command=command,
            grant=extended,
            plane=plane2,
            preflight=lambda _c: {"ok": True},
            adapter=_adapter_applied,
        )
    # Outside 4A.4 correction scope: expired/replaced grant identity matrix.
    assert replaced.value.code in {
        "authority_grant_not_issued_object",
        "authority_issuance_binding_mismatch",
        "authority_grant_expired",
    }


def test_public_kill_switch_has_no_reset_and_engaged_blocks_issue(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    assert not hasattr(plane, "clear_kill_switch_for_test")
    assert "clear_kill_switch_for_test" not in dir(effect_dispatch.TrustedAuthorityPlaneV1)
    plane.engage_kill_switch()
    command = _command()
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.issue_authority_grant_v1(command=command, plane=plane)
    assert raised.value.code == "kill_switch_engaged"
    # Fresh sealed plane required for recovery in tests.
    plane2 = _plane(tmp_path / "fresh")
    grant = effect_dispatch.issue_authority_grant_v1(command=command, plane=plane2)
    assert grant is not None


def test_preflight_drift_under_ownership_fails_before_adapter(tmp_path: Path) -> None:
    command = _command()
    grant, plane = _grant(tmp_path, command)
    calls = {"n": 0}

    def preflight(_c):
        calls["n"] += 1
        return {"ok": True, "n": calls["n"]}

    adapter_calls = {"n": 0}

    def adapter(cmd, fence):
        adapter_calls["n"] += 1
        return _adapter_applied(cmd, fence)

    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=tmp_path / "private",
            command=command,
            grant=grant,
            plane=plane,
            preflight=preflight,
            adapter=adapter,
        )
    assert raised.value.code == "preflight_drift"
    assert adapter_calls["n"] == 0


def test_post_intent_failures_are_unknown_and_observed_receipt_recovers(tmp_path: Path) -> None:
    command = _command()
    root = tmp_path / "private"
    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError):
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=_adapter_applied,
            crash_phase="after_observation_before_receipt",
        )
    proj = effect_dispatch.read_effect_projection_v1(private_root=root, effect_id=effect_dispatch.derive_effect_id_v1(command))
    assert proj["status"] == "observed"
    # Recovery synthesizes receipt without adapter.
    grant2, plane2 = _grant(tmp_path / "g2", command)
    recovered = effect_dispatch.recover_observed_effect_receipt_v1(
        private_root=root,
        command=command,
        plane=plane2,
        grant=grant2,
    )
    assert recovered.result_classification == "applied"
    assert recovered.receipt is not None
    proj2 = effect_dispatch.read_effect_projection_v1(private_root=root, effect_id=recovered.effect_id)
    assert proj2["status"] == "applied"


def test_recover_observed_receipt_from_immutable_projection_no_adapter_replay(tmp_path: Path) -> None:
    """Recovery must consume frozen public projection bodies without adapter replay."""
    from types import MappingProxyType

    from workflow_kernel.effect_contracts import thaw_admitted_json_v1

    command = _command()
    root = tmp_path / "private"
    grant, plane = _grant(tmp_path, command)
    adapter_calls = {"n": 0}

    def adapter(cmd, fence):
        adapter_calls["n"] += 1
        return _adapter_applied(cmd, fence)

    with pytest.raises(effect_dispatch.EffectDispatchUnknownError):
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True, "phase": "pre"},
            adapter=adapter,
            crash_phase="after_observation_before_receipt",
        )
    assert adapter_calls["n"] == 1
    proj = effect_dispatch.read_effect_projection_v1(
        private_root=root, effect_id=effect_dispatch.derive_effect_id_v1(command)
    )
    assert proj["status"] == "observed"
    assert isinstance(proj, MappingProxyType)
    assert isinstance(proj["observation"], MappingProxyType)
    assert isinstance(proj["facts"], MappingProxyType)
    assert isinstance(proj["facts"]["preflight_readback"], MappingProxyType)
    durable_obs = thaw_admitted_json_v1(proj["observation"])
    durable_preflight = thaw_admitted_json_v1(proj["facts"]["preflight_readback"])
    # Nested public projection mutation must fail closed (no mutable alias).
    with pytest.raises(TypeError):
        proj["observation"]["classification"] = "not_applied"  # type: ignore[index]
    with pytest.raises(TypeError):
        proj["facts"]["preflight_readback"]["ok"] = False  # type: ignore[index]

    grant2, plane2 = _grant(tmp_path / "g2", command)
    recovered = effect_dispatch.recover_observed_effect_receipt_v1(
        private_root=root,
        command=command,
        plane=plane2,
        grant=grant2,
    )
    assert adapter_calls["n"] == 1  # no adapter replay
    assert recovered.replayed is False
    assert recovered.result_classification == "applied"
    assert recovered.receipt is not None
    assert thaw_admitted_json_v1(recovered.receipt.preflight_readback) == durable_preflight
    assert thaw_admitted_json_v1(recovered.receipt.post_readback) == durable_obs["readback"]
    # Receipt is canonical/frozen and not a mutable alias of the public projection.
    assert recovered.receipt.preflight_readback is not proj["facts"]["preflight_readback"]
    with pytest.raises(TypeError):
        recovered.receipt.preflight_readback["ok"] = False  # type: ignore[index]
    proj2 = effect_dispatch.read_effect_projection_v1(private_root=root, effect_id=recovered.effect_id)
    assert proj2["status"] == "applied"
    assert isinstance(proj2["terminal_receipt"], MappingProxyType)
    assert thaw_admitted_json_v1(proj2["terminal_receipt"]["preflight_readback"]) == durable_preflight
    assert thaw_admitted_json_v1(proj2["terminal_receipt"]["post_readback"]) == durable_obs["readback"]


def _seed_observed_effect(tmp_path: Path):
    command = _command()
    root = tmp_path / "private"
    grant, plane = _grant(tmp_path / "seed", command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError):
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=_adapter_applied,
            crash_phase="after_observation_before_receipt",
        )
    assert effect_dispatch.read_effect_projection_v1(
        private_root=root,
        effect_id=effect_dispatch.derive_effect_id_v1(command),
    )["status"] == "observed"
    return command, root


def test_recover_observed_receipt_generic_ledger_error_is_unknown_and_no_ordinary_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, root = _seed_observed_effect(tmp_path)
    original_append = effect_dispatch.effect_ledger.append_effect_event_v1

    def ambiguous_append(**_kwargs):
        raise effect_dispatch.effect_ledger.WorkflowEffectLedgerError(
            "write_failed", "receipt append result is ambiguous"
        )

    monkeypatch.setattr(
        effect_dispatch.effect_ledger, "append_effect_event_v1", ambiguous_append
    )
    grant, plane = _grant(tmp_path / "recover", command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError) as raised:
        effect_dispatch.recover_observed_effect_receipt_v1(
            private_root=root, command=command, plane=plane, grant=grant
        )
    assert raised.value.code == "effect_result_unknown"
    monkeypatch.setattr(
        effect_dispatch.effect_ledger, "append_effect_event_v1", original_append
    )

    adapter_calls = {"n": 0}

    def forbidden_adapter(cmd, fence):
        adapter_calls["n"] += 1
        return _adapter_applied(cmd, fence)

    retry_grant, retry_plane = _grant(tmp_path / "retry", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as blocked:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=retry_grant,
            plane=retry_plane,
            preflight=lambda _c: {"ok": True},
            adapter=forbidden_adapter,
        )
    assert blocked.value.code == "effect_unknown_no_redispatch"
    assert adapter_calls["n"] == 0


def test_recover_observed_receipt_post_event_fsync_is_unknown_even_when_receipt_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, root = _seed_observed_effect(tmp_path)
    original_append = effect_dispatch.effect_ledger.append_effect_event_v1

    def append_then_report_ambiguous(**kwargs):
        original_append(**kwargs)
        raise effect_dispatch.effect_ledger.WorkflowEffectLedgerError(
            "layout_corrupt",
            "identity changed after receipt fsync",
            phase="post_event_fsync",
        )

    monkeypatch.setattr(
        effect_dispatch.effect_ledger,
        "append_effect_event_v1",
        append_then_report_ambiguous,
    )
    grant, plane = _grant(tmp_path / "recover", command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError) as raised:
        effect_dispatch.recover_observed_effect_receipt_v1(
            private_root=root, command=command, plane=plane, grant=grant
        )
    assert raised.value.code == "effect_result_unknown"
    monkeypatch.setattr(
        effect_dispatch.effect_ledger, "append_effect_event_v1", original_append
    )
    projection = effect_dispatch.read_effect_projection_v1(
        private_root=root,
        effect_id=effect_dispatch.derive_effect_id_v1(command),
    )
    assert projection["status"] == "applied"
    assert projection["terminal_receipt"] is not None


def test_intent_append_post_fsync_projection_hidden_is_unknown_without_adapter_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _command()
    root = tmp_path / "private"
    grant, plane = _grant(tmp_path / "grant", command)
    original_append = effect_dispatch.effect_ledger.append_effect_event_v1
    hidden = root / "effects.hidden"
    replacement = root / "effects"

    def append_hide_projection_then_report_ambiguous(**kwargs):
        result = original_append(**kwargs)
        if kwargs.get("event_type") == "intent_recorded":
            os.rename(replacement, hidden)
            replacement.mkdir()
            raise effect_dispatch.effect_ledger.WorkflowEffectLedgerError(
                "root_replaced",
                "intent is durable but canonical projection is hidden",
                phase="post_event_fsync",
            )
        return result

    monkeypatch.setattr(
        effect_dispatch.effect_ledger,
        "append_effect_event_v1",
        append_hide_projection_then_report_ambiguous,
    )
    adapter_calls = {"n": 0}

    def forbidden_adapter(cmd, fence):
        adapter_calls["n"] += 1
        return _adapter_applied(cmd, fence)

    try:
        with pytest.raises(effect_dispatch.EffectDispatchUnknownError) as raised:
            effect_dispatch.dispatch_effect_v1(
                private_root=root,
                command=command,
                grant=grant,
                plane=plane,
                preflight=lambda _c: {"ok": True},
                adapter=forbidden_adapter,
            )
        assert raised.value.code == "effect_result_unknown"
        assert adapter_calls["n"] == 0
    finally:
        monkeypatch.setattr(
            effect_dispatch.effect_ledger, "append_effect_event_v1", original_append
        )
        if hidden.exists():
            os.rmdir(replacement)
            os.rename(hidden, replacement)

    effect_id = effect_dispatch.derive_effect_id_v1(command)
    events = effect_dispatch.effect_ledger.read_effect_events_v1(
        private_root=root, effect_id=effect_id
    )
    assert [event["event_type"] for event in events] == ["claimed", "intent_recorded"]
    retry_grant, retry_plane = _grant(tmp_path / "retry", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as blocked:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=retry_grant,
            plane=retry_plane,
            preflight=lambda _c: {"ok": True},
            adapter=forbidden_adapter,
        )
    assert blocked.value.code == "effect_unknown_no_redispatch"
    assert adapter_calls["n"] == 0


def test_crash_before_adapter_unknown_no_duplicate_on_restart(tmp_path: Path) -> None:
    command = _command()
    root = tmp_path / "private"
    calls = {"n": 0}

    def adapter(cmd, fence):
        calls["n"] += 1
        return _adapter_applied(cmd, fence)

    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError):
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
            crash_phase="before_adapter",
        )
    assert calls["n"] == 0
    proj = effect_dispatch.read_effect_projection_v1(private_root=root, effect_id=effect_dispatch.derive_effect_id_v1(command))
    assert proj["status"] == "unknown"
    grant2, plane2 = _grant(tmp_path / "g2", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant2,
            plane=plane2,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert raised.value.code == "effect_unknown_no_redispatch"
    assert calls["n"] == 0


def test_post_intent_invalid_classification_is_unknown_no_redispatch(tmp_path: Path) -> None:
    command = _command()
    root = tmp_path / "private"
    calls = {"n": 0}

    def adapter(cmd, fence: int):
        calls["n"] += 1
        return effect_dispatch.EffectAdapterResultV1(
            classification="nope",
            adapter_result_digest=None,
            readback={"present": False},
        )

    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert raised.value.code == "effect_result_unknown"
    assert calls["n"] == 1
    effect_id = effect_dispatch.derive_effect_id_v1(command)
    proj = effect_dispatch.read_effect_projection_v1(private_root=root, effect_id=effect_id)
    assert proj["status"] == "unknown"
    assert proj.get("terminal_receipt") is None
    grant2, plane2 = _grant(tmp_path / "g2", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as blocked:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant2,
            plane=plane2,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert blocked.value.code == "effect_unknown_no_redispatch"
    assert calls["n"] == 1


def test_post_intent_malformed_readback_is_unknown_no_redispatch(tmp_path: Path) -> None:
    command = _command()
    root = tmp_path / "private"
    calls = {"n": 0}

    def adapter(cmd, fence: int):
        calls["n"] += 1
        return effect_dispatch.EffectAdapterResultV1(
            classification="applied",
            adapter_result_digest=DIGEST,
            readback=object(),  # type: ignore[arg-type]
        )

    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert raised.value.code == "effect_result_unknown"
    assert calls["n"] == 1
    effect_id = effect_dispatch.derive_effect_id_v1(command)
    proj = effect_dispatch.read_effect_projection_v1(private_root=root, effect_id=effect_id)
    assert proj["status"] == "unknown"
    grant2, plane2 = _grant(tmp_path / "g2", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as blocked:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant2,
            plane=plane2,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert blocked.value.code == "effect_unknown_no_redispatch"
    assert calls["n"] == 1


def test_post_intent_adapter_exception_is_unknown_no_redispatch(tmp_path: Path) -> None:
    command = _command()
    root = tmp_path / "private"
    calls = {"n": 0}

    def adapter(cmd, fence: int):
        calls["n"] += 1
        raise RuntimeError("adapter boom")

    grant, plane = _grant(tmp_path, command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError):
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant,
            plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert calls["n"] == 1
    grant2, plane2 = _grant(tmp_path / "g2", command)
    with pytest.raises(effect_dispatch.EffectDispatchError) as blocked:
        effect_dispatch.dispatch_effect_v1(
            private_root=root,
            command=command,
            grant=grant2,
            plane=plane2,
            preflight=lambda _c: {"ok": True},
            adapter=adapter,
        )
    assert blocked.value.code == "effect_unknown_no_redispatch"
    assert calls["n"] == 1


def _seed_terminal_tree(path: Path, command) -> None:
    grant, plane = _grant(path / "authority", command)
    result = effect_dispatch.dispatch_effect_v1(
        private_root=path, command=command, grant=grant, plane=plane,
        preflight=lambda _c: {"ok": True}, adapter=_adapter_applied,
    )
    assert result.result_classification == "applied"


def _swap_root_after_ownership(monkeypatch, root: Path, replacement: Path):
    original = effect_dispatch.effect_ledger.hold_target_ownership_v1
    held = root.with_name(root.name + ".held")

    @contextmanager
    def swapped(**kwargs):
        with original(**kwargs) as ownership:
            root.rename(held)
            replacement.rename(root)
            yield ownership

    monkeypatch.setattr(effect_dispatch.effect_ledger, "hold_target_ownership_v1", swapped)
    return held


def test_owned_dispatch_never_returns_forged_terminal_from_replacement_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _command()
    root = tmp_path / "private"
    replacement = tmp_path / "forged-terminal"
    _seed_terminal_tree(replacement, command)
    grant, plane = _grant(tmp_path / "dispatch-authority", command)
    held = _swap_root_after_ownership(monkeypatch, root, replacement)
    with pytest.raises(effect_dispatch.EffectDispatchError) as raised:
        effect_dispatch.dispatch_effect_v1(
            private_root=root, command=command, grant=grant, plane=plane,
            preflight=lambda _c: {"ok": True},
            adapter=lambda *_args: pytest.fail("replacement terminal must not authorize return"),
        )
    assert raised.value.code == "root_replaced"
    assert held.exists()


def test_owned_recovery_never_returns_forged_terminal_from_replacement_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _command()
    root = tmp_path / "private"
    seed_grant, seed_plane = _grant(tmp_path / "seed-authority", command)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError):
        effect_dispatch.dispatch_effect_v1(
            private_root=root, command=command, grant=seed_grant, plane=seed_plane,
            preflight=lambda _c: {"ok": True}, adapter=_adapter_applied,
            crash_phase="after_observation_before_receipt",
        )
    replacement = tmp_path / "forged-terminal"
    _seed_terminal_tree(replacement, command)
    grant, plane = _grant(tmp_path / "recover-authority", command)
    held = _swap_root_after_ownership(monkeypatch, root, replacement)
    with pytest.raises(effect_dispatch.EffectDispatchUnknownError) as raised:
        effect_dispatch.recover_observed_effect_receipt_v1(
            private_root=root, command=command, plane=plane, grant=grant
        )
    assert raised.value.code == "effect_result_unknown"
    assert held.exists()
