"""Slice 2: subordinate durable workflow effect ledger."""

from __future__ import annotations

import copy
import json
import multiprocessing as mp
import os
import inspect
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from workflow_kernel import effect_contracts
from workflow_kernel import workflow_effect_ledger as ledger


DIGEST = "sha256:" + ("11" * 32)
OTHER_DIGEST = "sha256:" + ("22" * 32)
TARGET = {"target_kind": "injected_callable", "target_ref": "target/alpha"}
TARGET_DIGEST = effect_contracts.canonical_digest_v1(TARGET)


def _comp_preflight() -> dict:
    return {"compensation_ok": True}


def _compensation_intent_payload(original: dict, fence: int = 1, **overrides: object) -> dict:
    preflight = _comp_preflight()
    payload = {
        "original_receipt_digest": original["receipt_digest"],
        "original_receipt": original,
        "compensation_ref": "compensation:test.restore",
        "request_digest": OTHER_DIGEST,
        "command_digest": DIGEST,
        "target_digest": TARGET_DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "effect_id": "effect-1",
        "fence": fence,
        "authority_binding_digest": DIGEST,
        "preflight_readback": preflight,
        "preflight_readback_digest": effect_contracts.canonical_digest_v1(preflight),
    }
    payload.update(overrides)
    return payload


def _compensation_observation_payload(
    original: dict, comp_obs: dict, fence: int = 1, **overrides: object
) -> dict:
    readback = comp_obs["readback"]
    payload = {
        "classification": comp_obs["classification"],
        "observation": comp_obs,
        "readback": readback,
        "readback_digest": effect_contracts.canonical_digest_v1(readback),
        "original_receipt_digest": original["receipt_digest"],
        "original_receipt": original,
        "fence": fence,
        "command_digest": DIGEST,
        "request_digest": OTHER_DIGEST,
        "target_digest": TARGET_DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "effect_id": "effect-1",
        "authority_binding_digest": DIGEST,
    }
    payload.update(overrides)
    if "readback" in payload and isinstance(payload["readback"], dict) and "readback_digest" not in overrides:
        payload["readback_digest"] = effect_contracts.canonical_digest_v1(payload["readback"])
        if isinstance(payload.get("observation"), dict):
            payload["observation"] = dict(payload["observation"])
            payload["observation"]["readback"] = payload["readback"]
    return payload


def _compensation_receipt_dict(
    *, fence: int = 1, classification: str = "applied", preflight: dict | None = None, post: dict | None = None
) -> dict:
    body = {
        "schema_version": "effect_receipt_v1",
        "effect_id": "effect-1",
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "target": dict(TARGET),
        "payload_digest": DIGEST,
        "authority_binding_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "fence": fence,
        "preflight_readback": dict(preflight or _comp_preflight()),
        "post_readback": dict(post or {"present": True, "content_digest": DIGEST}),
        "result_classification": classification,
        "compensation_ref": "compensation:test.restore",
        "reconcile_ref": "reconcile:test.readback",
    }
    digest = effect_contracts.canonical_digest_v1(body)
    return {**body, "receipt_digest": digest}



def _paths(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "private"
    return root, root / "effects" / "effect-1.jsonl"


def _claim_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "command_digest": DIGEST,
        "target_digest": TARGET_DIGEST,
        "owner": "owner-a",
        "request_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "effect_id": "effect-1",
    }
    payload.update(overrides)
    return payload


def _intent_payload(fence: int = 1, **overrides: object) -> dict[str, object]:
    preflight = {"ok": True}
    payload = {
        "fence": fence,
        "command_digest": DIGEST,
        "request_digest": DIGEST,
        "target_digest": TARGET_DIGEST,
        "authority_binding_digest": DIGEST,
        "preflight_readback": preflight,
        "preflight_readback_digest": effect_contracts.canonical_digest_v1(preflight),
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "idempotency_key": str(overrides.get("idempotency_key", "effect-cmd-1")),
        "effect_id": str(overrides.get("effect_id", "effect-1")),
    }
    payload.update(overrides)
    return payload


def _reconcile_payload(
    fence: int = 1,
    classification: str = "applied",
    *,
    readback: dict | None = None,
    **overrides: object,
) -> dict[str, object]:
    if readback is None:
        if classification == "applied":
            readback = {"present": True, "content_digest": DIGEST}
        else:
            readback = {"present": False, "content_digest": DIGEST}
    payload: dict[str, object] = {
        "classification": classification,
        "fence": fence,
        "command_digest": DIGEST,
        "request_digest": DIGEST,
        "target_digest": TARGET_DIGEST,
        "evidence_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "effect_id": "effect-1",
        "authority_binding_digest": DIGEST,
        "readback": dict(readback),
        "readback_digest": effect_contracts.canonical_digest_v1(readback),
    }
    payload.update(overrides)
    if "readback" in payload and isinstance(payload["readback"], dict) and "readback_digest" not in overrides:
        payload["readback_digest"] = effect_contracts.canonical_digest_v1(payload["readback"])
    return payload


def _observation_payload(fence: int = 1, classification: str = "applied", **overrides: object) -> dict[str, object]:
    effect_id = str(overrides.get("effect_id", "effect-1"))
    idemp = str(overrides.get("idempotency_key", "effect-cmd-1"))
    obs = _observation_dict(effect_id=effect_id, fence=fence, classification=classification)
    if "observation" in overrides and isinstance(overrides["observation"], dict):
        obs = overrides["observation"]  # type: ignore[assignment]
    if "readback" in overrides and isinstance(overrides["readback"], dict):
        obs = dict(obs)
        obs["readback"] = overrides["readback"]  # type: ignore[index]
    readback = obs["readback"]
    payload = {
        "fence": fence,
        "classification": classification,
        "adapter_result_digest": DIGEST if classification != "unknown" else None,
        "readback": readback,
        "readback_digest": effect_contracts.canonical_digest_v1(readback),
        "observation": obs,
        "command_digest": DIGEST,
        "request_digest": DIGEST,
        "target_digest": TARGET_DIGEST,
        "authority_binding_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "idempotency_key": idemp,
        "effect_id": effect_id,
    }
    payload.update(overrides)
    # Keep readback digest coherent after overrides.
    if "readback" in payload and isinstance(payload["readback"], dict):
        if "observation" in payload and isinstance(payload["observation"], dict):
            payload["observation"] = dict(payload["observation"])
            payload["observation"]["readback"] = payload["readback"]
        if "readback_digest" not in overrides:
            payload["readback_digest"] = effect_contracts.canonical_digest_v1(payload["readback"])
    return payload


def _receipt_payload(fence: int = 1, classification: str = "applied", **overrides: object) -> dict[str, object]:
    effect_id = str(overrides.get("effect_id", "effect-1"))
    idemp = str(overrides.get("idempotency_key", "effect-cmd-1"))
    receipt = _receipt_dict(effect_id=effect_id, classification=classification, fence=fence)
    # Align receipt body idempotency with claim when overridden.
    if idemp != receipt.get("idempotency_key"):
        body = dict(receipt)
        del body["receipt_digest"]
        body["idempotency_key"] = idemp
        body["receipt_digest"] = effect_contracts.canonical_digest_v1(body)
        receipt = body
    obs = _observation_dict(effect_id=effect_id, fence=fence, classification=classification)
    if "observation" in overrides and isinstance(overrides["observation"], dict):
        obs = overrides["observation"]  # type: ignore[assignment]
    payload = {
        "fence": fence,
        "result_classification": classification,
        "receipt_digest": receipt["receipt_digest"],
        "command_digest": DIGEST,
        "request_digest": DIGEST,
        "target_digest": TARGET_DIGEST,
        "authority_binding_digest": DIGEST,
        "observation_digest": effect_contracts.canonical_digest_v1(obs),
        "receipt": receipt,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "idempotency_key": idemp,
        "effect_id": effect_id,
        "observation": obs,
    }
    payload.update(overrides)
    # Keep receipt/observation digests coherent after overrides.
    if "receipt" in payload and isinstance(payload["receipt"], dict):
        payload["receipt_digest"] = payload["receipt"].get("receipt_digest", payload["receipt_digest"])
    if "observation" in payload and isinstance(payload["observation"], dict):
        payload["observation_digest"] = effect_contracts.canonical_digest_v1(payload["observation"])
    return payload


def _receipt_dict(
    *, effect_id: str = "effect-1", classification: str = "applied", fence: int = 1
) -> dict:
    if classification == "applied":
        post = {"present": True, "content_digest": DIGEST}
    elif classification == "not_applied":
        post = {"present": False, "content_digest": DIGEST}
    else:
        post = {"present": False, "content_digest": None}
    body = {
        "schema_version": "effect_receipt_v1",
        "effect_id": effect_id,
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "target": dict(TARGET),
        "payload_digest": DIGEST,
        "authority_binding_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "fence": fence,
        "preflight_readback": {"ok": True},
        "post_readback": post,
        "result_classification": classification,
        "compensation_ref": "compensation:test.restore",
        "reconcile_ref": "reconcile:test.readback",
    }
    digest = effect_contracts.canonical_digest_v1(body)
    return {**body, "receipt_digest": digest}


def _observation_dict(
    *, effect_id: str = "effect-1", classification: str = "applied", fence: int = 1
) -> dict:
    return {
        "schema_version": "effect_observation_v1",
        "effect_id": effect_id,
        "observation_id": "obs-1",
        "classification": classification,
        "fence": fence,
        "observed_at": "2026-08-02T00:00:02Z",
        "adapter_result_digest": DIGEST if classification != "unknown" else None,
        "readback": {"present": classification == "applied", "content_digest": DIGEST},
    }


def test_claim_allocates_monotonic_target_scoped_fence(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    first = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    second = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-2",
        payload=_claim_payload(idempotency_key="effect-cmd-2", owner="owner-b"),
        idempotency_key="effect-2:claim",
        occurred_at="2026-08-02T00:00:01Z",
    )
    assert first["fence"] == 1
    assert second["fence"] == 2
    assert first["status"] == "claimed"
    projection = ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    assert projection["status"] == "claimed"
    assert "workflow_status" not in projection
    assert projection["fence"] == 1


def test_identical_idempotency_replays_and_conflict_fails_closed(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    first = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    replay = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    assert replay["event_id"] == first["event_id"]
    assert replay["replayed"] is True

    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-9",
            payload=_claim_payload(request_digest=OTHER_DIGEST),
            idempotency_key="effect-1:claim",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert raised.value.code == "idempotency_conflict"


def test_record_intent_observation_receipt_and_compensation_facts(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    intent = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    assert intent["status"] == "unknown"
    observation = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    assert observation["status"] == "observed"
    receipt = _receipt_dict()
    terminal = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )
    assert terminal["status"] == "applied"
    compensation = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_intent",
        payload=_compensation_intent_payload(receipt, fence=int(claim["fence"])),
        idempotency_key="effect-1:comp-intent",
        occurred_at="2026-08-02T00:00:04Z",
    )
    assert compensation["status"] == "compensation_pending"
    projection = ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    assert projection["status"] == "compensation_pending"
    assert projection["terminal_receipt_digest"] == receipt["receipt_digest"]
    assert projection["terminal_receipt"]["receipt_digest"] == receipt["receipt_digest"]


def test_state_machine_allowed_and_forbidden_edges(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    table = ledger.allowed_effect_transitions_v1()
    assert "claimed" in table["absent"]
    assert "receipt_terminal" not in table["claimed"]
    assert table["unknown"] == ("reconciled",)

    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    # claimed -> receipt_terminal forbidden (schema-complete payload; transition invariant)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as bad:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="receipt_terminal",
            payload=_receipt_payload(fence=int(claim["fence"])),
            idempotency_key="effect-1:bad-receipt",
            occurred_at="2026-08-02T00:00:01Z",
        )
    assert bad.value.code == "invalid_transition"

    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=int(claim["fence"]), classification="unknown"),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    # unknown only accepts reconcile
    with pytest.raises(ledger.WorkflowEffectLedgerError) as unk:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="intent_recorded",
            payload=_intent_payload(fence=int(claim["fence"])),
            idempotency_key="effect-1:intent2",
            occurred_at="2026-08-02T00:00:03Z",
        )
    assert unk.value.code == "invalid_transition"

    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="reconciled",
        payload=_reconcile_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:reconcile",
        occurred_at="2026-08-02T00:00:03Z",
    )
    # Reconcile relaxes classification only; embedded observation body must remain the
    # durable unknown observation, while receipt/post_readback follow reconcile.
    proj = ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    durable_readback = effect_contracts.thaw_admitted_json_v1(proj["facts"]["readback"])
    receipt_body = _receipt_dict(fence=int(claim["fence"]), classification="applied")
    del receipt_body["receipt_digest"]
    receipt_body["post_readback"] = dict(durable_readback)
    receipt_body["receipt_digest"] = effect_contracts.canonical_digest_v1(receipt_body)
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(
            fence=int(claim["fence"]),
            classification="applied",
            receipt=receipt_body,
            receipt_digest=receipt_body["receipt_digest"],
            observation=_observation_dict(classification="unknown", fence=int(claim["fence"])),
        ),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:04Z",
    )
    # compensation with wrong receipt digest fails
    with pytest.raises(ledger.WorkflowEffectLedgerError) as comp:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="compensation_intent",
            payload=_compensation_intent_payload(
                _receipt_dict(),
                fence=int(claim["fence"]),
                original_receipt_digest=OTHER_DIGEST,
            ),
            idempotency_key="effect-1:comp-bad",
            occurred_at="2026-08-02T00:00:05Z",
        )
    assert comp.value.code == "payload_invalid"


def test_torn_tail_and_fsync_ambiguity_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _paths(tmp_path)
    ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    raw = path.read_bytes()
    path.write_bytes(raw + b'{"schema_version":"workflow_effect_ledger_event_v1"')
    with pytest.raises(ledger.WorkflowEffectLedgerError) as torn:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert torn.value.code == "torn_tail"

    def boom(descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(ledger, "_fsync_descriptor", boom)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-2",
            payload=_claim_payload(idempotency_key="effect-cmd-2"),
            idempotency_key="effect-2:claim",
            occurred_at="2026-08-02T00:00:05Z",
        )
    assert raised.value.code == "durability_unknown"


def test_symlink_hardlink_and_path_replacement_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "private"
    outside = tmp_path / "outside.jsonl"
    outside.write_text("secret\n", encoding="utf-8")
    effects = root / "effects"
    effects.mkdir(parents=True)

    symlink_path = ledger.effect_ledger_path_v1(
        private_root=root, effect_id="effect-symlink"
    )
    symlink_path.symlink_to(outside)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-symlink")
    assert raised.value.code in {"unsafe_path", "unsafe_target"}

    hard_path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-hard")
    os.link(outside, hard_path)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-hard")
    assert raised.value.code in {"unsafe_path", "unsafe_target"}


def test_dynamic_root_replacement_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "private"
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    # Replace root directory identity while holding a pinned identity path name.
    replaced = tmp_path / "private-replaced"
    root.rename(replaced)
    root.mkdir()
    # New empty root with same path string but different inode.
    # Fence identity invariant: wrong target_digest under the correct fence name fails.
    fence_path = root / "fences"
    fence_path.mkdir(parents=True)
    bad_fence = {
        "schema_version": "workflow_effect_target_fence_v1",
        "target_digest": OTHER_DIGEST,
        "fence": 9,
    }
    fence_name = __import__("hashlib").sha256(TARGET_DIGEST.encode("utf-8")).hexdigest()
    (fence_path / f"{fence_name}.json").write_text(json.dumps(bad_fence), encoding="utf-8")
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-2",
            payload=_claim_payload(idempotency_key="effect-cmd-2"),
            idempotency_key="effect-2:claim",
            occurred_at="2026-08-02T00:00:01Z",
        )
    assert raised.value.code in {"fence_identity_mismatch", "fence_corrupt", "unsafe_path", "layout_corrupt"}
    assert claim["fence"] == 1


def test_stale_fence_is_rejected(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="intent_recorded",
            payload=_intent_payload(fence=int(int(claim["fence"]) + 7)),
            idempotency_key="effect-1:intent",
            occurred_at="2026-08-02T00:00:01Z",
        )
    assert raised.value.code == "stale_fence"


def test_ledger_never_projects_workflow_status_fields(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    projection = ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    forbidden = {
        "workflow_status",
        "run_status",
        "run_completed",
        "accepted",
        "running",
        "done",
    }
    assert forbidden.isdisjoint(projection)
    assert forbidden.isdisjoint(projection.get("facts", {}))


def test_concurrent_claims_on_same_target_are_totally_ordered(tmp_path: Path) -> None:
    root = tmp_path / "private"
    barriers = threading.Barrier(2)
    results: list[int] = []
    errors: list[BaseException] = []

    def worker(effect_id: str, key: str) -> None:
        try:
            barriers.wait(timeout=5)
            result = ledger.claim_effect_v1(
                private_root=root,
                effect_id=effect_id,
                payload=_claim_payload(idempotency_key=key, owner=effect_id),
                idempotency_key=f"{effect_id}:claim",
                occurred_at="2026-08-02T00:00:00Z",
            )
            results.append(int(result["fence"]))
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("effect-a", "cmd-a")),
        threading.Thread(target=worker, args=("effect-b", "cmd-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    assert sorted(results) == [1, 2]


def _mp_idemp_worker(root: str, key: str, digest: str, effect_id: str, ready, go, results) -> None:
    try:
        from workflow_kernel import effect_contracts as ec
        from workflow_kernel import workflow_effect_ledger as led

        target = {"target_kind": "injected_callable", "target_ref": "target/alpha"}
        target_digest = ec.canonical_digest_v1(target)
        ready.set()
        go.wait(timeout=10)
        try:
            result = led.claim_effect_v1(
                private_root=root,
                effect_id=effect_id,
                payload={
                    "command_digest": digest,
                    "target_digest": target_digest,
                    "owner": effect_id,
                    "request_digest": digest,
                    "idempotency_key": key,
                    "adapter_id": "test.injected_adapter",
                    "operation": "noop_apply",
                    "payload_digest": digest,
                    "effect_id": effect_id,
                },
                idempotency_key=f"{effect_id}:claim",
                occurred_at="2026-08-02T00:00:00Z",
            )
            results.append({"ok": True, "effect_id": result.get("effect_id"), "status": result.get("status")})
        except led.WorkflowEffectLedgerError as exc:
            results.append({"ok": False, "code": exc.code, "message": str(exc)})
        except Exception as exc:  # pragma: no cover
            results.append({"ok": False, "code": f"exc:{type(exc).__name__}", "message": str(exc)})
    except BaseException as exc:  # pragma: no cover
        results.append({"ok": False, "code": f"exc:{type(exc).__name__}:{exc}"})


def test_multiprocess_conflicting_idempotency_same_key_different_request(tmp_path: Path) -> None:
    root = str(tmp_path / "private")
    ctx = mp.get_context("spawn")
    ready1, ready2 = ctx.Event(), ctx.Event()
    go = ctx.Event()
    manager = ctx.Manager()
    results = manager.list()
    p1 = ctx.Process(
        target=_mp_idemp_worker,
        args=(root, "shared-key", DIGEST, "effect-a", ready1, go, results),
    )
    p2 = ctx.Process(
        target=_mp_idemp_worker,
        args=(root, "shared-key", OTHER_DIGEST, "effect-b", ready2, go, results),
    )
    p1.start()
    p2.start()
    assert ready1.wait(10) and ready2.wait(10)
    go.set()
    p1.join(30)
    p2.join(30)
    assert p1.exitcode == 0 and p2.exitcode == 0
    outcomes = list(results)
    assert len(outcomes) == 2
    oks = [o for o in outcomes if o.get("ok")]
    fails = [o for o in outcomes if not o.get("ok")]
    assert len(oks) == 1
    assert len(fails) == 1
    assert fails[0]["code"] == "idempotency_conflict"


def test_index_repair_after_claim_without_index(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    first = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    # Delete index to simulate crash between claim and index publication.
    index_dir = root / "indexes" / "idempotency"
    for path in index_dir.glob("*.json"):
        path.unlink()
    replay = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    assert replay["replayed"] is True
    assert replay["event_id"] == first["event_id"]
    # Conflicting digest still fails after repair.
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-x",
            payload=_claim_payload(request_digest=OTHER_DIGEST),
            idempotency_key="effect-1:claim",
            occurred_at="2026-08-02T00:00:09Z",
        )
    assert raised.value.code == "idempotency_conflict"


def test_cross_process_root_rename_during_owned_append(tmp_path: Path) -> None:
    root = tmp_path / "private"
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    # Holding ownership, rename root out from under the path and ensure fail-closed.
    # The operation rejects immediately and ownership performs a second fail-closed
    # identity check while leaving the context.
    with pytest.raises(ledger.WorkflowEffectLedgerError) as exit_raised:
        with ledger.hold_target_ownership_v1(
            private_root=root, target_digest=DIGEST
        ) as ownership:
            relocated = tmp_path / "moved-private"
            root.rename(relocated)
            with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
                ledger.append_effect_event_v1(
                    private_root=root,
                    effect_id="effect-1",
                    event_type="intent_recorded",
                    payload=_intent_payload(fence=int(claim["fence"])),
                    idempotency_key="effect-1:intent",
                    occurred_at="2026-08-02T00:00:01Z",
                    ownership=ownership,
                )
            assert raised.value.code in {"root_replaced", "unsafe_path", "unsafe_target"}
    assert exit_raised.value.code == "root_replaced"


def test_empty_intent_and_wrong_effect_fence_observation_rejected(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    with pytest.raises(ledger.WorkflowEffectLedgerError) as empty:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="intent_recorded",
            payload={},
            idempotency_key="effect-1:empty-intent",
            occurred_at="2026-08-02T00:00:01Z",
        )
    assert empty.value.code == "payload_invalid"
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    # One-field effect_id mutation rejects with exact payload_invalid.
    bad_effect = _observation_dict(effect_id="effect-OTHER", fence=int(claim["fence"]))
    with pytest.raises(ledger.WorkflowEffectLedgerError) as wrong_effect:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="observation_recorded",
            payload=_observation_payload(
                fence=int(claim["fence"]),
                observation=bad_effect,
                classification="applied",
            ),
            idempotency_key="effect-1:bad-obs-effect",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert wrong_effect.value.code == "payload_invalid"
    # One-field fence mutation rejects with exact stale_fence.
    # NOTE(4A.5B): broader semantic-relation one-field matrix (wrapper vs embedded fence
    # isolation completeness) is deferred to slice 4A.5B; do not broad-set here.
    bad_fence = _observation_dict(effect_id="effect-1", fence=99)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as wrong_fence:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="observation_recorded",
            payload=_observation_payload(fence=99, observation=bad_fence, classification="applied"),
            idempotency_key="effect-1:bad-obs-fence",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert wrong_fence.value.code == "stale_fence"


def test_applied_observation_to_not_applied_receipt_rejected(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    obs = _observation_dict(classification="applied", fence=int(claim["fence"]))
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    receipt = _receipt_dict(classification="not_applied")
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="receipt_terminal",
            payload=_receipt_payload(
                fence=int(claim["fence"]),
                classification="not_applied",
                receipt=receipt,
                receipt_digest=receipt["receipt_digest"],
                result_classification="not_applied",
                observation=_observation_dict(classification="not_applied", fence=int(claim["fence"])),
            ),
            idempotency_key="effect-1:receipt",
            occurred_at="2026-08-02T00:00:03Z",
        )
    assert raised.value.code == "payload_invalid"


def test_compensation_exact_original_receipt_chain(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    receipt = _receipt_dict()
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_intent",
        payload=_compensation_intent_payload(receipt, fence=int(claim["fence"])),
        idempotency_key="effect-1:comp-intent",
        occurred_at="2026-08-02T00:00:04Z",
    )
    comp_obs = _observation_dict(classification="applied", fence=int(claim["fence"]))
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_observation",
        payload=_compensation_observation_payload(receipt, comp_obs, fence=int(claim["fence"])),
        idempotency_key="effect-1:comp-obs",
        occurred_at="2026-08-02T00:00:05Z",
    )
    # Compensation receipt body must bind exactly to wrapper/intent (including idempotency).
    body = _compensation_receipt_dict(fence=int(claim["fence"]), post=comp_obs["readback"])
    terminal = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_receipt",
        payload={
            "receipt_digest": body["receipt_digest"],
            "receipt": body,
            "original_receipt_digest": receipt["receipt_digest"],
            "original_receipt": _as_plain(receipt),
            "fence": int(claim["fence"]),
            "command_digest": DIGEST,
            "request_digest": OTHER_DIGEST,
            "target_digest": TARGET_DIGEST,
            "adapter_id": "test.injected_adapter",
            "operation": "noop_apply",
            "payload_digest": DIGEST,
            "idempotency_key": "effect-cmd-1",
            "effect_id": "effect-1",
            "authority_binding_digest": DIGEST,
            "observation_digest": effect_contracts.canonical_digest_v1(comp_obs),
            "observation": comp_obs,
        },
        idempotency_key="effect-1:comp-receipt",
        occurred_at="2026-08-02T00:00:06Z",
    )
    assert terminal["status"] == "compensated"


def test_idempotency_crash_gap_process_b_different_request_fails(tmp_path: Path) -> None:
    """Process A claims then crashes before index bind; B different request must fail."""
    root, _ = _paths(tmp_path)
    # Simulate A: write pending then claim event, then delete bound index and leave pending only
    # by crafting: claim fully then replace index with pending-only without event_id.
    first = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-a",
        payload=_claim_payload(idempotency_key="shared-key"),
        idempotency_key="effect-a:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    # Crash after claim before bind leaves pending key ownership (not absent index).
    # Simulate that exact residue: pending record for A's request remains, no bound event_id.
    pending = {
        "schema_version": "workflow_effect_idempotency_index_v1",
        "idempotency_key": "shared-key",
        "request_digest": DIGEST,
        "effect_id": "effect-a",
        "target_digest": TARGET_DIGEST,
        "state": "pending",
        "event_id": None,
        "fence": None,
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
    }
    # write via claim path internals
    from workflow_kernel.workflow_effect_ledger import (
        _idempotency_index_path,
        _compact_json,
    )
    from workflow_kernel.runtime_state import write_private_file_atomic

    path = _idempotency_index_path(root, "shared-key")
    write_private_file_atomic(path, _compact_json(pending) + b"\n", private_root=root)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as b_err:
        ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-b",
            payload=_claim_payload(
                idempotency_key="shared-key",
                request_digest=OTHER_DIGEST,
                owner="owner-b",
            ),
            idempotency_key="effect-b:claim",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert b_err.value.code == "idempotency_conflict"
    # No second claim/fence for effect-b
    proj_b = ledger.project_effect_v1(private_root=root, effect_id="effect-b")
    assert proj_b["status"] == "absent"
    proj_a = ledger.project_effect_v1(private_root=root, effect_id="effect-a")
    assert proj_a["status"] == "claimed"
    assert proj_a["fence"] == first["fence"]


def _mp_replace_child(root: str, child: str, ready, go, result) -> None:
    import os
    import shutil
    from pathlib import Path
    try:
        root_path = Path(root)
        ready.set()
        go.wait(timeout=10)
        target = root_path / child
        relocated = root_path / f"{child}.moved"
        replacement = root_path / f"{child}.new"
        replacement.mkdir(parents=True, exist_ok=True)
        if target.exists():
            os.rename(target, relocated)
        os.rename(replacement, target)
        result.append({"ok": True, "child": child})
    except BaseException as exc:  # pragma: no cover
        result.append({"ok": False, "error": f"{type(exc).__name__}:{exc}"})


def test_two_process_child_directory_replacement_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "private"
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    for child in ("locks", "effects", "fences", "indexes"):
        ctx = mp.get_context("spawn")
        ready, go = ctx.Event(), ctx.Event()
        manager = ctx.Manager()
        results = manager.list()
        proc = ctx.Process(
            target=_mp_replace_child, args=(str(root), child, ready, go, results)
        )
        with pytest.raises(ledger.WorkflowEffectLedgerError) as exit_raised:
            with ledger.hold_target_ownership_v1(
                private_root=root, target_digest=DIGEST
            ) as ownership:
                proc.start()
                assert ready.wait(10)
                go.set()
                proc.join(10)
                assert proc.exitcode == 0
                with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
                    ledger.append_effect_event_v1(
                        private_root=root,
                        effect_id="effect-1",
                        event_type="intent_recorded",
                        payload=_intent_payload(fence=int(claim["fence"])),
                        idempotency_key=f"effect-1:intent-{child}",
                        occurred_at="2026-08-02T00:00:01Z",
                        ownership=ownership,
                    )
                assert raised.value.code == "root_replaced"
        assert exit_raised.value.code == "root_replaced"
        # restore tree for next child by recreating from claim root if needed
        # After replacement, root child is new empty dir; recreate required dirs via new claim root pin
        for name in ("locks", "effects", "fences", "indexes"):
            (root / name).mkdir(parents=True, exist_ok=True)
        seal = root / ".hgwk_effect_layout_v1.json"
        if seal.exists():
            seal.unlink()


def test_index_event_id_syntax_and_claim_binding_validation(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    first = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    from workflow_kernel.workflow_effect_ledger import _idempotency_index_path
    from workflow_kernel.runtime_state import write_private_file_atomic
    import json as _json
    path = _idempotency_index_path(root, "effect-cmd-1")
    bad = {
        "schema_version": "workflow_effect_idempotency_index_v1",
        "idempotency_key": "effect-cmd-1",
        "request_digest": DIGEST,
        "effect_id": "effect-OTHER",
        "target_digest": TARGET_DIGEST,
        "state": "bound",
        "event_id": "not-an-event-id",
        "fence": 1,
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
    }
    write_private_file_atomic(path, _json.dumps(bad, separators=(",", ":"), sort_keys=True).encode() + b"\n", private_root=root)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-1",
            payload=_claim_payload(),
            idempotency_key="effect-1:claim",
            occurred_at="2026-08-02T00:00:00Z",
        )
    assert raised.value.code == "index_corrupt"
    assert first["event_id"].startswith("wel_")


def _advance_to_applied(root: Path, effect_id: str = "effect-1") -> dict:
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id=effect_id,
        payload=_claim_payload(effect_id=effect_id),
        idempotency_key=f"{effect_id}:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id=effect_id,
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence, effect_id=effect_id),
        idempotency_key=f"{effect_id}:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id=effect_id,
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence, effect_id=effect_id),
        idempotency_key=f"{effect_id}:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    return ledger.append_effect_event_v1(
        private_root=root,
        effect_id=effect_id,
        event_type="receipt_terminal",
        payload=_receipt_payload(fence=fence, effect_id=effect_id),
        idempotency_key=f"{effect_id}:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )


def test_public_append_rejects_codex_invalid_semantic_payloads(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    # INTENT_WITHOUT_TARGET
    with pytest.raises(ledger.WorkflowEffectLedgerError) as e1:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="intent_recorded",
            payload={
                "fence": fence,
                "command_digest": DIGEST,
                "request_digest": DIGEST,
                "authority_binding_digest": DIGEST,
                "preflight_readback": {"ok": True},
            },
            idempotency_key="effect-1:bad-intent",
            occurred_at="2026-08-02T00:00:01Z",
        )
    assert e1.value.code == "payload_invalid"
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    # RECONCILE_WITHOUT_BINDINGS
    with pytest.raises(ledger.WorkflowEffectLedgerError) as e2:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="reconciled",
            payload={"classification": "applied"},
            idempotency_key="effect-1:bad-rec",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert e2.value.code == "payload_invalid"
    # observation then SEMANTICALLY_WRONG_RECEIPT
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    bad_receipt = _receipt_dict(classification="not_applied")
    with pytest.raises(ledger.WorkflowEffectLedgerError) as e3:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="receipt_terminal",
            payload=_receipt_payload(fence=fence, classification="not_applied", receipt=bad_receipt, receipt_digest=bad_receipt["receipt_digest"], result_classification="not_applied"),
            idempotency_key="effect-1:bad-receipt",
            occurred_at="2026-08-02T00:00:03Z",
        )
    assert e3.value.code == "payload_invalid"
    terminal = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(fence=fence),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )
    assert terminal["status"] == "applied"
    # COMPENSATE_NOT_APPLIED on a separate not_applied effect
    root2 = tmp_path / "private2"
    claim2 = ledger.claim_effect_v1(
        private_root=root2,
        effect_id="effect-na",
        payload=_claim_payload(effect_id="effect-na", idempotency_key="na-key"),
        idempotency_key="effect-na:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    f2 = int(claim2["fence"])
    ledger.append_effect_event_v1(
        private_root=root2,
        effect_id="effect-na",
        event_type="intent_recorded",
        payload=_intent_payload(fence=f2, effect_id="effect-na", idempotency_key="na-key"),
        idempotency_key="effect-na:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root2,
        effect_id="effect-na",
        event_type="observation_recorded",
        payload=_observation_payload(fence=f2, classification="not_applied", effect_id="effect-na", idempotency_key="na-key"),
        idempotency_key="effect-na:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    obs_na = _observation_dict(effect_id="effect-na", classification="not_applied", fence=f2)
    na_payload = _receipt_payload(
        fence=f2,
        classification="not_applied",
        effect_id="effect-na",
        idempotency_key="na-key",
        observation=obs_na,
    )
    na_receipt = na_payload["receipt"]
    ledger.append_effect_event_v1(
        private_root=root2,
        effect_id="effect-na",
        event_type="receipt_terminal",
        payload=na_payload,
        idempotency_key="effect-na:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )
    with pytest.raises(ledger.WorkflowEffectLedgerError) as e4:
        ledger.append_effect_event_v1(
            private_root=root2,
            effect_id="effect-na",
            event_type="compensation_intent",
            payload=_compensation_intent_payload(
                na_receipt,
                fence=f2,
                idempotency_key="na-key",
                effect_id="effect-na",
            ),
            idempotency_key="effect-na:comp",
            occurred_at="2026-08-02T00:00:04Z",
        )
    # Public parse now fail-closes not_applied original receipts as payload_invalid
    # before cross-event transition classification (still exact rejection).
    assert e4.value.code == "payload_invalid"
    # WRONG_COMP_OBSERVATION: missing original binding fields
    with pytest.raises(ledger.WorkflowEffectLedgerError) as e5:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="compensation_intent",
            payload={
                "original_receipt_digest": _receipt_dict()["receipt_digest"],
                "compensation_ref": "compensation:test.restore",
                "request_digest": OTHER_DIGEST,
            },
            idempotency_key="effect-1:bad-comp",
            occurred_at="2026-08-02T00:00:04Z",
        )
    assert e5.value.code == "payload_invalid"


def test_pending_claim_same_request_finalizes_then_process_b_rejects(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    first = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-a",
        payload=_claim_payload(idempotency_key="shared-key", effect_id="effect-a"),
        idempotency_key="effect-a:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    from workflow_kernel.workflow_effect_ledger import _idempotency_index_path, _compact_json
    from workflow_kernel.runtime_state import write_private_file_atomic

    pending = {
        "schema_version": "workflow_effect_idempotency_index_v1",
        "idempotency_key": "shared-key",
        "request_digest": DIGEST,
        "effect_id": "effect-a",
        "target_digest": TARGET_DIGEST,
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "state": "pending",
        "event_id": None,
        "fence": None,
    }
    path = _idempotency_index_path(root, "shared-key")
    write_private_file_atomic(path, _compact_json(pending) + b"\n", private_root=root)
    # Same-request recovery finalizes bound with exact wel_ claim id.
    recovered = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-a",
        payload=_claim_payload(idempotency_key="shared-key", effect_id="effect-a"),
        idempotency_key="effect-a:claim",
        occurred_at="2026-08-02T00:00:01Z",
    )
    assert recovered["replayed"] is True
    assert recovered["event_id"] == first["event_id"]
    import json as _json
    bound = _json.loads(path.read_text(encoding="utf-8"))
    assert bound["state"] == "bound"
    assert bound["event_id"] == first["event_id"]
    # Different request process B rejects; still one claim/fence.
    with pytest.raises(ledger.WorkflowEffectLedgerError) as b_err:
        ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-b",
            payload=_claim_payload(
                idempotency_key="shared-key",
                request_digest=OTHER_DIGEST,
                owner="owner-b",
                effect_id="effect-b",
            ),
            idempotency_key="effect-b:claim",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert b_err.value.code == "idempotency_conflict"
    assert ledger.project_effect_v1(private_root=root, effect_id="effect-b")["status"] == "absent"
    assert ledger.project_effect_v1(private_root=root, effect_id="effect-a")["fence"] == first["fence"]


def test_replace_effects_dir_with_empty_is_corruption_not_absent(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    _advance_to_applied(root)
    effects = root / "effects"
    relocated = root / "effects.old"
    empty = root / "effects.new"
    empty.mkdir()
    os.rename(effects, relocated)
    os.rename(empty, effects)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    assert raised.value.code == "layout_corrupt"


def _claim_outcome(result: dict, worker: str) -> dict:
    return {
        "ok": True,
        "worker": worker,
        "effect_id": result.get("effect_id"),
        "event_id": result.get("event_id"),
        "fence": result.get("fence"),
        "status": result.get("status"),
        "replayed": result.get("replayed"),
    }


def _mp_canonical_lock_holder(root: str, held_q, release_evt, result_q) -> None:
    """Contender A: hold the canonical target lock before any namespace swap."""
    try:
        with ledger.hold_target_ownership_v1(
            private_root=root, target_digest=TARGET_DIGEST
        ) as ownership:
            held_q.put("A-holds-canonical-target-lock")
            if not release_evt.wait(10):
                raise TimeoutError("A release barrier timed out")
            result = ledger.claim_effect_v1(
                private_root=root,
                effect_id="effect-adversarial-A",
                payload=_claim_payload(
                    effect_id="effect-adversarial-A",
                    idempotency_key="adversarial-key",
                    owner="owner-A",
                    request_digest=DIGEST,
                ),
                idempotency_key="effect-adversarial-A:claim",
                occurred_at="2026-08-02T00:00:00Z",
                ownership=ownership,
            )
            result_q.put(_claim_outcome(result, "A"))
    except ledger.WorkflowEffectLedgerError as exc:
        result_q.put({"ok": False, "worker": "A", "code": exc.code})
    except BaseException as exc:  # pragma: no cover
        result_q.put({"ok": False, "worker": "A", "error": f"{type(exc).__name__}:{exc}"})


def _mp_swapped_lock_contender(
    root: str,
    attempt_q,
    verify_swapped_evt,
    detected_q,
    release_after_restore_evt,
    result_q,
) -> None:
    """Contender B: pause inside the identity callback immediately before lock open."""
    try:
        from contextlib import contextmanager
        from workflow_kernel import workflow_effect_ledger as led

        original_lock = led._exclusive_lock_at_dirfd
        target_lock_name = led._target_lock_name(TARGET_DIGEST)
        announced = False

        @contextmanager
        def gated_lock(
            dir_fd: int,
            lock_name: str,
            *,
            verify_identity=None,
        ):
            nonlocal announced
            callback = verify_identity

            def gated_verify_identity() -> None:
                nonlocal announced
                if callback is None:
                    return
                if lock_name != target_lock_name or announced:
                    callback()
                    return
                # Admission and all canonical pins have already succeeded. This
                # callback invocation is the one immediately before os.open.
                callback()
                announced = True
                attempt_q.put("B-before-actual-target-lock-open")
                if not verify_swapped_evt.wait(10):
                    raise TimeoutError("B swapped-identity verification barrier timed out")
                try:
                    callback()
                except led.WorkflowEffectLedgerError as exc:
                    detected_q.put(exc.code)
                    if not release_after_restore_evt.wait(10):
                        raise TimeoutError("B restore-before-release barrier timed out")
                    raise
                raise AssertionError("B identity callback admitted a swapped directory")

            with original_lock(
                dir_fd,
                lock_name,
                verify_identity=gated_verify_identity,
            ) as lock_fd:
                yield lock_fd

        led._exclusive_lock_at_dirfd = gated_lock
        result = led.claim_effect_v1(
            private_root=root,
            effect_id="effect-adversarial-B",
            payload=_claim_payload(
                effect_id="effect-adversarial-B",
                idempotency_key="adversarial-key",
                owner="owner-B",
                request_digest=OTHER_DIGEST,
            ),
            idempotency_key="effect-adversarial-B:claim",
            occurred_at="2026-08-02T00:00:01Z",
        )
        result_q.put(_claim_outcome(result, "B"))
    except led.WorkflowEffectLedgerError as exc:
        result_q.put({"ok": False, "worker": "B", "code": exc.code})
    except BaseException as exc:  # pragma: no cover
        result_q.put({"ok": False, "worker": "B", "error": f"{type(exc).__name__}:{exc}"})


def _restore_adversarial_directory(canonical: Path, held: Path, replacement: Path) -> None:
    if held.exists():
        if canonical.exists() and not replacement.exists():
            canonical.rename(replacement)
        if not canonical.exists():
            held.rename(canonical)


@pytest.mark.parametrize(
    ("parent", "child"),
    (
        (None, "locks"),
        (None, "effects"),
        (None, "fences"),
        (None, "indexes"),
        ("locks", "idempotency"),
        ("indexes", "idempotency"),
    ),
    ids=(
        "top-level-locks",
        "top-level-effects",
        "top-level-fences",
        "top-level-indexes",
        "nested-locks-idempotency",
        "nested-indexes-idempotency",
    ),
)
def test_oscillating_child_swap_and_competing_dispatcher(
    tmp_path: Path, parent: str | None, child: str
) -> None:
    """Each swapped identity has one durable winner and one fail-closed loser."""
    import shutil

    root = tmp_path / "private"
    ctx = mp.get_context("spawn")
    held_q = ctx.Queue()
    attempt_q = ctx.Queue()
    result_q = ctx.Queue()
    release_a = ctx.Event()
    verify_b_while_swapped = ctx.Event()
    release_b_after_restore = ctx.Event()
    detected_q = ctx.Queue()
    a = ctx.Process(
        target=_mp_canonical_lock_holder,
        args=(str(root), held_q, release_a, result_q),
    )
    b = ctx.Process(
        target=_mp_swapped_lock_contender,
        args=(
            str(root),
            attempt_q,
            verify_b_while_swapped,
            detected_q,
            release_b_after_restore,
            result_q,
        ),
    )
    processes = (a, b)
    base = root if parent is None else root / parent
    canonical = base / child
    held = base / f"{child}.canonical-held"
    replacement = base / f"{child}.swapped-replacement"
    try:
        a.start()
        assert held_q.get(timeout=10) == "A-holds-canonical-target-lock"

        b.start()
        # B has admitted and pinned the canonical layout, then reached the
        # callback invocation immediately before its actual target-lock open.
        assert attempt_q.get(timeout=10) == "B-before-actual-target-lock-open"
        before_swapped_domain = [
            (
                str(path.relative_to(canonical)),
                "dir" if path.is_dir() else path.read_bytes(),
            )
            for path in sorted(canonical.rglob("*"))
        ]
        replacement.mkdir()
        canonical.rename(held)
        replacement.rename(canonical)
        verify_b_while_swapped.set()
        assert detected_q.get(timeout=10) == "root_replaced"
        # Inspect both swapped domains before restoration renames either path.
        assert [
            (
                str(path.relative_to(held)),
                "dir" if path.is_dir() else path.read_bytes(),
            )
            for path in sorted(held.rglob("*"))
        ] == before_swapped_domain
        assert canonical.exists() and not list(canonical.iterdir())

        # B has latched root_replaced but remains paused inside the callback, so
        # it cannot open or acquire a lock in either replacement domain. Restore
        # canonical identity before allowing B to unwind.
        _restore_adversarial_directory(canonical, held, replacement)
        release_b_after_restore.set()
        b_outcome = result_q.get(timeout=15)
        assert b_outcome == {"ok": False, "worker": "B", "code": "root_replaced"}
        assert replacement.exists() and not list(replacement.iterdir())

        # A still owns the canonical target lock and is the sole publisher.
        release_a.set()

        outcomes = [b_outcome, result_q.get(timeout=15)]
        for proc in processes:
            proc.join(10)
            assert not proc.is_alive()
            assert proc.exitcode == 0

        assert len(outcomes) == 2
        assert {outcome.get("worker") for outcome in outcomes} == {"A", "B"}
        winners = [outcome for outcome in outcomes if outcome.get("ok") is True]
        losers = [outcome for outcome in outcomes if outcome.get("ok") is False]
        assert len(winners) == 1
        assert len(losers) == 1
        winner = winners[0]
        loser = losers[0]
        assert winner["worker"] == "A"
        assert winner["status"] == "claimed"
        assert winner["replayed"] is False
        assert isinstance(winner["fence"], int) and winner["fence"] == 1
        assert loser == {"ok": False, "worker": "B", "code": "root_replaced"}

        effect_files = list((root / "effects").glob("*.jsonl"))
        fence_files = list((root / "fences").glob("*.json"))
        index_files = list((root / "indexes" / "idempotency").glob("*.json"))
        assert len(effect_files) == 1
        assert len(fence_files) == 1
        assert len(index_files) == 1

        events = ledger.read_effect_events_v1(
            private_root=root, effect_id=str(winner["effect_id"])
        )
        assert len(events) == 1
        assert events[0]["event_type"] == "claimed"
        assert events[0]["event_id"] == winner["event_id"]
        assert events[0]["payload"]["fence"] == winner["fence"]
        assert events[0]["payload"]["owner"] == "owner-A"

        index = json.loads(index_files[0].read_text(encoding="utf-8"))
        assert index["state"] == "bound"
        assert index["idempotency_key"] == "adversarial-key"
        assert index["effect_id"] == winner["effect_id"]
        assert index["event_id"] == winner["event_id"]
        assert index["fence"] == winner["fence"]
        assert index["request_digest"] == DIGEST
        assert ledger.project_effect_v1(
            private_root=root, effect_id="effect-adversarial-B"
        )["status"] == "absent"
    finally:
        release_a.set()
        verify_b_while_swapped.set()
        release_b_after_restore.set()
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
            proc.join(2)
        _restore_adversarial_directory(canonical, held, replacement)
        if replacement.exists():
            shutil.rmtree(replacement)
        assert not any(proc.is_alive() for proc in processes)


def _rewrite_ledger_events(private_root: Path, effect_id: str, events: list[dict]) -> None:
    from workflow_kernel.runtime_state import write_private_file_atomic

    path = ledger.effect_ledger_path_v1(private_root=private_root, effect_id=effect_id)
    payload = b"".join(
        effect_contracts.canonical_json_v1(effect_contracts.thaw_admitted_json_v1(event)).encode("utf-8")
        + b"\n"
        for event in events
    )
    write_private_file_atomic(path, payload, private_root=private_root)


@pytest.mark.parametrize(
    ("parent", "nested"),
    (("locks", "idempotency"), ("indexes", "idempotency")),
)
def test_owned_claim_rejects_replaced_nested_idempotency_directory(
    tmp_path: Path, parent: str, nested: str
) -> None:
    root = tmp_path / "private"
    with pytest.raises(ledger.WorkflowEffectLedgerError) as ownership_exit:
        with ledger.hold_target_ownership_v1(
            private_root=root, target_digest=TARGET_DIGEST
        ) as ownership:
            canonical = root / parent / nested
            moved = root / parent / f"{nested}.held"
            replacement = root / parent / f"{nested}.replacement"
            replacement.mkdir()
            canonical.rename(moved)
            replacement.rename(canonical)
            with pytest.raises(ledger.WorkflowEffectLedgerError) as operation_error:
                ledger.claim_effect_v1(
                    private_root=root,
                    effect_id=f"effect-nested-{parent}",
                    payload=_claim_payload(
                        effect_id=f"effect-nested-{parent}",
                        idempotency_key=f"nested-key-{parent}",
                    ),
                    idempotency_key=f"effect-nested-{parent}:claim",
                    occurred_at="2026-08-02T00:00:00Z",
                    ownership=ownership,
                )
            assert operation_error.value.code == "root_replaced"
            assert not list(canonical.iterdir())
            assert not list(moved.iterdir())
    assert ownership_exit.value.code == "root_replaced"


def _swap_nested_directory(root: Path, parent: str, nested: str, suffix: str) -> tuple[Path, Path]:
    canonical = root / parent / nested
    moved = root / parent / f"{nested}.{suffix}.moved"
    replacement = root / parent / f"{nested}.{suffix}.replacement"
    replacement.mkdir()
    canonical.rename(moved)
    replacement.rename(canonical)
    return moved, canonical


def _restore_nested_directory(canonical: Path, moved: Path) -> Path:
    replacement = canonical.with_name(f"{canonical.name}.discarded-replacement")
    canonical.rename(replacement)
    moved.rename(canonical)
    return replacement


def test_nested_lock_replacement_after_verify_precedes_lock_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "private"
    original_lock = ledger._exclusive_lock_at_dirfd
    swapped: dict[str, Path] = {}
    idemp_lock_name = ledger._idempotency_lock_name("key-pre-lock-open")

    @contextmanager
    def swap_after_lock_verify(dir_fd, lock_name, *, verify_identity=None):
        callback = verify_identity
        fired = False

        def verify_then_swap() -> None:
            nonlocal fired
            if callback is not None:
                callback()
            if lock_name == idemp_lock_name and not fired:
                fired = True
                moved, canonical = _swap_nested_directory(
                    root, "locks", "idempotency", "pre-lock-open"
                )
                swapped.update(moved=moved, canonical=canonical)

        with original_lock(
            dir_fd, lock_name, verify_identity=verify_then_swap
        ) as lock_fd:
            yield lock_fd

    monkeypatch.setattr(ledger, "_exclusive_lock_at_dirfd", swap_after_lock_verify)
    with ledger.hold_target_ownership_v1(
        private_root=root, target_digest=TARGET_DIGEST
    ) as ownership:
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            ledger.claim_effect_v1(
                private_root=root,
                effect_id="effect-pre-lock-open",
                payload=_claim_payload(
                    effect_id="effect-pre-lock-open", idempotency_key="key-pre-lock-open"
                ),
                idempotency_key="effect-pre-lock-open:claim",
                occurred_at="2026-08-02T00:00:00Z",
                ownership=ownership,
            )
        assert raised.value.code == "root_replaced"
        assert not list(swapped["moved"].iterdir())
        assert not list(swapped["canonical"].iterdir())
        replacement = _restore_nested_directory(swapped["canonical"], swapped["moved"])
        assert not list(replacement.iterdir())


def test_nested_lock_replacement_after_acquire_removes_only_owned_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-acquire identity failure removes the created lock from the pinned moved dir."""
    from workflow_kernel import runtime_state

    root = tmp_path / "private"
    swapped: dict[str, Path] = {}
    original_acquire = runtime_state._acquire_posix_lock

    with ledger.hold_target_ownership_v1(
        private_root=root, target_digest=TARGET_DIGEST
    ) as ownership:
        def acquire_then_swap(fd: int) -> None:
            original_acquire(fd)
            moved, canonical = _swap_nested_directory(
                root, "locks", "idempotency", "post-lock-acquire"
            )
            swapped.update(moved=moved, canonical=canonical)

        monkeypatch.setattr(runtime_state, "_acquire_posix_lock", acquire_then_swap)
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            ledger.claim_effect_v1(
                private_root=root,
                effect_id="effect-post-lock-acquire",
                payload=_claim_payload(
                    effect_id="effect-post-lock-acquire",
                    idempotency_key="key-post-lock-acquire",
                ),
                idempotency_key="effect-post-lock-acquire:claim",
                occurred_at="2026-08-02T00:00:00Z",
                ownership=ownership,
            )
        assert raised.value.code == "root_replaced"
        assert not list(swapped["moved"].iterdir())
        assert not list(swapped["canonical"].iterdir())
        replacement = _restore_nested_directory(swapped["canonical"], swapped["moved"])
        assert not list(replacement.iterdir())
        monkeypatch.setattr(runtime_state, "_acquire_posix_lock", original_acquire)


def test_nested_index_replacement_after_verify_precedes_pending_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "private"
    original_verify = ledger._verify_transaction_identity
    swapped: dict[str, Path] = {}
    fired = False
    write_verify_count = 0

    def swap_after_write_admission(identity, child_pins=None, nested_pins=None):
        nonlocal fired, write_verify_count
        original_verify(identity, child_pins, nested_pins)
        caller = inspect.currentframe().f_back.f_code.co_name  # type: ignore[union-attr]
        if caller == "_write_idempotency_key_record":
            write_verify_count += 1
        if caller == "_write_idempotency_key_record" and write_verify_count == 2 and not fired:
            fired = True
            moved, canonical = _swap_nested_directory(
                root, "indexes", "idempotency", "pre-index-write"
            )
            swapped.update(moved=moved, canonical=canonical)

    monkeypatch.setattr(ledger, "_verify_transaction_identity", swap_after_write_admission)
    with ledger.hold_target_ownership_v1(
        private_root=root, target_digest=TARGET_DIGEST
    ) as ownership:
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            ledger.claim_effect_v1(
                private_root=root,
                effect_id="effect-pre-index-write",
                payload=_claim_payload(
                    effect_id="effect-pre-index-write", idempotency_key="key-pre-index-write"
                ),
                idempotency_key="effect-pre-index-write:claim",
                occurred_at="2026-08-02T00:00:00Z",
                ownership=ownership,
            )
        assert fired is True
        assert raised.value.code == "root_replaced"
        assert not list(swapped["moved"].iterdir())
        assert not list(swapped["canonical"].iterdir())
        replacement = _restore_nested_directory(swapped["canonical"], swapped["moved"])
        assert not list(replacement.iterdir())


@pytest.mark.parametrize(
    ("event_type", "payload_factory"),
    (
        ("intent_recorded", _intent_payload),
        ("observation_recorded", _observation_payload),
        ("receipt_terminal", _receipt_payload),
    ),
)
def test_post_claim_nested_replacement_precedes_each_append_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    payload_factory,
) -> None:
    root = tmp_path / event_type
    with ledger.hold_target_ownership_v1(
        private_root=root, target_digest=TARGET_DIGEST
    ) as ownership:
        claim = ledger.claim_effect_v1(
            private_root=root,
            effect_id="effect-1",
            payload=_claim_payload(),
            idempotency_key="effect-1:claim",
            occurred_at="2026-08-02T00:00:00Z",
            ownership=ownership,
        )
        fence = int(claim["fence"])
        if event_type != "intent_recorded":
            ledger.append_effect_event_v1(
                private_root=root,
                effect_id="effect-1",
                event_type="intent_recorded",
                payload=_intent_payload(fence=fence),
                idempotency_key="effect-1:intent",
                occurred_at="2026-08-02T00:00:01Z",
                ownership=ownership,
            )
        if event_type == "receipt_terminal":
            ledger.append_effect_event_v1(
                private_root=root,
                effect_id="effect-1",
                event_type="observation_recorded",
                payload=_observation_payload(fence=fence),
                idempotency_key="effect-1:observation",
                occurred_at="2026-08-02T00:00:02Z",
                ownership=ownership,
            )

        effect_path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
        before_event_count = len(
            ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
        )
        before_index = {
            path.name: path.read_bytes()
            for path in (root / "indexes" / "idempotency").iterdir()
        }
        original_write_index = ledger._write_idempotency_index
        swapped: dict[str, Path] = {}
        fired = False

        def swap_at_final_index_publication(*args, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                moved, canonical = _swap_nested_directory(
                    root, "indexes", "idempotency", f"publication-{event_type}"
                )
                swapped.update(moved=moved, canonical=canonical)
            return original_write_index(*args, **kwargs)

        monkeypatch.setattr(
            ledger, "_write_idempotency_index", swap_at_final_index_publication
        )
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            ledger.append_effect_event_v1(
                private_root=root,
                effect_id="effect-1",
                event_type=event_type,
                payload=payload_factory(fence=fence),
                idempotency_key=f"effect-1:{event_type}",
                occurred_at="2026-08-02T00:00:03Z",
                ownership=ownership,
            )
        assert fired is True
        assert raised.value.code == "root_replaced"
        assert raised.value.phase == "post_event_fsync"
        # The event fsync boundary precedes final index publication; the failure
        # is unknown/reconcile-only, but neither moved nor replacement index
        # domain receives the new idempotency record.
        assert not list(swapped["canonical"].iterdir())
        assert {path.name: path.read_bytes() for path in swapped["moved"].iterdir()} == before_index
        replacement = _restore_nested_directory(swapped["canonical"], swapped["moved"])
        assert not list(replacement.iterdir())
        monkeypatch.setattr(ledger, "_write_idempotency_index", original_write_index)
        assert len(
            ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
        ) == before_event_count + 1


def _recompute_event_id(event: dict) -> dict:
    body = {k: v for k, v in event.items() if k != "event_id"}
    event_id = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
    return {**body, "event_id": event_id}


def test_public_append_and_raw_replay_reject_mismatched_observation_receipt_compensation(
    tmp_path: Path,
) -> None:
    """Codex rereview-3 accepted-invalid cases: public append + raw replay with recomputed IDs."""
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )

    # 1) observation: wrapper adapter_result_digest=null with non-null embedded observation digest
    obs = _observation_dict(fence=fence, classification="applied")
    bad_obs_payload = _observation_payload(fence=fence, classification="applied", observation=obs)
    bad_obs_payload["adapter_result_digest"] = None  # mismatch vs embedded DIGEST
    with pytest.raises(ledger.WorkflowEffectLedgerError) as obs_append:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="observation_recorded",
            payload=bad_obs_payload,
            idempotency_key="effect-1:bad-obs",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert obs_append.value.code == "payload_invalid"

    good_events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    bad_obs_event = _recompute_event_id(
        {
            "schema_version": "workflow_effect_ledger_event_v1",
            "effect_id": "effect-1",
            "sequence": 3,
            "previous_event_id": good_events[-1]["event_id"],
            "idempotency_key": "effect-1:raw-bad-obs",
            "occurred_at": "2026-08-02T00:00:02Z",
            "event_type": "observation_recorded",
            "payload": bad_obs_payload,
        }
    )
    _rewrite_ledger_events(root, "effect-1", [*good_events, bad_obs_event])
    with pytest.raises(ledger.WorkflowEffectLedgerError) as obs_replay:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert obs_replay.value.code == "payload_invalid"
    _rewrite_ledger_events(root, "effect-1", good_events)

    # Restore good observation + receipt path for terminal binding cases.
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    events_after_obs = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")

    # 2) receipt: terminal receipt containing a different target
    bad_receipt_body = _receipt_dict(fence=fence, classification="applied")
    bad_receipt_body = dict(bad_receipt_body)
    del bad_receipt_body["receipt_digest"]
    bad_receipt_body["target"] = {"target_kind": "injected_callable", "target_ref": "target/OTHER"}
    bad_receipt_body["receipt_digest"] = effect_contracts.canonical_digest_v1(bad_receipt_body)
    bad_receipt_payload = _receipt_payload(
        fence=fence,
        classification="applied",
        receipt=bad_receipt_body,
        receipt_digest=bad_receipt_body["receipt_digest"],
    )
    with pytest.raises(ledger.WorkflowEffectLedgerError) as rcpt_append:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="receipt_terminal",
            payload=bad_receipt_payload,
            idempotency_key="effect-1:bad-receipt",
            occurred_at="2026-08-02T00:00:03Z",
        )
    assert rcpt_append.value.code == "payload_invalid"

    bad_receipt_event = _recompute_event_id(
        {
            "schema_version": "workflow_effect_ledger_event_v1",
            "effect_id": "effect-1",
            "sequence": 4,
            "previous_event_id": events_after_obs[-1]["event_id"],
            "idempotency_key": "effect-1:raw-bad-receipt",
            "occurred_at": "2026-08-02T00:00:03Z",
            "event_type": "receipt_terminal",
            "payload": bad_receipt_payload,
        }
    )
    _rewrite_ledger_events(root, "effect-1", [*events_after_obs, bad_receipt_event])
    with pytest.raises(ledger.WorkflowEffectLedgerError) as rcpt_replay:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert rcpt_replay.value.code == "payload_invalid"
    _rewrite_ledger_events(root, "effect-1", events_after_obs)

    terminal = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(fence=fence),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )
    assert terminal["status"] == "applied"
    original = _receipt_dict(fence=fence)
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_intent",
        payload=_compensation_intent_payload(original, fence=fence),
        idempotency_key="effect-1:comp-intent",
        occurred_at="2026-08-02T00:00:04Z",
    )
    events_after_comp_intent = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")

    # Multi-field aggregate path removed; one-field observation cases live in
    # test_compensation_observation_one_field_binding_rejects.


def _compensation_chain_through_observation(tmp_path: Path) -> tuple[Path, list[dict], dict, dict, dict]:
    """Build applied original + compensation intent/observation; return root/events/original/obs/base payload."""
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    original = _receipt_dict(fence=fence)
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(fence=fence),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_intent",
        payload=_compensation_intent_payload(original, fence=fence),
        idempotency_key="effect-1:comp-intent",
        occurred_at="2026-08-02T00:00:04Z",
    )
    comp_obs = _observation_dict(classification="applied", fence=fence)
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_observation",
        payload=_compensation_observation_payload(original, comp_obs, fence=fence),
        idempotency_key="effect-1:comp-obs",
        occurred_at="2026-08-02T00:00:05Z",
    )
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    receipt_body = _compensation_receipt_dict(
        classification="applied",
        fence=fence,
        preflight=_comp_preflight(),
        post=_as_plain(comp_obs["readback"]),
    )
    base_payload = {
        "receipt_digest": receipt_body["receipt_digest"],
        "receipt": receipt_body,
        "original_receipt_digest": original["receipt_digest"],
        "original_receipt": original,
        "fence": fence,
        "command_digest": DIGEST,
        "request_digest": OTHER_DIGEST,
        "target_digest": TARGET_DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "effect_id": "effect-1",
        "authority_binding_digest": DIGEST,
        "observation_digest": effect_contracts.canonical_digest_v1(comp_obs),
        "observation": comp_obs,
    }
    return root, events, original, comp_obs, base_payload


def _mutate_compensation_receipt_payload(base: dict, field: str) -> dict:
    """Mutate exactly one compensation-receipt binding field and recompute digests."""
    import copy

    payload = copy.deepcopy(base)
    receipt = dict(payload["receipt"])
    del receipt["receipt_digest"]

    if field == "command_digest":
        receipt["command_digest"] = OTHER_DIGEST
        payload["command_digest"] = OTHER_DIGEST
    elif field == "request_digest":
        payload["request_digest"] = DIGEST  # was OTHER_DIGEST from intent
    elif field == "effect_id_body":
        receipt["effect_id"] = "effect-other"
    elif field == "fence":
        # Keep wrapper fence bound to intent; only body fence diverges -> payload_invalid.
        receipt["fence"] = int(payload["fence"]) + 1
    elif field == "adapter_id":
        receipt["adapter_id"] = "other.adapter"
        payload["adapter_id"] = "other.adapter"
    elif field == "operation":
        receipt["operation"] = "other_op"
        payload["operation"] = "other_op"
    elif field == "target_kind":
        receipt["target"] = {"target_kind": "other_kind", "target_ref": TARGET["target_ref"]}
        payload["target_digest"] = effect_contracts.canonical_digest_v1(receipt["target"])
    elif field == "target_ref":
        receipt["target"] = {"target_kind": TARGET["target_kind"], "target_ref": "target/other"}
        payload["target_digest"] = effect_contracts.canonical_digest_v1(receipt["target"])
    elif field == "payload_digest":
        receipt["payload_digest"] = OTHER_DIGEST
        payload["payload_digest"] = OTHER_DIGEST
    elif field == "idempotency_key":
        receipt["idempotency_key"] = "wrong-key"
        payload["idempotency_key"] = "wrong-key"
    elif field == "authority_binding_digest":
        receipt["authority_binding_digest"] = OTHER_DIGEST
        payload["authority_binding_digest"] = OTHER_DIGEST
    elif field == "classification":
        receipt["result_classification"] = "not_applied"
    elif field == "compensation_ref":
        receipt["compensation_ref"] = "compensation:other"
    elif field == "reconcile_ref":
        receipt["reconcile_ref"] = "reconcile:other"
    elif field == "observation_body":
        obs = dict(payload["observation"])
        obs["observation_id"] = "obs-mutated"
        payload["observation"] = obs
        payload["observation_digest"] = effect_contracts.canonical_digest_v1(obs)
    elif field == "observation_digest":
        payload["observation_digest"] = OTHER_DIGEST
    elif field == "original_receipt_digest":
        payload["original_receipt_digest"] = OTHER_DIGEST
    elif field == "original_receipt_body":
        mutated = dict(payload["original_receipt"])
        del mutated["receipt_digest"]
        mutated["operation"] = "mutated_op"
        mutated["receipt_digest"] = effect_contracts.canonical_digest_v1(mutated)
        payload["original_receipt"] = mutated
        payload["original_receipt_digest"] = mutated["receipt_digest"]
    elif field == "receipt_body_only_command":
        # wrapper keeps claim command; body alone diverges (recomputed receipt digest).
        receipt["command_digest"] = OTHER_DIGEST
    elif field == "wrapper_only_effect_id":
        payload["effect_id"] = "effect-other"
    elif field == "wrapper_only_fence":
        payload["fence"] = int(payload["fence"]) + 1
    elif field == "wrapper_only_target_digest":
        payload["target_digest"] = OTHER_DIGEST
    elif field == "wrapper_only_receipt_digest":
        payload["receipt_digest"] = OTHER_DIGEST
    elif field == "wrapper_only_command_digest":
        payload["command_digest"] = OTHER_DIGEST
    elif field == "preflight_readback":
        receipt["preflight_readback"] = {"tampered": True}
    elif field == "post_readback":
        receipt["post_readback"] = {"tampered": True}
    elif field == "authority_binding_digest_wrapper_only":
        payload["authority_binding_digest"] = OTHER_DIGEST
    elif field == "authority_binding_digest_body_only":
        receipt["authority_binding_digest"] = OTHER_DIGEST
    else:
        raise AssertionError(f"unknown field {field}")

    if field not in {
        "request_digest",
        "observation_body",
        "observation_digest",
        "original_receipt_digest",
        "original_receipt_body",
        "wrapper_only_effect_id",
        "wrapper_only_fence",
        "wrapper_only_target_digest",
        "wrapper_only_receipt_digest",
        "wrapper_only_command_digest",
        "authority_binding_digest_wrapper_only",
    }:
        payload["receipt"] = {**receipt, "receipt_digest": effect_contracts.canonical_digest_v1(receipt)}
        payload["receipt_digest"] = payload["receipt"]["receipt_digest"]
    return payload


@pytest.mark.parametrize(
    "field",
    [
        "command_digest",
        "request_digest",
        "effect_id_body",
        "fence",
        "adapter_id",
        "operation",
        "target_kind",
        "target_ref",
        "payload_digest",
        "idempotency_key",
        "authority_binding_digest",
        "classification",
        "compensation_ref",
        "reconcile_ref",
        "observation_body",
        "observation_digest",
        "original_receipt_digest",
        "original_receipt_body",
        "receipt_body_only_command",
        "wrapper_only_effect_id",
        "wrapper_only_fence",
        "wrapper_only_target_digest",
        "wrapper_only_receipt_digest",
        "wrapper_only_command_digest",
        "preflight_readback",
        "post_readback",
        "authority_binding_digest_wrapper_only",
        "authority_binding_digest_body_only",
    ],
)
def test_compensation_receipt_one_field_binding_rejects(tmp_path: Path, field: str) -> None:
    root, events, _original, _obs, base = _compensation_chain_through_observation(tmp_path)
    bad_payload = _mutate_compensation_receipt_payload(base, field)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as append_err:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="compensation_receipt",
            payload=bad_payload,
            idempotency_key=f"effect-1:bad-comp-receipt-{field}",
            occurred_at="2026-08-02T00:00:06Z",
        )
    assert append_err.value.code == "payload_invalid", field

    bad_event = _recompute_event_id(
        {
            "schema_version": "workflow_effect_ledger_event_v1",
            "effect_id": "effect-1",
            "sequence": len(events) + 1,
            "previous_event_id": events[-1]["event_id"],
            "idempotency_key": f"effect-1:raw-bad-comp-receipt-{field}",
            "occurred_at": "2026-08-02T00:00:06Z",
            "event_type": "compensation_receipt",
            "payload": bad_payload,
        }
    )
    _rewrite_ledger_events(root, "effect-1", [*events, bad_event])
    with pytest.raises(ledger.WorkflowEffectLedgerError) as replay_err:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert replay_err.value.code == "payload_invalid", field


def test_compensation_receipt_canonical_positive_chain(tmp_path: Path) -> None:
    root, _events, _original, _obs, base = _compensation_chain_through_observation(tmp_path)
    ok = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_receipt",
        payload=base,
        idempotency_key="effect-1:comp-receipt-ok",
        occurred_at="2026-08-02T00:00:06Z",
    )
    assert ok["status"] == "compensated"
    # Raw replay of the positive chain must succeed.
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert events[-1]["event_type"] == "compensation_receipt"


def test_nested_array_observation_append_and_raw_replay(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    nested_readback = {"items": [["before"], {"deep": [1]}]}
    obs = _observation_dict(fence=fence)
    obs["readback"] = nested_readback
    payload = _observation_payload(fence=fence, observation=obs, readback=nested_readback)
    appended = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=payload,
        idempotency_key="effect-1:obs-nested",
        occurred_at="2026-08-02T00:00:02Z",
    )
    assert appended["status"] == "observed"
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    projected = events[-1]["payload"]["observation"]["readback"]
    assert effect_contracts.thaw_admitted_json_v1(projected) == nested_readback
    # Public nested projections are frozen: direct mutation raises and cannot alter later reads.
    with pytest.raises(TypeError):
        projected["items"][0][0] = "mutated"  # type: ignore[index]
    again = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert effect_contracts.thaw_admitted_json_v1(
        again[-1]["payload"]["observation"]["readback"]
    ) == nested_readback
    assert effect_contracts.canonical_digest_v1(
        effect_contracts.thaw_admitted_json_v1(again[-1]["payload"]["observation"]["readback"])
    ) == effect_contracts.canonical_digest_v1(nested_readback)
    proj = ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    with pytest.raises(TypeError):
        proj["facts"]["readback"]["items"][0][0] = "mutated"  # type: ignore[index]
    proj2 = ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    assert effect_contracts.thaw_admitted_json_v1(proj2["facts"]["readback"]) == nested_readback


@pytest.mark.parametrize(
    "field",
    [
        "command_digest",
        "request_digest",
        "fence",
        "adapter_id",
        "operation",
        "target_digest",
        "payload_digest",
        "idempotency_key",
        "authority_binding_digest",
        "effect_id",
        "readback",
        "readback_digest",
        "original_receipt_digest",
    ],
)
def test_compensation_observation_one_field_binding_rejects(tmp_path: Path, field: str) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    original = _receipt_dict(fence=fence)
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(fence=fence),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:03Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="compensation_intent",
        payload=_compensation_intent_payload(original, fence=fence),
        idempotency_key="effect-1:comp-intent",
        occurred_at="2026-08-02T00:00:04Z",
    )
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    comp_obs = _observation_dict(classification="applied", fence=fence)
    base = _compensation_observation_payload(original, comp_obs, fence=fence)
    bad = dict(base)
    if field == "command_digest":
        bad["command_digest"] = OTHER_DIGEST
    elif field == "request_digest":
        bad["request_digest"] = DIGEST
    elif field == "fence":
        # One-field: only wrapper fence diverges from durable compensation intent/observation.
        bad["fence"] = fence + 1
    elif field == "adapter_id":
        bad["adapter_id"] = "other.adapter"
    elif field == "operation":
        bad["operation"] = "other_op"
    elif field == "target_digest":
        bad["target_digest"] = OTHER_DIGEST
    elif field == "payload_digest":
        bad["payload_digest"] = OTHER_DIGEST
    elif field == "idempotency_key":
        bad["idempotency_key"] = "wrong-key"
    elif field == "authority_binding_digest":
        bad["authority_binding_digest"] = OTHER_DIGEST
    elif field == "effect_id":
        # One-field: only wrapper effect_id diverges; embedded observation stays bound.
        bad["effect_id"] = "effect-other"
    elif field == "readback":
        # One-field: wrapper readback diverges from embedded observation.readback.
        bad["readback"] = {"tampered": True}
        bad["readback_digest"] = effect_contracts.canonical_digest_v1(bad["readback"])
    elif field == "readback_digest":
        bad["readback_digest"] = OTHER_DIGEST
    elif field == "original_receipt_digest":
        bad["original_receipt_digest"] = OTHER_DIGEST
    else:
        raise AssertionError(field)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as append_err:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="compensation_observation",
            payload=bad,
            idempotency_key=f"effect-1:bad-comp-obs-{field}",
            occurred_at="2026-08-02T00:00:05Z",
        )
    assert append_err.value.code == "payload_invalid", field
    bad_event = _recompute_event_id(
        {
            "schema_version": "workflow_effect_ledger_event_v1",
            "effect_id": "effect-1",
            "sequence": len(events) + 1,
            "previous_event_id": events[-1]["event_id"],
            "idempotency_key": f"effect-1:raw-bad-comp-obs-{field}",
            "occurred_at": "2026-08-02T00:00:05Z",
            "event_type": "compensation_observation",
            "payload": bad,
        }
    )
    _rewrite_ledger_events(root, "effect-1", [*events, bad_event])
    with pytest.raises(ledger.WorkflowEffectLedgerError) as replay_err:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert replay_err.value.code == "payload_invalid", field


@pytest.mark.parametrize("field", ["preflight_readback", "post_readback"])
def test_original_receipt_readback_one_field_rejects(tmp_path: Path, field: str) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    receipt = _receipt_dict(fence=fence)
    body = dict(receipt)
    del body["receipt_digest"]
    body[field] = {"tampered": True}
    body["receipt_digest"] = effect_contracts.canonical_digest_v1(body)
    bad = _receipt_payload(fence=fence, receipt=body, receipt_digest=body["receipt_digest"])
    with pytest.raises(ledger.WorkflowEffectLedgerError) as append_err:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="receipt_terminal",
            payload=bad,
            idempotency_key=f"effect-1:bad-receipt-{field}",
            occurred_at="2026-08-02T00:00:03Z",
        )
    assert append_err.value.code == "payload_invalid", field
    bad_event = _recompute_event_id(
        {
            "schema_version": "workflow_effect_ledger_event_v1",
            "effect_id": "effect-1",
            "sequence": len(events) + 1,
            "previous_event_id": events[-1]["event_id"],
            "idempotency_key": f"effect-1:raw-bad-receipt-{field}",
            "occurred_at": "2026-08-02T00:00:03Z",
            "event_type": "receipt_terminal",
            "payload": bad,
        }
    )
    _rewrite_ledger_events(root, "effect-1", [*events, bad_event])
    with pytest.raises(ledger.WorkflowEffectLedgerError) as replay_err:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert replay_err.value.code == "payload_invalid", field


def test_public_append_accepts_fence_float_1_0(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    intent = _intent_payload(fence=int(claim["fence"]))
    intent["fence"] = 1.0  # type: ignore[assignment]
    ok = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=intent,
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    assert ok["status"] == "unknown"
    assert ok.get("fence") == 1
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert events[-1]["payload"]["fence"] == 1


@pytest.mark.parametrize("form", ["1.0", "1e0"])
def test_raw_replay_accepts_integral_decimal_and_exponent_forms(tmp_path: Path, form: str) -> None:
    root, _ = _paths(tmp_path)
    ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    events = [
        effect_contracts.thaw_admitted_json_v1(e)
        for e in ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    ]
    from workflow_kernel.runtime_state import write_private_file_atomic

    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    line = effect_contracts.canonical_json_v1(events[0]).replace('"fence":1', f'"fence":{form}', 1)
    write_private_file_atomic(path, line.encode("utf-8") + b"\n", private_root=root)
    replayed = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert replayed[0]["payload"]["fence"] == 1, form


def test_raw_replay_accepts_neg_zero_in_readback(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    obs_payload = _observation_payload(fence=fence, readback={"n": 0, "present": True, "content_digest": DIGEST})
    events = [
        effect_contracts.thaw_admitted_json_v1(e)
        for e in ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    ]
    body = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": "effect-1",
        "sequence": 3,
        "previous_event_id": events[-1]["event_id"],
        "idempotency_key": "effect-1:obs-neg0",
        "occurred_at": "2026-08-02T00:00:02Z",
        "event_type": "observation_recorded",
        "payload": obs_payload,
    }
    event = _recompute_event_id(body)
    line = effect_contracts.canonical_json_v1(event).replace('"n":0', '"n":-0.0', 1)
    from workflow_kernel.runtime_state import write_private_file_atomic

    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    payload = b"".join(
        effect_contracts.canonical_json_v1(e).encode("utf-8") + b"\n" for e in events
    ) + line.encode("utf-8") + b"\n"
    write_private_file_atomic(path, payload, private_root=root)
    replayed = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert replayed[-1]["payload"]["readback"]["n"] == 0

@pytest.mark.parametrize(
    "raw_number",
    ["1e-7", "0.10000000000000002", "9007199254740992", "NaN", "Infinity", "-Infinity"],
)
def test_raw_replay_rejects_non_integral_or_unsafe_numbers(tmp_path: Path, raw_number: str) -> None:
    root, _ = _paths(tmp_path)
    ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    events = [
        effect_contracts.thaw_admitted_json_v1(e)
        for e in ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    ]
    from workflow_kernel.runtime_state import write_private_file_atomic

    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    line = effect_contracts.canonical_json_v1(events[0]).replace('"fence":1', f'"fence":{raw_number}', 1)
    write_private_file_atomic(path, line.encode("utf-8") + b"\n", private_root=root)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert raised.value.code == "payload_invalid", raw_number


def test_reconciled_chain_binds_terminal_post_readback_to_durable_fact(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence, classification="unknown"),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    reconcile_readback = {"present": True, "content_digest": DIGEST, "reconciled": True}
    reconciled = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="reconciled",
        payload=_reconcile_payload(fence=fence, readback=reconcile_readback),
        idempotency_key="effect-1:reconcile",
        occurred_at="2026-08-02T00:00:03Z",
    )
    assert reconciled["status"] == "reconciled"
    assert effect_contracts.thaw_admitted_json_v1(reconciled["facts"]["readback"]) == reconcile_readback
    assert reconciled["facts"]["readback_digest"] == effect_contracts.canonical_digest_v1(reconcile_readback)

    receipt_body = _receipt_dict(fence=fence, classification="applied")
    del receipt_body["receipt_digest"]
    receipt_body["post_readback"] = dict(reconcile_readback)
    receipt_body["receipt_digest"] = effect_contracts.canonical_digest_v1(receipt_body)
    terminal = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=_receipt_payload(
            fence=fence,
            classification="applied",
            receipt=receipt_body,
            receipt_digest=receipt_body["receipt_digest"],
            observation=_observation_dict(classification="unknown", fence=fence),
        ),
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:04Z",
    )
    assert terminal["status"] == "applied"
    # Durable bytes remain stable after public freeze.
    before = path.read_bytes()
    with pytest.raises(TypeError):
        terminal["status"] = "mutated"  # type: ignore[index]
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["readback", "readback_digest", "embedded_receipt_post"])
def test_reconciled_one_field_mutations_reject_exact_payload_invalid(tmp_path: Path, field: str) -> None:
    import copy

    root, _ = _paths(tmp_path)
    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence, classification="unknown"),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    reconcile_readback = {"present": True, "content_digest": DIGEST, "reconciled": True}
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="reconciled",
        payload=_reconcile_payload(fence=fence, readback=reconcile_readback),
        idempotency_key="effect-1:reconcile",
        occurred_at="2026-08-02T00:00:03Z",
    )
    events = [
        effect_contracts.thaw_admitted_json_v1(e)
        for e in ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    ]
    good_receipt = _receipt_dict(fence=fence, classification="applied")
    del good_receipt["receipt_digest"]
    good_receipt["post_readback"] = dict(reconcile_readback)
    good_receipt["receipt_digest"] = effect_contracts.canonical_digest_v1(good_receipt)
    good_payload = _receipt_payload(
        fence=fence,
        classification="applied",
        receipt=good_receipt,
        receipt_digest=good_receipt["receipt_digest"],
        observation=_observation_dict(classification="unknown", fence=fence),
    )

    if field == "readback":
        # Mutate durable reconcile readback value while keeping digest+event_id coherent.
        # NOTE(4A.5B): true single-field semantic isolation (mutate readback only without
        # recomputing digest/event_id, plus full relation table) is deferred to 4A.5B.
        bad_events = copy.deepcopy(events)
        rec = bad_events[-1]
        assert rec["event_type"] == "reconciled"
        rec["payload"]["readback"] = {"attacker_chosen": True}
        rec["payload"]["readback_digest"] = effect_contracts.canonical_digest_v1(
            rec["payload"]["readback"]
        )
        body = {k: v for k, v in rec.items() if k != "event_id"}
        rec["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
        from workflow_kernel.runtime_state import write_private_file_atomic

        raw = b"".join(effect_contracts.canonical_json_v1(e).encode() + b"\n" for e in bad_events)
        write_private_file_atomic(path, raw, private_root=root)
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            ledger.append_effect_event_v1(
                private_root=root,
                effect_id="effect-1",
                event_type="receipt_terminal",
                payload=good_payload,
                idempotency_key="effect-1:receipt",
                occurred_at="2026-08-02T00:00:04Z",
            )
        assert raised.value.code == "payload_invalid"
        return

    if field == "readback_digest":
        bad_events = copy.deepcopy(events)
        rec = bad_events[-1]
        rec["payload"]["readback_digest"] = OTHER_DIGEST
        body = {k: v for k, v in rec.items() if k != "event_id"}
        rec["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
        from workflow_kernel.runtime_state import write_private_file_atomic

        raw = b"".join(effect_contracts.canonical_json_v1(e).encode() + b"\n" for e in bad_events)
        write_private_file_atomic(path, raw, private_root=root)
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
        assert raised.value.code == "payload_invalid"
        return

    # embedded_receipt_post: public append with attacker post_readback + recomputed digests.
    bad_receipt = copy.deepcopy(good_receipt)
    del bad_receipt["receipt_digest"]
    bad_receipt["post_readback"] = {"attacker_chosen": True}
    bad_receipt["receipt_digest"] = effect_contracts.canonical_digest_v1(bad_receipt)
    bad_payload = _receipt_payload(
        fence=fence,
        classification="applied",
        receipt=bad_receipt,
        receipt_digest=bad_receipt["receipt_digest"],
        observation=_observation_dict(classification="unknown", fence=fence),
    )
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="receipt_terminal",
            payload=bad_payload,
            idempotency_key="effect-1:receipt-bad",
            occurred_at="2026-08-02T00:00:04Z",
        )
    assert raised.value.code == "payload_invalid"

    # Raw replay path with same one-field mutation also rejects.
    bad_event = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": "effect-1",
        "sequence": len(events) + 1,
        "previous_event_id": events[-1]["event_id"],
        "idempotency_key": "effect-1:receipt-raw",
        "occurred_at": "2026-08-02T00:00:04Z",
        "event_type": "receipt_terminal",
        "payload": bad_payload,
    }
    bad_event["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(bad_event)
    from workflow_kernel.runtime_state import write_private_file_atomic

    raw = b"".join(
        effect_contracts.canonical_json_v1(e).encode() + b"\n" for e in [*events, bad_event]
    )
    write_private_file_atomic(path, raw, private_root=root)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as replay_err:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert replay_err.value.code == "payload_invalid"


def test_public_append_read_projection_roots_are_immutable(tmp_path: Path) -> None:
    from types import MappingProxyType

    root, _ = _paths(tmp_path)
    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    assert isinstance(claim, MappingProxyType)
    with pytest.raises(TypeError):
        claim["status"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        claim["facts"]["owner"] = "mutated"  # type: ignore[index]

    intent = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    assert isinstance(intent, MappingProxyType)
    with pytest.raises(TypeError):
        intent["status"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        intent["facts"]["preflight_readback"]["ok"] = False  # type: ignore[index]

    replayed = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=int(claim["fence"])),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    assert replayed["replayed"] is True
    assert isinstance(replayed, MappingProxyType)
    with pytest.raises(TypeError):
        replayed["event_id"] = "mutated"  # type: ignore[index]

    before = path.read_bytes()
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert isinstance(events, tuple)
    with pytest.raises(TypeError):
        events[0]["effect_id"] = "mutated"  # type: ignore[index]
    with pytest.raises(AttributeError):
        events.append({})  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        events[-1]["payload"]["preflight_readback"]["ok"] = False  # type: ignore[index]

    proj = ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    assert isinstance(proj, MappingProxyType)
    with pytest.raises(TypeError):
        proj["status"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        proj["facts"]["preflight_readback"]["ok"] = False  # type: ignore[index]

    assert path.read_bytes() == before
    again = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert effect_contracts.canonical_digest_v1(
        effect_contracts.thaw_admitted_json_v1(again[-1])
    ) == effect_contracts.canonical_digest_v1(effect_contracts.thaw_admitted_json_v1(events[-1]))


def test_reconciled_receipt_embedded_observation_body_always_bound(tmp_path: Path) -> None:
    """P1.3: after reconcile, changed embedded observation body rejects on append and raw replay."""
    import copy

    from workflow_kernel.runtime_state import write_private_file_atomic

    root, _ = _paths(tmp_path)
    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    fence = int(claim["fence"])
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence),
        idempotency_key="effect-1:intent",
        occurred_at="2026-08-02T00:00:01Z",
    )
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="observation_recorded",
        payload=_observation_payload(fence=fence, classification="unknown"),
        idempotency_key="effect-1:obs",
        occurred_at="2026-08-02T00:00:02Z",
    )
    reconcile_readback = {"present": True, "content_digest": DIGEST, "reconciled": True}
    ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="reconciled",
        payload=_reconcile_payload(fence=fence, readback=reconcile_readback),
        idempotency_key="effect-1:reconcile",
        occurred_at="2026-08-02T00:00:03Z",
    )
    events = [
        effect_contracts.thaw_admitted_json_v1(e)
        for e in ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    ]
    before = path.read_bytes()
    durable_obs = _observation_dict(classification="unknown", fence=fence)
    bad_obs = dict(durable_obs)
    bad_obs["observation_id"] = "attacker-obs-id"
    receipt_body = _receipt_dict(fence=fence, classification="applied")
    del receipt_body["receipt_digest"]
    receipt_body["post_readback"] = dict(reconcile_readback)
    receipt_body["receipt_digest"] = effect_contracts.canonical_digest_v1(receipt_body)
    bad_payload = _receipt_payload(
        fence=fence,
        classification="applied",
        receipt=receipt_body,
        receipt_digest=receipt_body["receipt_digest"],
        observation=bad_obs,
    )
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="receipt_terminal",
            payload=bad_payload,
            idempotency_key="effect-1:receipt",
            occurred_at="2026-08-02T00:00:04Z",
        )
    assert raised.value.code == "payload_invalid"
    assert "observation" in str(raised.value).lower()
    assert path.read_bytes() == before
    assert len(ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")) == len(events)

    # Raw replay one-field negative with observation_digest+event_id recomputed.
    bad_event = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": "effect-1",
        "sequence": len(events) + 1,
        "previous_event_id": events[-1]["event_id"],
        "idempotency_key": "effect-1:receipt-raw",
        "occurred_at": "2026-08-02T00:00:04Z",
        "event_type": "receipt_terminal",
        "payload": bad_payload,
    }
    bad_event["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(bad_event)
    raw = b"".join(
        effect_contracts.canonical_json_v1(e).encode() + b"\n" for e in [*copy.deepcopy(events), bad_event]
    )
    write_private_file_atomic(path, raw, private_root=root)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as replay_err:
        ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert replay_err.value.code == "payload_invalid"

    # Restore durable chain and prove good terminal still works with exact durable observation body.
    write_private_file_atomic(path, before, private_root=root)
    good_payload = _receipt_payload(
        fence=fence,
        classification="applied",
        receipt=receipt_body,
        receipt_digest=receipt_body["receipt_digest"],
        observation=durable_obs,
    )
    terminal = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="receipt_terminal",
        payload=good_payload,
        idempotency_key="effect-1:receipt",
        occurred_at="2026-08-02T00:00:04Z",
    )
    assert terminal["status"] == "applied"


def test_envelope_and_command_idempotency_namespaces_are_independent(tmp_path: Path) -> None:
    """P2: unequal envelope event key vs command payload key is valid; conflicts exact-native."""
    root, _ = _paths(tmp_path)
    # Intentionally unequal namespaces: envelope key != payload.command idempotency_key
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(idempotency_key="command-namespace-A"),
        idempotency_key="envelope-namespace-A",
        occurred_at="2026-08-02T00:00:00Z",
    )
    assert claim["replayed"] is False
    path = ledger.effect_ledger_path_v1(private_root=root, effect_id="effect-1")
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    assert events[0]["idempotency_key"] == "envelope-namespace-A"
    assert events[0]["payload"]["idempotency_key"] == "command-namespace-A"

    # Event replay conflict at native envelope boundary (append path): same envelope key,
    # different canonical event body; effect IDs remain internally consistent.
    fence = int(claim["fence"])
    intent = ledger.append_effect_event_v1(
        private_root=root,
        effect_id="effect-1",
        event_type="intent_recorded",
        payload=_intent_payload(fence=fence, idempotency_key="command-namespace-A"),
        idempotency_key="envelope-intent-A",
        occurred_at="2026-08-02T00:00:01Z",
    )
    assert intent["replayed"] is False
    before_intent = path.read_bytes()
    bad_intent = _intent_payload(fence=fence, idempotency_key="command-namespace-A")
    bad_intent["authority_binding_digest"] = OTHER_DIGEST
    with pytest.raises(ledger.WorkflowEffectLedgerError) as env_conflict:
        ledger.append_effect_event_v1(
            private_root=root,
            effect_id="effect-1",
            event_type="intent_recorded",
            payload=bad_intent,
            idempotency_key="envelope-intent-A",
            occurred_at="2026-08-02T00:00:02Z",
        )
    assert env_conflict.value.code == "idempotency_conflict"
    assert "maps to another event" in str(env_conflict.value).lower() or "different event" in str(env_conflict.value).lower()
    assert path.read_bytes() == before_intent

    # Command/index conflict is independent: same command key, different request;
    # payload.effect_id matches envelope effect_id (internally valid).
    root2 = tmp_path / "private2"
    ledger.claim_effect_v1(
        private_root=root2,
        effect_id="effect-a",
        payload=_claim_payload(idempotency_key="shared-command-key", effect_id="effect-a"),
        idempotency_key="envelope-a",
        occurred_at="2026-08-02T00:00:00Z",
    )
    with pytest.raises(ledger.WorkflowEffectLedgerError) as cmd_conflict:
        ledger.claim_effect_v1(
            private_root=root2,
            effect_id="effect-b",
            payload=_claim_payload(
                idempotency_key="shared-command-key",
                request_digest=OTHER_DIGEST,
                effect_id="effect-b",
            ),
            idempotency_key="envelope-b",
            occurred_at="2026-08-02T00:00:01Z",
        )
    # Native command-index boundary exact code (not payload_invalid escape).
    assert cmd_conflict.value.code == "idempotency_conflict"
    assert "request digest" in str(cmd_conflict.value).lower() or "bound to a different request" in str(cmd_conflict.value).lower()
    # Prove envelope namespaces remained distinct on the successful first claim.
    ev_a = ledger.read_effect_events_v1(private_root=root2, effect_id="effect-a")
    assert ev_a[0]["idempotency_key"] == "envelope-a"
    assert ev_a[0]["payload"]["idempotency_key"] == "shared-command-key"
    assert not ledger.effect_ledger_path_v1(private_root=root2, effect_id="effect-b").exists()


def _cross_relation_ids() -> list[str]:
    from workflow_kernel.effect_ledger_relation_manifests import cross_event_relation_ids

    return cross_event_relation_ids()


def _as_plain(obj: object) -> object:
    """Normalize MappingProxyType/tuples from frozen projections to plain containers."""
    from collections.abc import Mapping

    # Prefer contract thaw which fully materializes admitted frozen trees.
    try:
        return effect_contracts.thaw_admitted_json_v1(obj)
    except Exception:
        pass
    if isinstance(obj, Mapping) and not isinstance(obj, dict):
        return {k: _as_plain(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {k: _as_plain(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_as_plain(v) for v in obj]
    if isinstance(obj, list):
        return [_as_plain(v) for v in obj]
    return obj


def _semantic_diff_paths(a: object, b: object, prefix: str = "") -> list[str]:
    a = _as_plain(a)
    b = _as_plain(b)
    diffs: list[str] = []
    if type(a) is not type(b) or (not isinstance(a, (dict, list)) and a != b):
        if a != b:
            diffs.append(prefix or "$")
        return diffs
    if isinstance(a, dict):
        assert isinstance(b, dict)
        for key in sorted(set(a) | set(b)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in a or key not in b:
                diffs.append(path)
            else:
                diffs.extend(_semantic_diff_paths(a[key], b[key], path))
    elif isinstance(a, list):
        if a != b:
            diffs.append(prefix or "$")
    return diffs


def _collapse_diff_paths(
    diffs: list[str], mutated: str, bookkeeping: list[str] | None = None
) -> list[str]:
    collapsed: list[str] = []
    bk = list(bookkeeping or [])
    for p in diffs:
        if p == mutated or p.startswith(mutated + "."):
            if mutated not in collapsed:
                collapsed.append(mutated)
            continue
        matched = None
        for b in bk:
            if p == b or p.startswith(b + "."):
                matched = b
                break
        if matched is not None:
            if matched not in collapsed:
                collapsed.append(matched)
        else:
            collapsed.append(p)
    return collapsed


def _thaw_proj(obj: object) -> object:
    from workflow_kernel.effect_contracts import thaw_admitted_json_v1

    return thaw_admitted_json_v1(obj)


def _normalize_layout_seal_bytes(data: bytes) -> bytes:
    """Layout seals embed host inode/dev identities; normalize for cross-root equality.

    Semantic durable content (events/indexes/fences/locks) remains exact. Only the
    layout seal's host-specific child identity fields are zeroed so independent
    rebuilds of the same logical prestate compare equal while still proving the
    seal file is present and structurally valid.
    """
    import json

    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    if not isinstance(obj, dict):
        return data
    children = obj.get("children")
    if not isinstance(children, dict):
        return data
    normalized_children: dict[str, object] = {}
    for name, meta in children.items():
        if isinstance(meta, dict):
            normalized_children[str(name)] = {
                **meta,
                "dev": 0,
                "ino": 0,
            }
        else:
            normalized_children[str(name)] = meta
    out = dict(obj)
    out["children"] = normalized_children
    return json.dumps(out, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _root_snapshot(root: Path, effect_id: str = "effect-1") -> tuple[bytes, object | None]:
    """Governed durable artifact snapshot under private root.

    Hashes/reads every durable artifact relevant to ledger semantics:
    ledger event file, idempotency index files, fence docs, lock/index files,
    and layout seal (inode/dev normalized). Projection is public API only
    (separate from durable bytes).
    """
    import hashlib

    parts: list[bytes] = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            data = p.read_bytes()
            if rel == ".hgwk_effect_layout_v1.json" or p.name == ".hgwk_effect_layout_v1.json":
                data = _normalize_layout_seal_bytes(data)
            parts.append(rel.encode() + b"\0" + hashlib.sha256(data).digest() + b"\0" + data)
    raw = b"\n".join(parts)
    proj = None
    path = ledger.effect_ledger_path_v1(private_root=root, effect_id=effect_id)
    if path.exists() and path.read_bytes():
        proj = _thaw_proj(ledger.project_effect_v1(private_root=root, effect_id=effect_id))
    return raw, proj


def _copy_root(src: Path, dst: Path) -> None:
    import shutil

    if dst.exists():
        shutil.rmtree(dst)
    if src.exists():
        shutil.copytree(src, dst)
    else:
        dst.mkdir(parents=True, exist_ok=True)


class _PrecallIndexAuthority:
    """Independent pre-call full-identity idempotency expected docs (never from durable SUT reads)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, object]] = {}
        self.events: list[dict] = []  # independently authored intended events
        self._seq_by_effect: dict[str, int] = {}
        self._prev_by_effect: dict[str, str | None] = {}
        self._fence_by_target: dict[str, int] = {}

    def expected_docs(self) -> dict[str, dict[str, object]]:
        return {k: dict(v) for k, v in self.docs.items()}

    def _note_doc(
        self,
        *,
        key: str,
        request_digest: str,
        effect_id: str,
        target_digest: str,
        event_id: str,
        fence: int,
        command_digest: str,
        adapter_id: str,
        operation: str,
        payload_digest: str,
    ) -> None:
        self.docs[str(key)] = {
            "schema_version": "workflow_effect_idempotency_index_v1",
            "state": "bound",
            "request_digest": request_digest,
            "effect_id": str(effect_id),
            "target_digest": target_digest,
            "idempotency_key": str(key),
            "event_id": str(event_id),
            "fence": int(fence),
            "command_digest": command_digest,
            "adapter_id": adapter_id,
            "operation": operation,
            "payload_digest": payload_digest,
        }

    def author_claim_event(self, *, effect_id: str, payload: dict, envelope_key: str, occurred_at: str) -> dict:
        target = str(payload["target_digest"])
        # Fresh-root protocol: first claim on target gets fence 1, then monotonic.
        next_fence = self._fence_by_target.get(target, 0) + 1
        self._fence_by_target[target] = next_fence
        claim_payload = dict(payload)
        claim_payload["fence"] = next_fence
        claim_payload["effect_id"] = effect_id
        body = {
            "schema_version": "workflow_effect_ledger_event_v1",
            "effect_id": effect_id,
            "sequence": 1,
            "previous_event_id": None,
            "idempotency_key": envelope_key,
            "occurred_at": occurred_at,
            "event_type": "claimed",
            "payload": claim_payload,
        }
        event_id = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
        event = {**body, "event_id": event_id}
        self._seq_by_effect[effect_id] = 1
        self._prev_by_effect[effect_id] = event_id
        self.events.append(event)
        # Index key is payload.idempotency_key (claim binding), not necessarily envelope key.
        idx_key = str(claim_payload.get("idempotency_key") or envelope_key)
        self._note_doc(
            key=idx_key,
            request_digest=str(claim_payload["request_digest"]),
            effect_id=effect_id,
            target_digest=str(claim_payload["target_digest"]),
            event_id=event_id,
            fence=int(claim_payload["fence"]),
            command_digest=str(claim_payload["command_digest"]),
            adapter_id=str(claim_payload["adapter_id"]),
            operation=str(claim_payload["operation"]),
            payload_digest=str(claim_payload["payload_digest"]),
        )
        return event

    def author_append_event(
        self, *, effect_id: str, event_type: str, payload: dict, key: str, at: str
    ) -> dict:
        seq = self._seq_by_effect.get(effect_id, 0) + 1
        prev = self._prev_by_effect.get(effect_id)
        body = {
            "schema_version": "workflow_effect_ledger_event_v1",
            "effect_id": effect_id,
            "sequence": seq,
            "previous_event_id": prev,
            "idempotency_key": key,
            "occurred_at": at,
            "event_type": event_type,
            "payload": dict(payload),
        }
        event_id = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
        event = {**body, "event_id": event_id}
        self._seq_by_effect[effect_id] = seq
        self._prev_by_effect[effect_id] = event_id
        self.events.append(event)
        if "request_digest" in payload and "target_digest" in payload:
            self._note_doc(
                key=key,
                request_digest=str(payload["request_digest"]),
                effect_id=effect_id,
                target_digest=str(payload["target_digest"]),
                event_id=event_id,
                fence=int(payload["fence"]),
                command_digest=str(payload["command_digest"]),
                adapter_id=str(payload["adapter_id"]),
                operation=str(payload["operation"]),
                payload_digest=str(payload["payload_digest"]),
            )
        return event


# Per-root setup authorities (test-only).
_PRECALL_BY_ROOT: dict[str, _PrecallIndexAuthority] = {}


def _precall_for(root: Path) -> _PrecallIndexAuthority:
    key = str(root.resolve())
    auth = _PRECALL_BY_ROOT.get(key)
    if auth is None:
        auth = _PrecallIndexAuthority()
        _PRECALL_BY_ROOT[key] = auth
    return auth


def _claim(root: Path, effect_id: str = "effect-1", **payload_over: object) -> dict:
    payload = _claim_payload(effect_id=effect_id, **payload_over)
    envelope_key = f"{effect_id}:claim"
    occurred_at = "2026-08-02T00:00:00Z"
    auth = _precall_for(root)
    intended = auth.author_claim_event(
        effect_id=effect_id, payload=payload, envelope_key=envelope_key, occurred_at=occurred_at
    )
    result = ledger.claim_effect_v1(
        private_root=root,
        effect_id=effect_id,
        payload=payload,
        idempotency_key=envelope_key,
        occurred_at=occurred_at,
    )
    # Return value may include fence; intended already predicted fence for fresh root.
    assert int(result["fence"]) == int(intended["payload"]["fence"])
    assert str(result["event_id"]) == str(intended["event_id"])
    return result


def _append(root: Path, effect_id: str, event_type: str, payload: dict, key: str, at: str) -> dict:
    auth = _precall_for(root)
    # Identity replay: key already authored with same request_digest — do not invent a new event.
    existing = auth.docs.get(str(key))
    req = payload.get("request_digest")
    if (
        existing is not None
        and req is not None
        and str(existing.get("request_digest")) == str(req)
        and str(existing.get("effect_id")) == str(effect_id)
    ):
        result = ledger.append_effect_event_v1(
            private_root=root,
            effect_id=effect_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=key,
            occurred_at=at,
        )
        assert result.get("replayed") is True
        assert str(result["event_id"]) == str(existing["event_id"])
        return result
    intended = auth.author_append_event(
        effect_id=effect_id, event_type=event_type, payload=payload, key=key, at=at
    )
    result = ledger.append_effect_event_v1(
        private_root=root,
        effect_id=effect_id,
        event_type=event_type,
        payload=payload,
        idempotency_key=key,
        occurred_at=at,
    )
    assert str(result["event_id"]) == str(intended["event_id"])
    return result


def _build_to_applied_terminal(root: Path, *, classification: str = "applied") -> tuple[dict, dict]:
    claim = _claim(root)
    fence = int(claim["fence"])
    _append(root, "effect-1", "intent_recorded", _intent_payload(fence=fence), "effect-1:intent", "2026-08-02T00:00:01Z")
    obs_class = "unknown" if classification == "applied" and False else classification
    # For not_applied terminal, observation must also be not_applied (or unknown+reconcile).
    if classification == "not_applied":
        _append(
            root,
            "effect-1",
            "observation_recorded",
            _observation_payload(fence=fence, classification="not_applied"),
            "effect-1:obs",
            "2026-08-02T00:00:02Z",
        )
        receipt = _receipt_dict(classification="not_applied", fence=fence)
        _append(
            root,
            "effect-1",
            "receipt_terminal",
            _receipt_payload(fence=fence, classification="not_applied", receipt=receipt, receipt_digest=receipt["receipt_digest"]),
            "effect-1:receipt",
            "2026-08-02T00:00:03Z",
        )
    else:
        _append(
            root,
            "effect-1",
            "observation_recorded",
            _observation_payload(fence=fence, classification="applied"),
            "effect-1:obs",
            "2026-08-02T00:00:02Z",
        )
        receipt = _receipt_dict(classification="applied", fence=fence)
        _append(
            root,
            "effect-1",
            "receipt_terminal",
            _receipt_payload(fence=fence, classification="applied", receipt=receipt, receipt_digest=receipt["receipt_digest"]),
            "effect-1:receipt",
            "2026-08-02T00:00:03Z",
        )
    return claim, receipt


def _build_to_compensation_intent(root: Path) -> tuple[dict, dict]:
    claim, receipt = _build_to_applied_terminal(root)
    fence = int(claim["fence"])
    _append(
        root,
        "effect-1",
        "compensation_intent",
        _compensation_intent_payload(receipt, fence=fence),
        "effect-1:comp-intent",
        "2026-08-02T00:00:04Z",
    )
    return claim, receipt


def _build_to_compensation_observation(root: Path) -> tuple[dict, dict, dict]:
    claim, receipt = _build_to_compensation_intent(root)
    fence = int(claim["fence"])
    comp_obs = _observation_dict(classification="applied", fence=fence)
    _append(
        root,
        "effect-1",
        "compensation_observation",
        _compensation_observation_payload(receipt, comp_obs, fence=fence),
        "effect-1:comp-obs",
        "2026-08-02T00:00:05Z",
    )
    return claim, receipt, comp_obs




# ---------------------------------------------------------------------------
# P1.2 / P1.1 exact binding matrix + callable harness (4A.5B.9)
# ---------------------------------------------------------------------------

from tests.workflow_kernel.cross_event_binding_harness import (
    ALL_PATH_BINDING_CASES_V1,
    BINDING_BOUNDARY_EXPECTATIONS_V1,
    CROSS_EVENT_BINDING_CASES,
    CROSS_EVENT_CASE_CONTRACTS,
    DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1,
    EXACT_17_KEYS,
    INDEPENDENT_DESCRIPTOR_EXPECTATIONS_V1,
    PARSER_CONTRACT_CASES_V1,
    PARSER_CONTRACT_EXPECTATIONS_V1,
    PARSER_CASE_IDS_V1,
    PRIMARY_BINDING_IDS,
    EXECUTABLE_BINDING_IDS,
    RUNNER_REQUIRED_KEYS_V1,
    RUNNER_REQUIRED_TRUE_V1,
    HarnessSabotage,
    assert_binding_expectations_exact_cover,
    assert_descriptor_schema_keys as _assert_descriptor_schema_keys,
    assert_exact_boundary_message as _assert_exact_boundary_message,
    assert_runner_contract as _assert_runner_contract,
    assert_zero_duplicate_execution_groups,
    build_deps_from_test_module,
    run_cross_event_binding_case,
)
from workflow_kernel.effect_ledger_cross_relation_descriptors import (
    CROSS_EVENT_RELATION_DESCRIPTORS_V1,
    EXACT_CROSS_DESCRIPTOR_KEYS_V1,
)

assert EXACT_17_KEYS == EXACT_CROSS_DESCRIPTOR_KEYS_V1

def _recompute_receipt_digest(receipt: dict) -> dict:
    body = dict(receipt)
    body.pop("receipt_digest", None)
    body["receipt_digest"] = effect_contracts.canonical_digest_v1(body)
    return body


def _build_accept_chain(root: Path, accept_builder: str) -> dict:
    auth = _precall_for(root)
    meta: dict = {
        "builder": accept_builder,
        "precall_idempotency_docs": auth.expected_docs(),
        "precall_authority": auth,
    }
    if accept_builder == "claim":
        claim = _claim(root)
        meta.update(claim=claim, fence=int(claim["fence"]))
    elif accept_builder == "intent":
        claim = _claim(root)
        fence = int(claim["fence"])
        _append(root, "effect-1", "intent_recorded", _intent_payload(fence=fence), "effect-1:intent", "2026-08-02T00:00:01Z")
        meta.update(claim=claim, fence=fence)
    elif accept_builder == "observation":
        claim = _claim(root)
        fence = int(claim["fence"])
        _append(root, "effect-1", "intent_recorded", _intent_payload(fence=fence), "effect-1:intent", "2026-08-02T00:00:01Z")
        _append(root, "effect-1", "observation_recorded", _observation_payload(fence=fence, classification="applied"), "effect-1:obs", "2026-08-02T00:00:02Z")
        meta.update(claim=claim, fence=fence)
    elif accept_builder == "reconciled":
        claim = _claim(root)
        fence = int(claim["fence"])
        _append(root, "effect-1", "intent_recorded", _intent_payload(fence=fence), "effect-1:intent", "2026-08-02T00:00:01Z")
        _append(root, "effect-1", "observation_recorded", _observation_payload(fence=fence, classification="unknown"), "effect-1:obs", "2026-08-02T00:00:02Z")
        recon_readback = {"present": True, "content_digest": DIGEST, "via": "reconcile"}
        _append(root, "effect-1", "reconciled", _reconcile_payload(fence=fence, classification="applied", readback=recon_readback), "effect-1:reconcile", "2026-08-02T00:00:03Z")
        meta.update(claim=claim, fence=fence, recon_readback=recon_readback)
    elif accept_builder == "applied_terminal":
        claim, receipt = _build_to_applied_terminal(root, classification="applied")
        meta.update(claim=claim, fence=int(claim["fence"]), receipt=receipt)
    elif accept_builder == "not_applied_terminal":
        claim, receipt = _build_to_applied_terminal(root, classification="not_applied")
        meta.update(claim=claim, fence=int(claim["fence"]), receipt=receipt)
    elif accept_builder == "compensation_intent":
        claim, receipt = _build_to_compensation_intent(root)
        meta.update(claim=claim, fence=int(claim["fence"]), receipt=receipt)
    elif accept_builder == "compensation_observation":
        claim, receipt, comp_obs = _build_to_compensation_observation(root)
        meta.update(claim=claim, fence=int(claim["fence"]), receipt=receipt, comp_obs=comp_obs)
    else:
        raise AssertionError(f"unknown accept_builder {accept_builder}")
    # Carry independent pre-call expected map authored from call arguments only.
    meta["precall_idempotency_docs"] = auth.expected_docs()
    meta["precall_authored_events"] = [dict(e) for e in auth.events]
    return meta


def _good_destination_payload(relation_id: str, root: Path, meta: dict) -> tuple[str, dict, str, str]:
    fence = int(meta["fence"])
    events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-1")
    if relation_id in {
        "cross_event.claim_to_intent_fence",
        "cross_event.claim_to_intent_complete_facts",
        "cross_event.envelope_idempotency_replay_identity",
        "cross_event.state_event_type_transition",
        "cross_event.effect_id_chain_mixed_effect",
        "cross_event.sequence_contiguity",
        "cross_event.previous_event_id_link",
    }:
        return "intent_recorded", _intent_payload(fence=fence), "effect-1:intent", "2026-08-02T00:00:01Z"
    if relation_id in {
        "cross_event.claim_to_observation_facts",
        "cross_event.claim_to_observation_fence",
        "cross_event.claim_to_observation_authority",
    }:
        return "observation_recorded", _observation_payload(fence=fence), "effect-1:obs", "2026-08-02T00:00:02Z"
    if relation_id in {
        "cross_event.claim_to_reconcile_facts",
        "cross_event.claim_to_reconcile_fence",
    }:
        return "reconciled", _reconcile_payload(fence=fence), "effect-1:reconcile", "2026-08-02T00:00:02Z"
    if relation_id in {
        "cross_event.claim_to_receipt_facts",
        "cross_event.claim_to_receipt_identity_facts",
        "cross_event.claim_to_receipt_fence",
        "cross_event.claim_to_receipt_authority",
        "cross_event.receipt_target_body_digest",
        "cross_event.receipt_target_claim_binding",
        "cross_event.intent_preflight_to_receipt",
        "cross_event.observation_to_receipt_body",
        "cross_event.observation_to_receipt_classification",
        "cross_event.observation_to_receipt_post_readback",
    }:
        durable = _as_plain([e for e in events if e["event_type"] == "observation_recorded"][-1]["payload"]["observation"])
        return ("receipt_terminal", _receipt_payload(fence=fence, classification="applied", observation=_as_plain(durable)), "effect-1:receipt", "2026-08-02T00:00:03Z")
    if relation_id in {"cross_event.reconcile_to_receipt_post_readback", "cross_event.reconcile_to_receipt_classification"}:
        durable_obs = _as_plain([e for e in events if e["event_type"] == "observation_recorded"][-1]["payload"]["observation"])
        recon = _as_plain([e for e in events if e["event_type"] == "reconciled"][-1]["payload"])
        good_r = _receipt_dict(fence=fence, classification=str(recon["classification"]))
        del good_r["receipt_digest"]
        good_r["post_readback"] = dict(recon["readback"])
        good_r = _recompute_receipt_digest(good_r)
        return (
            "receipt_terminal",
            _receipt_payload(fence=fence, classification=str(recon["classification"]), receipt=good_r, receipt_digest=good_r["receipt_digest"], observation=_as_plain(durable_obs)),
            "effect-1:receipt",
            "2026-08-02T00:00:04Z",
        )
    if relation_id in {
        "cross_event.claim_to_compensation_facts",
        "cross_event.claim_to_compensation_fence",
        "cross_event.compensation_requires_applied_terminal_state",
    }:
        receipt = _as_plain([e for e in events if e["event_type"] == "receipt_terminal"][-1]["payload"]["receipt"])
        return ("compensation_intent", _compensation_intent_payload(receipt, fence=fence), "effect-1:comp-intent", "2026-08-02T00:00:04Z")
    if relation_id in {
        "cross_event.compensation_intent_local_facts_to_observation",
        "cross_event.compensation_original_receipt_bind_observation",
        "cross_event.compensation_fence_to_observation",
    }:
        receipt = _as_plain([e for e in events if e["event_type"] == "receipt_terminal"][-1]["payload"]["receipt"])
        comp_obs = _observation_dict(classification="applied", fence=fence)
        return ("compensation_observation", _compensation_observation_payload(receipt, comp_obs, fence=fence), "effect-1:comp-obs", "2026-08-02T00:00:05Z")
    if relation_id in {
        "cross_event.compensation_intent_local_facts_to_receipt",
        "cross_event.compensation_original_receipt_bind_receipt",
        "cross_event.compensation_fence_to_receipt",
        "cross_event.compensation_intent_preflight_to_compensation_receipt",
        "cross_event.compensation_observation_to_receipt_chain",
    }:
        receipt = _as_plain([e for e in events if e["event_type"] == "receipt_terminal"][-1]["payload"]["receipt"])
        comp_obs = _as_plain([e for e in events if e["event_type"] == "compensation_observation"][-1]["payload"]["observation"])
        good_comp_receipt = _compensation_receipt_dict(fence=fence, classification="applied", post=_as_plain(comp_obs["readback"]))
        payload = {
            "adapter_id": "test.injected_adapter",
            "authority_binding_digest": DIGEST,
            "command_digest": DIGEST,
            "effect_id": "effect-1",
            "fence": fence,
            "idempotency_key": "effect-cmd-1",
            "observation": _as_plain(comp_obs),
            "observation_digest": effect_contracts.canonical_digest_v1(comp_obs),
            "operation": "noop_apply",
            "original_receipt": _as_plain(receipt),
            "original_receipt_digest": receipt["receipt_digest"],
            "payload_digest": DIGEST,
            "receipt": good_comp_receipt,
            "receipt_digest": good_comp_receipt["receipt_digest"],
            "request_digest": OTHER_DIGEST,
            "target_digest": TARGET_DIGEST,
        }
        return "compensation_receipt", payload, "effect-1:comp-receipt", "2026-08-02T00:00:06Z"
    raise AssertionError(relation_id)


def _apply_bookkeeping(event: dict, paths: tuple[str, ...]) -> None:
    for p in paths:
        if p == "event_id":
            body = {k: v for k, v in event.items() if k != "event_id"}
            event["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
        elif p == "payload.effect_id":
            event["payload"]["effect_id"] = event["effect_id"]
        elif p == "payload.observation_digest":
            event["payload"]["observation_digest"] = effect_contracts.canonical_digest_v1(event["payload"]["observation"])
        elif p == "payload.receipt_digest":
            event["payload"]["receipt_digest"] = event["payload"]["receipt"]["receipt_digest"]
        elif p == "payload.receipt.receipt_digest":
            event["payload"]["receipt"] = _recompute_receipt_digest(dict(event["payload"]["receipt"]))
            event["payload"]["receipt_digest"] = event["payload"]["receipt"]["receipt_digest"]
        elif p == "payload.receipt.command_digest":
            pass
        elif p in {
            "payload.receipt.adapter_id",
            "payload.receipt.operation",
            "payload.receipt.idempotency_key",
            "payload.receipt.authority_binding_digest",
            "payload.receipt.payload_digest",
        }:
            # already applied in mutator when present
            pass
        elif p == "payload.receipt.result_classification":
            r = dict(event["payload"]["receipt"])
            r["result_classification"] = event["payload"]["result_classification"]
            event["payload"]["receipt"] = _recompute_receipt_digest(r)
            event["payload"]["receipt_digest"] = event["payload"]["receipt"]["receipt_digest"]
        elif p == "payload.original_receipt_digest":
            event["payload"]["original_receipt_digest"] = event["payload"]["original_receipt"]["receipt_digest"]
        elif p == "payload.observation.fence":
            event["payload"]["observation"] = dict(event["payload"]["observation"])
            event["payload"]["observation"]["fence"] = event["payload"]["fence"]
            if "observation_digest" in event["payload"]:
                event["payload"]["observation_digest"] = effect_contracts.canonical_digest_v1(
                    event["payload"]["observation"]
                )
        elif p == "payload.observation.classification":
            event["payload"]["observation"] = dict(event["payload"]["observation"])
            event["payload"]["observation"]["classification"] = event["payload"].get("result_classification") or event["payload"]["observation"].get("classification")
        elif p == "payload.receipt.fence":
            r = dict(event["payload"]["receipt"])
            r["fence"] = event["payload"]["fence"]
            event["payload"]["receipt"] = _recompute_receipt_digest(r)
            event["payload"]["receipt_digest"] = event["payload"]["receipt"]["receipt_digest"]


def _mutate_accepted_good_event(contract, good_event: dict, meta: dict) -> dict:
    bad = copy.deepcopy(_as_plain(good_event))  # type: ignore[arg-type]
    assert isinstance(bad, dict)
    fence = int(meta["fence"])
    rid = contract.relation_id
    mut = contract.semantic_mutated_paths[0]
    if rid == "cross_event.effect_id_chain_mixed_effect":
        bad["effect_id"] = "effect-OTHER"
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["effect_id"] = "effect-OTHER"
    elif rid == "cross_event.sequence_contiguity":
        bad["sequence"] = 99
    elif rid == "cross_event.previous_event_id_link":
        bad["previous_event_id"] = "wel_" + ("ab" * 32)
    elif rid == "cross_event.state_event_type_transition":
        bad["event_type"] = "receipt_terminal"
    elif rid == "cross_event.envelope_idempotency_replay_identity":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["authority_binding_digest"] = OTHER_DIGEST
    elif mut == "payload.fence":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["fence"] = fence + 1
    elif mut == "payload.command_digest":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["command_digest"] = OTHER_DIGEST
        if "payload.receipt.command_digest" in contract.derived_bookkeeping_paths and isinstance(bad["payload"].get("receipt"), dict):
            r = dict(bad["payload"]["receipt"])
            del r["receipt_digest"]
            r["command_digest"] = OTHER_DIGEST
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.authority_binding_digest":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["authority_binding_digest"] = OTHER_DIGEST
        if "payload.receipt.authority_binding_digest" in contract.derived_bookkeeping_paths and isinstance(
            bad["payload"].get("receipt"), dict
        ):
            r = dict(bad["payload"]["receipt"])
            del r["receipt_digest"]
            r["authority_binding_digest"] = OTHER_DIGEST
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut in {
        "payload.receipt.target",
        "payload.receipt.target.target_kind",
        "payload.receipt.target.target_ref",
    } or (
        any(
            p == "payload.receipt.target" or p.startswith("payload.receipt.target.")
            for p in contract.semantic_mutated_paths
        )
        and mut == "payload.target_digest"
    ):
        # Intra-coherent target mutations:
        # - body-digest tuples mutate only the chosen leaf/root and keep wrapper digest,
        #   so the first native boundary is body/digest.
        # - claim-binding tuples mutate chosen leaf/root AND recompute wrapper digest so
        #   body/digest passes and claim binding fails.
        bad["payload"] = dict(bad["payload"])
        r = dict(bad["payload"]["receipt"])
        old_target = dict(r.get("target") or {})
        if mut == "payload.receipt.target.target_kind" or (
            "payload.receipt.target.target_kind" in contract.semantic_mutated_paths
            and "payload.receipt.target.target_ref" not in contract.semantic_mutated_paths
            and "payload.receipt.target" not in contract.semantic_mutated_paths
        ):
            new_target = {"target_kind": "other_kind", "target_ref": old_target.get("target_ref", "target-1")}
        elif mut == "payload.receipt.target.target_ref" or (
            "payload.receipt.target.target_ref" in contract.semantic_mutated_paths
            and "payload.receipt.target.target_kind" not in contract.semantic_mutated_paths
            and "payload.receipt.target" not in contract.semantic_mutated_paths
        ):
            new_target = {"target_kind": old_target.get("target_kind", "local_exact"), "target_ref": "other-target"}
        else:
            new_target = {"target_kind": "local_exact", "target_ref": "other-target"}
        del r["receipt_digest"]
        r["target"] = new_target
        bad["payload"]["receipt"] = _recompute_receipt_digest(r)
        bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
        if "payload.target_digest" in contract.semantic_mutated_paths:
            # Keep body/digest coherent so claim-binding is the first boundary.
            bad["payload"]["target_digest"] = effect_contracts.canonical_digest_v1(new_target)
    elif mut == "payload.target_digest":
        # Lone wrapper digest flip (no target body semantic path).
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["target_digest"] = OTHER_DIGEST
    elif mut == "payload.receipt.preflight_readback":
        bad["payload"] = dict(bad["payload"])
        r = dict(bad["payload"]["receipt"])
        del r["receipt_digest"]
        r["preflight_readback"] = {"ok": False, "tampered": True}
        bad["payload"]["receipt"] = _recompute_receipt_digest(r)
        bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.observation.observation_id":
        bad["payload"] = dict(bad["payload"])
        obs = dict(bad["payload"]["observation"])
        obs["observation_id"] = "obs-CROSS-TAMPER"
        bad["payload"]["observation"] = obs
        # Keep observation_digest coherent so body/equality boundary fires first.
        if "observation_digest" in bad["payload"] or "payload.observation_digest" in contract.derived_bookkeeping_paths:
            bad["payload"]["observation_digest"] = effect_contracts.canonical_digest_v1(obs)
    elif mut == "payload.result_classification":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["result_classification"] = "not_applied"
        if isinstance(bad["payload"].get("receipt"), dict):
            r = dict(bad["payload"]["receipt"])
            del r["receipt_digest"]
            r["result_classification"] = "not_applied"
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.receipt.post_readback":
        bad["payload"] = dict(bad["payload"])
        r = dict(bad["payload"]["receipt"])
        del r["receipt_digest"]
        r["post_readback"] = {"present": False, "content_digest": OTHER_DIGEST, "wrong": True}
        bad["payload"]["receipt"] = _recompute_receipt_digest(r)
        bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.original_receipt":
        bad["payload"] = dict(bad["payload"])
        orig = dict(bad["payload"]["original_receipt"])
        del orig["receipt_digest"]
        orig["operation"] = "tampered-op"
        bad["payload"]["original_receipt"] = _recompute_receipt_digest(orig)
        bad["payload"]["original_receipt_digest"] = bad["payload"]["original_receipt"]["receipt_digest"]
    elif mut == "payload.adapter_id":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["adapter_id"] = "other.adapter"
        if "payload.receipt.adapter_id" in contract.derived_bookkeeping_paths and isinstance(bad["payload"].get("receipt"), dict):
            r = dict(bad["payload"]["receipt"])
            del r["receipt_digest"]
            r["adapter_id"] = "other.adapter"
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.operation":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["operation"] = "other_op"
        if "payload.receipt.operation" in contract.derived_bookkeeping_paths and isinstance(bad["payload"].get("receipt"), dict):
            r = dict(bad["payload"]["receipt"])
            del r["receipt_digest"]
            r["operation"] = "other_op"
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.idempotency_key":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["idempotency_key"] = "other-key"
        if "payload.receipt.idempotency_key" in contract.derived_bookkeeping_paths and isinstance(bad["payload"].get("receipt"), dict):
            r = dict(bad["payload"]["receipt"])
            del r["receipt_digest"]
            r["idempotency_key"] = "other-key"
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.request_digest":
        bad["payload"] = dict(bad["payload"])
        # claim uses DIGEST; flip to OTHER_DIGEST (or reverse if already OTHER)
        cur = bad["payload"].get("request_digest")
        bad["payload"]["request_digest"] = OTHER_DIGEST if cur == DIGEST else DIGEST
    elif mut == "payload.target_digest" and "payload.receipt.target" not in contract.semantic_mutated_paths:
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["target_digest"] = OTHER_DIGEST
    elif mut == "payload.payload_digest":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["payload_digest"] = OTHER_DIGEST
        if "payload.receipt.payload_digest" in contract.derived_bookkeeping_paths and isinstance(bad["payload"].get("receipt"), dict):
            r = dict(bad["payload"]["receipt"])
            del r["receipt_digest"]
            r["payload_digest"] = OTHER_DIGEST
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.receipt.compensation_ref":
        bad["payload"] = dict(bad["payload"])
        r = dict(bad["payload"]["receipt"])
        del r["receipt_digest"]
        r["compensation_ref"] = "compensation:tampered.ref"
        bad["payload"]["receipt"] = _recompute_receipt_digest(r)
        bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut == "payload.compensation_ref":
        bad["payload"] = dict(bad["payload"])
        bad["payload"]["compensation_ref"] = "compensation:tampered.ref"
    elif mut == "payload.observation":
        bad["payload"] = dict(bad["payload"])
        obs = dict(bad["payload"].get("observation") or {})
        obs["observation_id"] = str(obs.get("observation_id") or "obs") + "-TAMPER"
        bad["payload"]["observation"] = obs
        if "observation_digest" in bad["payload"] or "payload.observation_digest" in contract.derived_bookkeeping_paths:
            bad["payload"]["observation_digest"] = effect_contracts.canonical_digest_v1(obs)
    elif mut.startswith("payload.observation."):
        bad["payload"] = dict(bad["payload"])
        obs = dict(bad["payload"].get("observation") or {})
        leaf = mut[len("payload.observation.") :]
        if leaf == "fence":
            obs["fence"] = int(obs.get("fence") or 0) + 1
        elif leaf in {"classification"}:
            obs["classification"] = "not_applied" if obs.get("classification") == "applied" else "applied"
        elif leaf in {"effect_id"}:
            obs["effect_id"] = "effect-OTHER"
        elif leaf in {"observation_id"}:
            obs["observation_id"] = "obs-CROSS-TAMPER"
        elif leaf in {"observed_at"}:
            obs["observed_at"] = "2099-01-01T00:00:00Z"
        elif leaf in {"schema_version"}:
            obs["schema_version"] = "effect_observation_v1_TAMPER"
        elif leaf in {"adapter_result_digest"}:
            obs["adapter_result_digest"] = OTHER_DIGEST
        elif leaf in {"readback"}:
            obs["readback"] = {"tampered": True, "present": True}
        else:
            obs[leaf] = "TAMPER"
        bad["payload"]["observation"] = obs
        if "observation_digest" in bad["payload"] or "payload.observation_digest" in contract.derived_bookkeeping_paths:
            bad["payload"]["observation_digest"] = effect_contracts.canonical_digest_v1(obs)
        # Keep compensation-receipt internal classification fields coherent with observation
        # ONLY when the semantic mutation is not itself the classification leaf (which must
        # produce the classification-vs-observation boundary).
        if (
            contract.relation_id == "cross_event.compensation_observation_to_receipt_chain"
            and leaf == "classification"
            and "payload.observation.classification" not in contract.semantic_mutated_paths
        ):
            if isinstance(bad["payload"].get("receipt"), dict):
                r = dict(bad["payload"]["receipt"])
                if "receipt_digest" in r:
                    del r["receipt_digest"]
                r["result_classification"] = obs["classification"]
                bad["payload"]["receipt"] = _recompute_receipt_digest(r)
                bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
    elif mut in {"payload.readback", "payload.classification", "payload.readback_digest", "payload.preflight_readback", "payload.preflight_readback_digest"}:
        bad["payload"] = dict(bad["payload"])
        if mut == "payload.readback":
            bad["payload"]["readback"] = {"tampered": True}
        elif mut == "payload.classification":
            bad["payload"]["classification"] = "not_applied" if bad["payload"].get("classification") == "applied" else "applied"
        elif mut == "payload.readback_digest":
            bad["payload"]["readback_digest"] = OTHER_DIGEST
        elif mut == "payload.preflight_readback":
            bad["payload"]["preflight_readback"] = {"ok": False, "tampered": True}
        elif mut == "payload.preflight_readback_digest":
            bad["payload"]["preflight_readback_digest"] = OTHER_DIGEST
    elif mut.startswith("payload.receipt.") or mut in {"payload.receipt", "payload.receipt_digest"}:
        bad["payload"] = dict(bad["payload"])
        if mut == "payload.receipt_digest":
            # Incoherent wrapper digest only.
            bad["payload"]["receipt_digest"] = OTHER_DIGEST
        else:
            r = dict(bad["payload"].get("receipt") or {})
            if "receipt_digest" in r:
                del r["receipt_digest"]
            if mut == "payload.receipt":
                r["operation"] = "other_op"
                bad["payload"]["operation"] = "other_op"
            else:
                leaf = mut[len("payload.receipt.") :]
                if leaf == "target":
                    r["target"] = {"target_kind": "local_exact", "target_ref": "other-target"}
                elif leaf == "target.target_kind":
                    tgt = dict(r.get("target") or {})
                    r["target"] = {"target_kind": "other_kind", "target_ref": tgt.get("target_ref", "target-1")}
                elif leaf == "target.target_ref":
                    tgt = dict(r.get("target") or {})
                    r["target"] = {"target_kind": tgt.get("target_kind", "local_exact"), "target_ref": "other-target"}
                elif leaf == "fence":
                    r["fence"] = int(r.get("fence") or 0) + 1
                elif leaf.endswith("_digest") or leaf in {"adapter_id", "operation", "idempotency_key", "effect_id", "compensation_ref", "reconcile_ref", "schema_version", "result_classification"}:
                    if leaf == "result_classification":
                        r[leaf] = "not_applied" if r.get(leaf) == "applied" else "applied"
                    elif leaf == "fence":
                        r[leaf] = int(r.get(leaf) or 0) + 1
                    elif leaf.endswith("_digest"):
                        r[leaf] = OTHER_DIGEST
                    elif leaf == "adapter_id":
                        r[leaf] = "other.adapter"
                    elif leaf == "operation":
                        r[leaf] = "other_op"
                    elif leaf == "idempotency_key":
                        r[leaf] = "other-key"
                    elif leaf == "effect_id":
                        r[leaf] = "effect-OTHER"
                    else:
                        r[leaf] = f"other-{leaf.replace('_', '-')}"
                elif leaf in {"preflight_readback", "post_readback"}:
                    r[leaf] = {"tampered": True, "present": True}
                else:
                    cur = r
                    parts = leaf.split(".")
                    for p in parts[:-1]:
                        cur[p] = dict(cur.get(p) or {})
                        cur = cur[p]
                    cur[parts[-1]] = "TAMPER"
            bad["payload"]["receipt"] = _recompute_receipt_digest(r)
            bad["payload"]["receipt_digest"] = bad["payload"]["receipt"]["receipt_digest"]
        # Compensation receipt: for result_classification semantic mutation, do NOT
        # auto-sync observation.classification — that is the classification boundary.
        if (
            contract.relation_id == "cross_event.compensation_observation_to_receipt_chain"
            and mut == "payload.receipt.result_classification"
            and False
        ):
            pass
    elif mut.startswith("payload.original_receipt") or mut == "payload.original_receipt_digest":
        bad["payload"] = dict(bad["payload"])
        orig = dict(bad["payload"].get("original_receipt") or {})
        if "receipt_digest" in orig:
            del orig["receipt_digest"]
        if mut in {"payload.original_receipt", "payload.original_receipt_digest"}:
            orig["operation"] = str(orig.get("operation") or "op") + "-TAMPER"
        else:
            leaf = mut[len("payload.original_receipt.") :]
            if leaf == "target":
                orig["target"] = {"target_kind": "local_exact", "target_ref": "other-target"}
            elif leaf == "target.target_kind":
                tgt = dict(orig.get("target") or {})
                orig["target"] = {"target_kind": "other_kind", "target_ref": tgt.get("target_ref", "t")}
            elif leaf == "target.target_ref":
                tgt = dict(orig.get("target") or {})
                orig["target"] = {"target_kind": tgt.get("target_kind", "local_exact"), "target_ref": "other-target"}
            elif leaf == "fence":
                orig["fence"] = int(orig.get("fence") or 0) + 1
            elif leaf == "result_classification":
                orig[leaf] = "not_applied" if orig.get(leaf) == "applied" else "applied"
            elif leaf == "receipt_digest":
                # Incoherent digest twin: do not recompute from body after this assignment.
                orig["receipt_digest"] = OTHER_DIGEST
                bad["payload"]["original_receipt"] = orig
                bad["payload"]["original_receipt_digest"] = OTHER_DIGEST if mut.endswith("receipt_digest") else bad["payload"].get("original_receipt_digest", OTHER_DIGEST)
                body = {k: v for k, v in bad.items() if k != "event_id"}
                bad["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
                return bad
            elif leaf.endswith("_digest"):
                orig[leaf] = OTHER_DIGEST
            elif leaf in {"preflight_readback", "post_readback"}:
                orig[leaf] = {"tampered": True, "present": True}
            elif leaf == "adapter_id":
                orig[leaf] = "other.adapter"
            elif leaf == "operation":
                orig[leaf] = "other_op"
            elif leaf == "idempotency_key":
                orig[leaf] = "other-key"
            elif leaf == "effect_id":
                orig[leaf] = "effect-OTHER"
            elif leaf == "schema_version":
                orig[leaf] = "effect_receipt_v1_TAMPER"
            elif leaf == "compensation_ref":
                orig[leaf] = "compensation:other.ref"
            elif leaf == "reconcile_ref":
                orig[leaf] = "reconcile:other.ref"
            else:
                orig[leaf] = f"other-{leaf.replace('_', '-')}"
        if mut == "payload.original_receipt.receipt_digest":
            # Handled by early return above.
            raise AssertionError("original_receipt.receipt_digest mutation should early-return")
        bad["payload"]["original_receipt"] = _recompute_receipt_digest(orig)
        bad["payload"]["original_receipt_digest"] = bad["payload"]["original_receipt"]["receipt_digest"]
    else:
        raise AssertionError(f"no mutation handler for {rid} mut={mut} paths={contract.semantic_mutated_paths}")
    _apply_bookkeeping(bad, contract.derived_bookkeeping_paths)
    # Any body mutation invalidates event_id; always recompute for exact twin bytes.
    body = {k: v for k, v in bad.items() if k != "event_id"}
    bad["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
    return bad


def _strip_payload_prefix(path: str) -> str:
    return path[len("payload.") :] if path.startswith("payload.") else path


def _assert_diff_exact(
    good: object,
    bad: object,
    contract,
    *,
    payload_only: bool = False,
) -> None:
    if payload_only:
        semantic = tuple(_strip_payload_prefix(p) for p in contract.semantic_mutated_paths)
        bookkeeping = tuple(_strip_payload_prefix(p) for p in contract.derived_bookkeeping_paths)
    else:
        semantic = contract.semantic_mutated_paths
        bookkeeping = contract.derived_bookkeeping_paths
    diffs = _semantic_diff_paths(good, bad)
    collapsed = list(diffs)
    for sm in semantic:
        collapsed = _collapse_diff_paths(collapsed, sm, list(bookkeeping))
    expected = set(semantic) | set(bookkeeping)
    assert set(collapsed) == expected, (contract.relation_id, collapsed, expected, diffs)


def _materialize_good_event(root: Path, et: str, payload: dict, key: str, at: str) -> dict:
    """Author destination event from setup pre-call chain state (peek only; append consumes)."""
    auth = _precall_for(root)
    effect_id = "effect-1"
    plain_payload = _as_plain(payload)
    assert isinstance(plain_payload, dict)
    # Peek next sequence/prev without mutating authority — public append authors the doc.
    seq = auth._seq_by_effect.get(effect_id, 0) + 1
    prev = auth._prev_by_effect.get(effect_id)
    ev = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": effect_id,
        "sequence": seq,
        "previous_event_id": prev,
        "idempotency_key": key,
        "occurred_at": at,
        "event_type": et,
        "payload": plain_payload,
    }
    body = {k: v for k, v in ev.items() if k != "event_id"}
    ev["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
    return ev

def _deps():
    import sys
    return build_deps_from_test_module(sys.modules[__name__])


def test_cross_event_case_contracts_exact_cover_and_17_keys() -> None:
    from workflow_kernel.effect_ledger_relation_manifests import CROSS_EVENT_RELATION_MANIFEST_V1

    ids = set(CROSS_EVENT_RELATION_MANIFEST_V1)
    assert set(CROSS_EVENT_CASE_CONTRACTS) == ids
    assert len(CROSS_EVENT_CASE_CONTRACTS) == len(CROSS_EVENT_RELATION_DESCRIPTORS_V1)
    assert set(INDEPENDENT_DESCRIPTOR_EXPECTATIONS_V1) == ids
    assert_binding_expectations_exact_cover()
    # Every binding expectation has a case row
    assert set(CROSS_EVENT_BINDING_CASES) == {b.binding_id for b in BINDING_BOUNDARY_EXPECTATIONS_V1}
    for b in BINDING_BOUNDARY_EXPECTATIONS_V1:
        case = CROSS_EVENT_BINDING_CASES[b.binding_id]
        assert case.expected_code == b.expected_code
        assert case.expected_message == b.expected_message
        assert case.validator_function == b.validator_function
        assert case.destination_path == b.destination_path
        assert case.source_path == b.source_path
        assert "{" not in case.expected_message
    ssot = _ssot_cross_rows()
    assert set(ssot) == ids
    modes = set()
    for rid, contract in CROSS_EVENT_CASE_CONTRACTS.items():
        row = ssot[rid]
        assert set(row) == EXACT_17_KEYS, (rid, sorted(set(row) ^ EXACT_17_KEYS))
        exp = INDEPENDENT_DESCRIPTOR_EXPECTATIONS_V1[rid]
        assert row["validator_function"] == exp["validator_function"] == contract.validator_function
        assert row["validator_boundary"] == exp["validator_boundary"]
        assert contract.comparison == row["comparison"] == exp["comparison"]
        assert set(str(contract.source_event).split("|")) == set(str(row["source_event"]).split("|")) == set(
            str(exp["source_event"]).split("|")
        )
        assert set(str(contract.destination_event).split("|")) == set(str(row["destination_event"]).split("|")) == set(
            str(exp["destination_event"]).split("|")
        )
        assert row["focused_test_id"] == f"test_cross_event_relation_enforced[{rid}]"
        assert row["enforcement"] == "python_replay_only"
        assert row["scope"] == "cross_event_python_replay"
        assert contract.semantic_mutated_paths
        assert contract.expected_code
        assert contract.state_preservation in {"append_reject_unchanged", "replay_readonly_copy"}
        modes.add(contract.state_preservation)
    assert modes == {"append_reject_unchanged", "replay_readonly_copy"}
    for row in CROSS_EVENT_RELATION_DESCRIPTORS_V1:
        assert set(row) == EXACT_17_KEYS
        rid = row["relation_id"]
        exp = INDEPENDENT_DESCRIPTOR_EXPECTATIONS_V1[rid]
        assert row["validator_function"] == exp["validator_function"]
        assert row["validator_boundary"] == exp["validator_boundary"]


def _ssot_cross_rows() -> dict[str, dict]:
    import json
    from pathlib import Path as P

    ssot = json.loads(
        (
            P(__file__).resolve().parents[2]
            / "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json"
        ).read_text(encoding="utf-8")
    )
    return {r["relation_id"]: r for r in ssot["cross_event_relations"]}


def test_path_inventory_cross_event_precision_regressions() -> None:
    import json
    from pathlib import Path as P

    from workflow_kernel.effect_ledger_cross_relation_descriptors import FALSE_INTENT_MEMBERSHIPS_V1

    ssot = json.loads(
        (P(__file__).resolve().parents[2] / "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json").read_text(encoding="utf-8")
    )
    by = {(r["event_type"], r["path"]): r for r in ssot["path_inventory"]}

    def assert_cross(et: str, path: str, *must_include: str) -> None:
        row = by[(et, path)]
        assert row["classification"] == "cross_event_replay_relation", (et, path, row)
        ids = set(row.get("relation_ids") or [row["relation_id"]])
        for mid in must_include:
            assert mid in ids, (et, path, ids, mid)

    terminal_must = [
        "payload.receipt",
        "payload.receipt.adapter_id",
        "payload.receipt.authority_binding_digest",
        "payload.receipt.command_digest",
        "payload.receipt.compensation_ref",
        "payload.receipt.effect_id",
        "payload.receipt.fence",
        "payload.receipt.idempotency_key",
        "payload.receipt.operation",
        "payload.receipt.payload_digest",
        "payload.receipt.receipt_digest",
        "payload.receipt.reconcile_ref",
        "payload.receipt.result_classification",
        "payload.receipt.schema_version",
    ]
    assert len(terminal_must) == 14
    for path in terminal_must:
        ids = set(by[("receipt_terminal", path)].get("relation_ids") or [by[("receipt_terminal", path)]["relation_id"]])
        assert ids & {
            "cross_event.compensation_original_receipt_bind_observation",
            "cross_event.compensation_original_receipt_bind_receipt",
        }, (path, ids)

    for et in ("compensation_intent", "compensation_observation", "compensation_receipt"):
        for leaf in (
            "payload.original_receipt.effect_id",
            "payload.original_receipt.operation",
            "payload.original_receipt.result_classification",
        ):
            ids = set(by[(et, leaf)].get("relation_ids") or [by[(et, leaf)]["relation_id"]])
            assert ids & {
                "cross_event.compensation_original_receipt_bind_observation",
                "cross_event.compensation_original_receipt_bind_receipt",
            }, (et, leaf, ids)

    assert len(FALSE_INTENT_MEMBERSHIPS_V1) == 32
    for et, path, rid in FALSE_INTENT_MEMBERSHIPS_V1:
        ids = set(by[(et, path)].get("relation_ids") or [by[(et, path)].get("relation_id")])
        assert rid not in ids, (et, path, rid, ids)

    assert_cross("intent_recorded", "payload.authority_binding_digest", "cross_event.claim_to_observation_authority")
    assert_cross("intent_recorded", "payload.authority_binding_digest", "cross_event.claim_to_receipt_authority")
    assert_cross("receipt_terminal", "payload.receipt.target.target_kind", "cross_event.receipt_target_body_digest")
    assert_cross("receipt_terminal", "payload.receipt.target.target_kind", "cross_event.receipt_target_claim_binding")
    assert_cross("observation_recorded", "payload.command_digest", "cross_event.claim_to_observation_facts")
    assert_cross("observation_recorded", "payload.fence", "cross_event.claim_to_observation_fence")

    for et in ("compensation_intent", "compensation_observation", "compensation_receipt"):
        for path in ("payload.request_digest", "payload.authority_binding_digest"):
            ids = set(by[(et, path)].get("relation_ids") or [by[(et, path)].get("relation_id")])
            assert "cross_event.claim_to_compensation_facts" not in ids
            assert ids & {
                "cross_event.compensation_intent_local_facts_to_observation",
                "cross_event.compensation_intent_local_facts_to_receipt",
            }

    for (et, path) in by:
        if et == "reconciled":
            assert not str(path).startswith("payload.observation"), (et, path)

    for c in ssot["cross_event_relations"]:
        assert set(c) == EXACT_17_KEYS, c["relation_id"]
        if c["relation_id"] in {
            "cross_event.compensation_requires_applied_terminal_state",
            "cross_event.compensation_original_receipt_bind_observation",
            "cross_event.compensation_original_receipt_bind_receipt",
        }:
            for b in c.get("source_bindings") or []:
                if b.get("event") == "receipt_terminal":
                    for p in b.get("paths") or []:
                        assert not str(p).startswith("payload.original_receipt"), p


def test_cross_event_descriptor_mutation_probes(tmp_path: Path) -> None:
    from tests.workflow_kernel.cross_event_binding_harness import (
        run_extra_descriptor_generator_cli_sabotage,
    )

    deps = _deps()
    # 1) extra descriptor field on temp copy of real SSOT descriptor source via generator CLI --check
    repo_root = Path(__file__).resolve().parents[2]
    run_extra_descriptor_generator_cli_sabotage(repo_root, tmp_path / "desc-cli")

    # 2) broadened expected message runs a real ledger negative and harness fails equality
    bid = 'cross_event.claim_to_intent_fence::claimed:payload.fence->intent_recorded:payload.fence'
    contract = CROSS_EVENT_BINDING_CASES[bid]
    sab = HarnessSabotage(broaden_expected_message=contract.expected_message + " BROAD", trace=[])
    try:
        run_cross_event_binding_case(contract, tmp_path / "broad", deps, sabotage=sab)
        raised = False
    except AssertionError as exc:
        raised = True
        assert "boundary message mismatch" in str(exc) or "BROAD" in str(exc)
    assert raised, "harness must fail broadened expected message against real exception"
    assert "broaden_message_applied" in sab.trace

    # 3) sabotage skip-good-append runs full harness and fails durable accept/evidence before negative
    sab = HarnessSabotage(skip_good_append=True, trace=[])
    try:
        run_cross_event_binding_case(contract, tmp_path / "skip-good", deps, sabotage=sab)
        raised = False
    except AssertionError as exc:
        raised = True
        assert "mandatory evidence incomplete" in str(exc) or "good_append" in str(exc)
    assert raised
    assert "sabotage_skip_good_append" in sab.trace
    assert "harness_enter" in sab.trace

    # 4) sabotage skip-diff-guard runs full harness and fails independent mandatory evidence-completeness
    sab = HarnessSabotage(skip_diff_guard=True, trace=[])
    try:
        run_cross_event_binding_case(contract, tmp_path / "skip-diff", deps, sabotage=sab)
        raised = False
    except AssertionError as exc:
        raised = True
        assert "diff_guard" in str(exc) or "mandatory evidence incomplete" in str(exc)
    assert raised
    assert "sabotage_skip_diff_guard" in sab.trace
    assert "good_append_or_capture" in sab.trace


def test_self_contained_copy_missing_dependency_hard_fail_no_partial(tmp_path: Path) -> None:
    """Deleting one manifest source before helper hard-fails with exact marker; no partial publish."""
    import shutil
    from pathlib import Path as P

    from tests.workflow_kernel.cross_event_binding_harness import (
        GENERATOR_SELF_CONTAINED_COPY_RELS_V1,
        MISSING_SELF_CONTAINED_COPY_SOURCE_MARKER,
        copy_generator_self_contained_tree,
    )

    repo = P(__file__).resolve().parents[2]
    # Build a staging tree that looks like the repo but with one required source deleted.
    staging = tmp_path / "staging_missing_src"
    missing_rel = "workflow_kernel/workflow_effect_ledger.py"
    assert missing_rel in GENERATOR_SELF_CONTAINED_COPY_RELS_V1
    for rel in GENERATOR_SELF_CONTAINED_COPY_RELS_V1:
        src = repo / rel
        assert src.is_file(), rel
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (staging / missing_rel).unlink()
    assert not (staging / missing_rel).exists()

    iso = tmp_path / "iso_should_stay_empty"
    iso.mkdir()
    from tests.workflow_kernel.cross_event_binding_harness import snapshot_whole_tree_exclusion_free
    before_snap = snapshot_whole_tree_exclusion_free(iso)
    with pytest.raises(FileNotFoundError) as ei:
        copy_generator_self_contained_tree(staging, iso)
    msg = str(ei.value)
    assert MISSING_SELF_CONTAINED_COPY_SOURCE_MARKER in msg
    assert missing_rel in msg
    # Fail-closed: exact whole-tree snapshot equality (no partial dirs/files/symlinks).
    after_snap = snapshot_whole_tree_exclusion_free(iso)
    assert after_snap == before_snap, sorted(
        k for k in set(after_snap) | set(before_snap) if after_snap.get(k) != before_snap.get(k)
    )

    # Explicit missing-source supply path (empty fake root): same exact marker, first rel.
    iso2 = tmp_path / "iso_empty_root"
    iso2.mkdir()
    before2 = snapshot_whole_tree_exclusion_free(iso2)
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()
    with pytest.raises(FileNotFoundError) as ei2:
        copy_generator_self_contained_tree(empty_root, iso2)
    msg2 = str(ei2.value)
    assert MISSING_SELF_CONTAINED_COPY_SOURCE_MARKER in msg2
    assert GENERATOR_SELF_CONTAINED_COPY_RELS_V1[0] in msg2
    after2 = snapshot_whole_tree_exclusion_free(iso2)
    assert after2 == before2

    # Directory-only partial artifact sabotage: a pre-seeded dest dir must remain
    # exactly equal (no new files under it) and still hard-fail with exact marker.
    iso3 = tmp_path / "iso_dir_only_partial"
    iso3.mkdir()
    partial_dir = iso3 / "workflow_kernel"
    partial_dir.mkdir()
    before3 = snapshot_whole_tree_exclusion_free(iso3)
    assert any(k.endswith("workflow_kernel/") or k == "workflow_kernel/" for k in before3)
    with pytest.raises(FileNotFoundError) as ei3:
        copy_generator_self_contained_tree(staging, iso3)
    msg3 = str(ei3.value)
    assert MISSING_SELF_CONTAINED_COPY_SOURCE_MARKER in msg3
    assert missing_rel in msg3
    after3 = snapshot_whole_tree_exclusion_free(iso3)
    assert after3 == before3, sorted(
        k for k in set(after3) | set(before3) if after3.get(k) != before3.get(k)
    )


def test_self_contained_ten_module_canary_and_seeded_cache_snapshot(tmp_path: Path) -> None:
    """10 eager local modules under iso only; exclusion-free snapshot includes seeded cache canaries."""
    import os
    from pathlib import Path as P

    from tests.workflow_kernel.cross_event_binding_harness import (
        GENERATOR_SELF_CONTAINED_COPY_RELS_V1,
        GENERATOR_SELF_CONTAINED_WORKFLOW_KERNEL_MODULES_V1,
        assert_iso_only_generator_workflow_kernel_imports,
        assert_snapshot_includes_seeded_cache_canaries,
        copy_generator_self_contained_tree,
        seed_cache_named_snapshot_canaries,
        snapshot_whole_tree_exclusion_free,
    )

    assert len(GENERATOR_SELF_CONTAINED_WORKFLOW_KERNEL_MODULES_V1) == 10
    assert set(GENERATOR_SELF_CONTAINED_WORKFLOW_KERNEL_MODULES_V1) == {
        "workflow_kernel",
        "workflow_kernel.effect_contracts",
        "workflow_kernel.effect_ledger_cross_relation_descriptors",
        "workflow_kernel.effect_ledger_relation_manifests",
        "workflow_kernel.workflow_effect_ledger",
        "workflow_kernel.runtime_state",
        "tests",
        "tests.workflow_kernel",
        "tests.workflow_kernel.test_effect_contracts",
        "hermes_constants",
    }
    assert len(GENERATOR_SELF_CONTAINED_COPY_RELS_V1) == 19

    repo = P(__file__).resolve().parents[2]
    iso = tmp_path / "iso_ten"
    copy_generator_self_contained_tree(repo, iso)
    # Destination existence + hash already verified inside helper; re-check all rels present.
    for rel in GENERATOR_SELF_CONTAINED_COPY_RELS_V1:
        assert (iso / rel).is_file(), rel

    canary_keys = seed_cache_named_snapshot_canaries(iso)
    snap = snapshot_whole_tree_exclusion_free(iso)
    assert_snapshot_includes_seeded_cache_canaries(snap, canary_keys, label="ten-module snap")
    # Explicit key-set proof for cache-named entries (no filter).
    assert "__pycache__/" in snap
    assert "__pycache__/seeded_snapshot_canary.pyc" in snap
    assert ".pytest_cache/" in snap
    assert ".pytest_cache/seeded_snapshot_canary" in snap
    assert ".DS_Store" in snap

    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}}
    env["PYTHONPATH"] = str(iso)
    assert_iso_only_generator_workflow_kernel_imports(
        iso,
        cwd=tmp_path,
        env=env,
        label="ten-module canary",
    )


def test_descriptor_gate_rejects_exact_plus_alternate_synthetic() -> None:
    """Synthetic output with exact marker AND alternate must still reject unconditionally."""
    from tests.workflow_kernel.cross_event_binding_harness import (
        DESCRIPTOR_ALTERNATE_GATES_V1,
        EXACT_DESCRIPTOR_MARKER,
        assert_descriptor_adversary_output_gates,
    )

    # Exact-only succeeds.
    assert_descriptor_adversary_output_gates(
        f"ERROR: {EXACT_DESCRIPTOR_MARKER} at row 0\n",
        label="exact-only-ok",
    )
    # Exact + each alternate rejected regardless of exact presence.
    for alt in DESCRIPTOR_ALTERNATE_GATES_V1:
        synthetic = f"{EXACT_DESCRIPTOR_MARKER}\nalso saw {alt} somewhere\n"
        with pytest.raises(AssertionError) as ei:
            assert_descriptor_adversary_output_gates(synthetic, label="exact+alternate")
        assert "alternate gate present unconditionally rejected" in str(ei.value)
        assert alt in str(ei.value)
    # Missing exact fails even without alternate.
    with pytest.raises(AssertionError) as ei2:
        assert_descriptor_adversary_output_gates("some other failure\n", label="missing-exact")
    assert "missing exact single gate" in str(ei2.value)


@pytest.mark.parametrize("binding_id", EXECUTABLE_BINDING_IDS)
def test_cross_event_binding_enforced(binding_id: str, tmp_path: Path) -> None:
    contract = CROSS_EVENT_BINDING_CASES[binding_id]
    assert contract.exec_mode == "primary"
    assert binding_id in {b.binding_id for b in BINDING_BOUNDARY_EXPECTATIONS_V1}
    run_cross_event_binding_case(contract, tmp_path, _deps())


@pytest.mark.parametrize("relation_id", _cross_relation_ids())
def test_cross_event_relation_enforced(relation_id: str, tmp_path: Path) -> None:
    """Relation-level entry: descriptor/metadata checks only (no duplicate harness execution)."""
    from workflow_kernel.effect_ledger_cross_relation_descriptors import CROSS_EVENT_RELATION_DESCRIPTORS_V1
    from tests.workflow_kernel._cross_event_binding_data import (
        ALL_PATH_BINDING_CASES_V1,
        DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1,
        EXECUTABLE_BINDING_IDS,
        PARSER_CONTRACT_CASES_V1,
    )

    desc = {r["relation_id"]: r for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1}[relation_id]
    assert set(desc)  # descriptor present
    inv = [c for c in ALL_PATH_BINDING_CASES_V1.values() if c.relation_id == relation_id]
    assert inv, relation_id
    cross_inv = [c for c in inv if c.binding_id not in PARSER_CONTRACT_CASES_V1]
    for c in cross_inv:
        probe = DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1.get(c.binding_id)
        assert probe is not None, c.binding_id
        assert probe in EXECUTABLE_BINDING_IDS
        # Mapped guard consumes this path relation triple.
        from tests.workflow_kernel._cross_event_binding_data import CROSS_EVENT_BINDING_CASES
        g = CROSS_EVENT_BINDING_CASES[probe]
        assert g.expected_code == c.expected_code
        assert g.expected_message == c.expected_message
        assert g.validator_function == c.validator_function
    # Metadata representative contract remains available without re-executing harness.
    if relation_id in CROSS_EVENT_CASE_CONTRACTS:
        contract = CROSS_EVENT_CASE_CONTRACTS[relation_id]
        assert contract.relation_id == relation_id
        assert contract.expected_code
        assert contract.expected_message
    _ = tmp_path  # isolated path unused (no harness side effects)


def test_generator_adversarial_false_relation_coverage() -> None:
    import importlib.util
    import json
    from pathlib import Path as P

    gen_path = P(__file__).resolve().parents[2] / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    spec = importlib.util.spec_from_file_location("gen_ssot_4a5b2", gen_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from workflow_kernel.effect_ledger_cross_relation_descriptors import cross_event_relation_descriptors

    ssot = json.loads(
        (P(__file__).resolve().parents[2] / "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json").read_text(encoding="utf-8")
    )
    cross = cross_event_relation_descriptors()
    cov = mod.extract_cross_path_coverage(cross)
    inv = list(ssot["path_inventory"])
    mod.assert_adversarial_false_relation_id_rejected(inv, cross, cov)
    for bad_path in (
        "payload.observation",
        "payload.observation.adapter_result_digest",
        "payload.observation.classification",
        "payload.observation.effect_id",
        "payload.observation.fence",
        "payload.observation.observation_id",
        "payload.observation.observed_at",
        "payload.observation.readback",
        "payload.observation.schema_version",
        "payload.observation.__synthetic_nonexistent_child__",
    ):
        assert ("reconciled", bad_path) not in {(r["event_type"], r["path"]) for r in inv}
        assert ("reconciled", bad_path) not in cov


def test_generator_missing_bookkeeping_hard_error_write_and_check(tmp_path: Path) -> None:
    import copy
    import hashlib
    import importlib.util
    import json
    import shutil
    import subprocess
    import sys
    from pathlib import Path as P

    gen_path = P(__file__).resolve().parents[2] / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    spec = importlib.util.spec_from_file_location("gen_ssot_4a5b2_bk", gen_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    corpus_path = P(__file__).resolve().parents[2] / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    host = None
    for c in corpus["cases"]:
        if c.get("expect") == "reject" and str(c.get("id", "")).startswith("rel_") and "bookkeeping_paths" in c:
            host = c
            break
    assert host is not None

    missing_key = copy.deepcopy(corpus)
    for c in missing_key["cases"]:
        if c.get("id") == host["id"]:
            del c["bookkeeping_paths"]
            break
    errs = mod.validate_explicit_bookkeeping_paths(missing_key, mode="check")
    assert errs and any("missing explicit bookkeeping_paths" in e for e in errs)

    # Isolated copied repo/fixture: real CLI --write and --check must exit nonzero and leave bytes unchanged.
    from tests.workflow_kernel.cross_event_binding_harness import (
        assert_iso_only_generator_workflow_kernel_imports,
        assert_snapshot_includes_seeded_cache_canaries,
        copy_generator_self_contained_tree,
        seed_cache_named_snapshot_canaries,
        snapshot_whole_tree_exclusion_free,
    )

    src_root = P(__file__).resolve().parents[2]
    iso = tmp_path / "iso_repo"
    copy_generator_self_contained_tree(src_root, iso)
    # Self-contained: imports resolve only inside copied repo (no live-repo PYTHONPATH).
    iso_corpus = iso / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json"
    data = json.loads(iso_corpus.read_text(encoding="utf-8"))
    for c in data["cases"]:
        if c.get("id") == host["id"]:
            del c["bookkeeping_paths"]
            break
    iso_corpus.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    canary_keys = seed_cache_named_snapshot_canaries(iso)

    before = snapshot_whole_tree_exclusion_free(iso)
    assert_snapshot_includes_seeded_cache_canaries(before, canary_keys, label="bookkeeping before")
    env = dict(**{k: v for k, v in __import__("os").environ.items() if k not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}})
    env["PYTHONPATH"] = str(iso)  # iso-only; no live-repo fallback
    py = sys.executable
    gen = str(iso / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py")
    # Import-path canary: every generator-imported workflow_kernel module under isolated copy only.
    assert_iso_only_generator_workflow_kernel_imports(
        iso,
        cwd=tmp_path,
        env=env,
        label="bookkeeping adversary import",
    )
    # Canary: live source rename must not be required; module loads from iso via PYTHONPATH.
    for flag in ("--write", "--check"):
        # No caller -B / PYTHONDONTWRITEBYTECODE suppression.
        env_run = dict(env)
        env_run.pop("PYTHONDONTWRITEBYTECODE", None)
        proc = subprocess.run([py, gen, flag], cwd=str(tmp_path), env=env_run, capture_output=True, text=True)
        assert proc.returncode != 0, (flag, proc.stdout, proc.stderr)
        combined = (proc.stdout or "") + (proc.stderr or "")
        assert "BOOKKEEPING PRECHECK FAILED" in combined or "bookkeeping-precheck" in combined, combined
        assert "missing explicit bookkeeping_paths" in combined, combined
        # Explicitly reject manifest/baseline/import as the primary failure marker.
        assert not combined.lstrip().startswith("CANONICAL CORPUS MANIFEST FAILED"), combined
        assert "canonical-manifest raw sha mismatch" not in combined
        after = snapshot_whole_tree_exclusion_free(iso)
        assert_snapshot_includes_seeded_cache_canaries(after, canary_keys, label=f"bookkeeping after {flag}")
        assert after == before, (flag, {k for k in after if after.get(k) != before.get(k)})

    # Missing needed entry (empty bookkeeping with residual)
    data2 = json.loads(iso_corpus.read_text(encoding="utf-8"))
    # restore structure from original then empty bk
    orig = json.loads(corpus_path.read_text(encoding="utf-8"))
    iso_corpus.write_text(json.dumps(orig, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    data2 = json.loads(iso_corpus.read_text(encoding="utf-8"))
    for c in data2["cases"]:
        if c.get("id") == host["id"]:
            c["bookkeeping_paths"] = []
            break
    iso_corpus.write_text(json.dumps(data2, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before2 = snapshot_whole_tree_exclusion_free(iso)
    proc = subprocess.run([py, gen, "--write"], cwd=str(tmp_path), env=env, capture_output=True, text=True)
    assert proc.returncode != 0
    assert snapshot_whole_tree_exclusion_free(iso) == before2

    # Injected second mutation residual
    data3 = json.loads(corpus_path.read_text(encoding="utf-8"))
    for c in data3["cases"]:
        if c.get("id") == host["id"]:
            val = copy.deepcopy(c["value"])
            payload = val.get("payload")
            if isinstance(payload, dict) and "command_digest" in payload and c.get("mutated_path") != "payload.command_digest":
                payload["command_digest"] = "sha256:" + ("99" * 32)
                c["value"] = val
            break
    iso_corpus.write_text(json.dumps(data3, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before3 = snapshot_whole_tree_exclusion_free(iso)
    proc = subprocess.run([py, gen, "--check"], cwd=str(tmp_path), env=env, capture_output=True, text=True)
    assert proc.returncode != 0
    assert snapshot_whole_tree_exclusion_free(iso) == before3

    # Alternative prevalidated but wrong semantic value/reason/path (bookkeeping still valid).
    # Ordinary generator must fail --write/--check with all governed artifact bytes unchanged.
    def poison_governed_semantic(kind: str) -> None:
        data = json.loads(corpus_path.read_text(encoding="utf-8"))
        hit = False
        for c in data["cases"]:
            if c.get("expect") != "reject" or not str(c.get("id", "")).startswith("rel_"):
                continue
            if "bookkeeping_paths" not in c or not c.get("mutated_path"):
                continue
            if kind == "reason":
                # Swap to another internally known reason class that is still a valid token.
                cur = c.get("expected_reason_class") or "payload_invalid"
                c["expected_reason_class"] = "schema_invalid" if cur != "schema_invalid" else "payload_invalid"
                hit = True
                break
            if kind == "path":
                # Point mutated_path at a different declared-looking path while keeping bookkeeping.
                cur = c.get("mutated_path")
                c["mutated_path"] = "payload.effect_id" if cur != "payload.effect_id" else "payload.fence"
                hit = True
                break
            if kind == "value":
                val = copy.deepcopy(c["value"])
                # Keep parseable structure but flip a semantic field already consistent with bookkeeping.
                payload = val.get("payload")
                if not isinstance(payload, dict):
                    continue
                if "fence" in payload and isinstance(payload["fence"], int):
                    payload["fence"] = int(payload["fence"]) + 7
                    # Keep receipt/obs fence bookkeeping coherent enough to stay "prevalidated-looking"
                    if isinstance(payload.get("receipt"), dict) and "fence" in payload["receipt"]:
                        payload["receipt"] = dict(payload["receipt"])
                        payload["receipt"]["fence"] = payload["fence"]
                    c["value"] = val
                    hit = True
                    break
        assert hit, kind
        iso_corpus.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for kind in ("reason", "path", "value"):
        poison_governed_semantic(kind)
        before_k = snapshot_whole_tree_exclusion_free(iso)
        for flag in ("--write", "--check"):
            proc = subprocess.run([py, gen, flag], cwd=str(tmp_path), env=env, capture_output=True, text=True)
            assert proc.returncode != 0, (kind, flag, proc.stdout, proc.stderr)
            assert snapshot_whole_tree_exclusion_free(iso) == before_k, (kind, flag)


def test_binding_expectations_immutable_and_reference_reducer() -> None:
    from tests.workflow_kernel.cross_event_binding_harness import (
        assert_binding_expectations_exact_cover,
        assert_binding_expectations_immutable_and_exact,
    )
    from tests.workflow_kernel.reference_project_events_v1 import assert_reference_known_vectors

    assert_reference_known_vectors()
    assert_binding_expectations_immutable_and_exact()
    assert_binding_expectations_exact_cover()
    # Import-time table is a tuple literal; identity stable across reloads of attribute.
    assert isinstance(BINDING_BOUNDARY_EXPECTATIONS_V1, tuple)
    assert list(EXECUTABLE_BINDING_IDS) == sorted(b.binding_id for b in BINDING_BOUNDARY_EXPECTATIONS_V1)
    assert list(EXECUTABLE_BINDING_IDS) == sorted(CROSS_EVENT_BINDING_CASES)


def test_executable_boundary_mapping_each_descriptor_row() -> None:
    ssot = _ssot_cross_rows()
    from workflow_kernel.effect_ledger_cross_relation_descriptors import CROSS_EVENT_RELATION_DESCRIPTORS_V1

    prod = {r["relation_id"]: r for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1}
    for rid, row in ssot.items():
        contract = CROSS_EVENT_CASE_CONTRACTS[rid]
        exp = INDEPENDENT_DESCRIPTOR_EXPECTATIONS_V1[rid]
        boundary = str(row["validator_boundary"])
        assert boundary == exp["validator_boundary"]
        # Descriptor boundary matches independent map exactly (no prefix/template).
        assert boundary == exp["validator_boundary"]
        assert boundary == f"{contract.expected_code}:{contract.expected_message}"
        assert "{" not in boundary
        assert contract.validator_function == row["validator_function"] == exp["validator_function"]
        assert prod[rid]["validator_boundary"] == boundary
        assert prod[rid]["validator_function"] == contract.validator_function
        assert list(prod[rid]["source_paths"]) == list(row["source_paths"]) == list(exp["source_paths"])
        assert list(prod[rid]["destination_paths"]) == list(row["destination_paths"]) == list(exp["destination_paths"])
        assert row["focused_test_id"] == f"test_cross_event_relation_enforced[{rid}]"
    # Every binding expectation message is exact full literal (no templates)
    for b in BINDING_BOUNDARY_EXPECTATIONS_V1:
        assert "{" not in b.expected_message
        assert b.expected_code
        assert b.expected_message


def test_canonical_corpus_manifest_matches_live_and_not_ordinary_regenerated(tmp_path: Path) -> None:
    """Independent positive checks for immutable canonical corpus manifest.

    Recomputes raw/canonical/order/per-case/count/provenance WITHOUT importing the
    generator validator. Also pins manifest raw SHA via independent module + SSOT.
    """
    import hashlib
    import json
    import subprocess
    import sys
    from pathlib import Path as P

    from tests.workflow_kernel.canonical_corpus_manifest_pin_v1 import (
        BASELINE_MANIFEST_RAW_SHA256_V1,
        CANONICAL_CORPUS_MANIFEST_RAW_SHA256_V1,
        CANONICAL_CORPUS_MANIFEST_RELPATH_V1,
        BASELINE_MANIFEST_RELPATH_V1,
    )
    repo = P(__file__).resolve().parents[2]
    corpus_path = repo / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json"
    manifest_path = repo / CANONICAL_CORPUS_MANIFEST_RELPATH_V1
    gen_path = repo / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    assert corpus_path.is_file() and manifest_path.is_file() and gen_path.is_file()

    manifest_raw = manifest_path.read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == CANONICAL_CORPUS_MANIFEST_RAW_SHA256_V1
    # Second authority: SSOT text must carry the same full pin.
    ssot = (repo / "docs/private/hgwk-f4-real-adapters-ssot.md").read_text(encoding="utf-8")
    assert CANONICAL_CORPUS_MANIFEST_RAW_SHA256_V1 in ssot

    raw = corpus_path.read_bytes()
    corpus = json.loads(raw.decode("utf-8"))
    manifest = json.loads(manifest_raw.decode("utf-8"))
    assert manifest.get("schema_version") == "effect_contract_shared_corpus_canonical_manifest_v1"

    # Independent recompute of corpus raw/canonical/order/per-case/count.
    assert hashlib.sha256(raw).hexdigest() == manifest["corpus_raw_sha256"]
    cases = list(corpus.get("cases") or [])
    ordered_ids = [str(c.get("id")) for c in cases]
    assert ordered_ids == list(manifest.get("ordered_case_ids") or [])
    order_digest = hashlib.sha256(("\n".join(ordered_ids) + "\n").encode()).hexdigest()
    assert order_digest == manifest.get("ordered_case_id_list_digest_sha256")
    # per-case digests (independent of generator module)
    per = manifest.get("per_case_canonical_digest_sha256") or {}
    for c in cases:
        cid = str(c.get("id"))
        dig = hashlib.sha256(
            json.dumps(c, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        assert per.get(cid) == dig, cid
    # counts
    led = [c for c in cases if c.get("kind") == "workflow_effect_ledger_event_v1"]
    counts = manifest.get("expected_counts") or {}
    assert counts.get("total_cases") == len(cases) == 206
    assert counts.get("ledger_cases") == len(led) == 175
    assert counts.get("total_accept") == sum(1 for c in cases if c.get("expect") == "accept") == 95
    assert counts.get("total_reject") == sum(1 for c in cases if c.get("expect") == "reject") == 111
    assert counts.get("ledger_accept") == sum(1 for c in led if c.get("expect") == "accept") == 84
    assert counts.get("ledger_reject") == sum(1 for c in led if c.get("expect") == "reject") == 91
    # corpus canonical sha (independent): indent-2 sort_keys JSON + newline
    can = hashlib.sha256(
        (json.dumps(corpus, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    ).hexdigest()
    assert can == manifest.get("corpus_canonical_sha256")

    prov = manifest.get("provenance") or {}
    baseline = repo / BASELINE_MANIFEST_RELPATH_V1
    assert baseline.is_file()
    b_sha = hashlib.sha256(baseline.read_bytes()).hexdigest()
    assert b_sha == BASELINE_MANIFEST_RAW_SHA256_V1 == prov.get("pre_4a5b_baseline_manifest_sha256")
    baseline_doc = json.loads(baseline.read_text(encoding="utf-8"))
    b_ids = [str(i) for i in baseline_doc.get("case_ids") or []]
    live_ids = set(ordered_ids)
    assert all(i in live_ids for i in b_ids)
    assert int(baseline_doc.get("case_count") or 0) == len(b_ids) == 44

    # Ordinary modes zero-write on a self-contained copied repo: snapshot ENTIRE copy.
    import os as _os
    import shutil as _shutil
    from tests.workflow_kernel.cross_event_binding_harness import (
        assert_iso_only_generator_workflow_kernel_imports,
        assert_snapshot_includes_seeded_cache_canaries,
        copy_generator_self_contained_tree,
        seed_cache_named_snapshot_canaries,
        snapshot_whole_tree_exclusion_free,
    )

    iso_zw = tmp_path / "zero_write_iso"
    copy_generator_self_contained_tree(repo, iso_zw)
    # TS check-only companion under copied repo: contracts package + package.json + node_modules symlink.
    pkg = repo / "package.json"
    if pkg.exists():
        _shutil.copy2(pkg, iso_zw / "package.json")
    contracts_src = repo / "apps" / "workflow-contracts"
    if contracts_src.is_dir():
        _shutil.copytree(contracts_src, iso_zw / "apps" / "workflow-contracts", dirs_exist_ok=True)
    nm = repo / "node_modules"
    if nm.exists() and not (iso_zw / "node_modules").exists():
        (iso_zw / "node_modules").symlink_to(nm, target_is_directory=True)
    canary_keys = seed_cache_named_snapshot_canaries(iso_zw)
    before_fs = snapshot_whole_tree_exclusion_free(iso_zw)
    assert_snapshot_includes_seeded_cache_canaries(before_fs, canary_keys, label="zero-write before")
    env = {k: v for k, v in __import__("os").environ.items() if k not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}}
    env["PYTHONPATH"] = str(iso_zw)
    gen_iso = iso_zw / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    # Import-path canary under zero-write copy: every workflow_kernel module under iso only.
    assert_iso_only_generator_workflow_kernel_imports(
        iso_zw,
        cwd=tmp_path,
        env=env,
        label="zero-write import",
    )
    for args in ([], ["--check"], ["--write"]):
        proc = subprocess.run(
            [sys.executable, str(gen_iso), *args],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)
        after = snapshot_whole_tree_exclusion_free(iso_zw)
        assert_snapshot_includes_seeded_cache_canaries(after, canary_keys, label=f"zero-write after {args}")
        assert after == before_fs, sorted(k for k in set(after)|set(before_fs) if after.get(k)!=before_fs.get(k))[:20]
    proc = subprocess.run(
        [sys.executable, str(gen_iso), "--check", "--not-a-real-flag-xyz"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    after_fail = snapshot_whole_tree_exclusion_free(iso_zw)
    assert_snapshot_includes_seeded_cache_canaries(after_fail, canary_keys, label="zero-write failure")
    assert after_fail == before_fs
    combined_fail = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0, (proc.returncode, combined_fail)
    assert "unrecognized arguments: --not-a-real-flag-xyz" in combined_fail, combined_fail


def test_canonical_corpus_manifest_adversary_row_swap_and_baseline_reject(tmp_path: Path) -> None:
    """Copied-repo subprocess adversaries for --check and --write: exact canonical-manifest/provenance error + filesystem-wide no change."""
    import hashlib
    import json
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path as P

    from tests.workflow_kernel.cross_event_binding_harness import (
        assert_iso_only_generator_workflow_kernel_imports,
        assert_snapshot_includes_seeded_cache_canaries,
        copy_generator_self_contained_tree,
        seed_cache_named_snapshot_canaries,
        snapshot_whole_tree_exclusion_free,
    )
    from workflow_kernel.effect_contracts import derive_workflow_effect_ledger_event_id_v1

    repo = P(__file__).resolve().parents[2]
    iso = tmp_path / "adv_iso"
    copy_generator_self_contained_tree(repo, iso)
    canary_keys = seed_cache_named_snapshot_canaries(iso)

    def snap() -> dict[str, tuple]:
        return snapshot_whole_tree_exclusion_free(iso)

    def run_both_expect_gate(before: dict, *, exact_markers: tuple[str, ...]) -> None:
        """Require exact expected gate markers; generic alternatives forbidden."""
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        env["PYTHONPATH"] = str(iso)  # iso-only; no live-repo fallback
        gen = str(iso / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py")
        # Import-path canary once per gate run family.
        assert_iso_only_generator_workflow_kernel_imports(
            iso,
            cwd=tmp_path,
            env=env,
            label="manifest adversary import",
        )
        assert_snapshot_includes_seeded_cache_canaries(before, canary_keys, label="manifest before")
        for flag in ("--check", "--write"):
            proc = subprocess.run(
                [sys.executable, gen, flag],  # no -B
                cwd=str(tmp_path),
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc.returncode != 0, (flag, proc.stdout, proc.stderr)
            combined = (proc.stdout or "") + (proc.stderr or "")
            for marker in exact_markers:
                assert marker in combined, (flag, marker, combined)
            after = snap()
            assert_snapshot_includes_seeded_cache_canaries(after, canary_keys, label=f"manifest after {flag}")
            assert after == before

    corpus_path = iso / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json"
    manifest_path = iso / "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-canonical-4a5b11.manifest.json"

    # 1) Row swap
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    idxs = [i for i, c in enumerate(data["cases"]) if str(c.get("id", "")).startswith("rel_")]
    assert len(idxs) >= 2
    data["cases"][idxs[0]], data["cases"][idxs[1]] = data["cases"][idxs[1]], data["cases"][idxs[0]]
    corpus_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    before = snap()
    run_both_expect_gate(before, exact_markers=("canonical-manifest ordered case id list mismatch",))

    # 2) Restore corpus; mutate baseline manifest archive (exact baseline gate).
    shutil.copy2(repo / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json", corpus_path)
    baseline_path = iso / "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json"
    bdoc = json.loads(baseline_path.read_text(encoding="utf-8"))
    # Coherent-looking baseline drift: flip one retained ID while keeping shape.
    ids = list(bdoc.get("case_ids") or [])
    assert ids
    ids[0] = str(ids[0]) + "__BASELINE_DRIFT"
    bdoc["case_ids"] = ids
    bdoc["case_count"] = len(ids)
    baseline_path.write_text(json.dumps(bdoc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    before = snap()
    run_both_expect_gate(before, exact_markers=("canonical-manifest baseline archive raw sha mismatch",))

    # 3) Coherent timestamp + recomputed event IDs on a non-reject-twin accept case.
    shutil.copy2(repo / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json", corpus_path)
    shutil.copy2(
        repo / "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json",
        iso / "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json",
    )
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    twin_hit = False
    for c in data["cases"]:
        cid = str(c.get("id", ""))
        if c.get("expect") != "accept" or not isinstance(c.get("value"), dict):
            continue
        # Prefer non-rel cases so reject-twin bookkeeping residual cannot mask the manifest gate.
        if cid.startswith("rel_"):
            continue
        val = c["value"]
        if "occurred_at" not in val:
            continue
        val["occurred_at"] = "2099-01-01T00:00:00Z"
        body = {k: v for k, v in val.items() if k != "event_id"}
        try:
            val["event_id"] = derive_workflow_effect_ledger_event_id_v1(body)
        except Exception:
            val["event_id"] = "wel_" + ("ab" * 32)
        twin_hit = True
        break
    assert twin_hit
    corpus_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    before = snap()
    run_both_expect_gate(before, exact_markers=("canonical-manifest", "per-case digest"))

    # 4) Coherent live corpus + manifest mutation (both updated together) still fails pinned authorities
    shutil.copy2(repo / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json", corpus_path)
    shutil.copy2(
        repo / "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-canonical-4a5b11.manifest.json",
        manifest_path,
    )
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    data["cases"][0]["id"] = str(data["cases"][0]["id"]) + "__COHERENT"
    # recompute a coherent-looking manifest for the mutated corpus
    raw = json.dumps(data, indent=2, ensure_ascii=False).encode() + b"\n"
    corpus_path.write_bytes(raw)
    man = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = [str(c.get("id")) for c in data["cases"]]
    man["ordered_case_ids"] = ids
    man["corpus_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    man["corpus_canonical_sha256"] = hashlib.sha256(
        (json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    ).hexdigest()
    man["ordered_case_id_list_digest_sha256"] = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
    per = {}
    for c in data["cases"]:
        per[str(c["id"])] = hashlib.sha256(
            json.dumps(c, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    man["per_case_canonical_digest_sha256"] = per
    man["expected_counts"] = {
        "total_cases": len(data["cases"]),
        "total_accept": sum(1 for c in data["cases"] if c.get("expect") == "accept"),
        "total_reject": sum(1 for c in data["cases"] if c.get("expect") == "reject"),
        "ledger_cases": sum(1 for c in data["cases"] if c.get("kind") == "workflow_effect_ledger_event_v1"),
        "ledger_accept": sum(1 for c in data["cases"] if c.get("kind") == "workflow_effect_ledger_event_v1" and c.get("expect") == "accept"),
        "ledger_reject": sum(1 for c in data["cases"] if c.get("kind") == "workflow_effect_ledger_event_v1" and c.get("expect") == "reject"),
    }
    manifest_path.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    before = snap()
    # Pin mismatch on manifest file raw SHA must fire with full pinned SHA.
    run_both_expect_gate(
        before,
        exact_markers=(
            "canonical-manifest raw sha mismatch",
            "c0f935c09ae6bbf16ec8eb066bdad1a5b1426cc8be6a6be5da8eed6f658e40fd",
        ),
    )


def test_parser_contract_cases_native_first_owners() -> None:
    """Parser-owned rows: direct parser invocation first; exact owner/code/message before any public remap."""
    import copy
    import tempfile
    from pathlib import Path as P
    from tests.workflow_kernel.guard_authority_v1 import PARSER_GUARD_ROWS_V1, PARSER_BINDING_EXPECTATIONS_V1, PARSER_CASE_IDS_V1
    from workflow_kernel import effect_contracts
    from workflow_kernel.effect_contracts import EffectContractError

    assert PARSER_CONTRACT_CASES_V1
    assert PARSER_CONTRACT_EXPECTATIONS_V1
    assert len(PARSER_CONTRACT_EXPECTATIONS_V1) == len(PARSER_GUARD_ROWS_V1) == len(PARSER_CASE_IDS_V1)
    assert len(PARSER_GUARD_ROWS_V1) == 34
    assert set(PARSER_CONTRACT_CASES_V1).isdisjoint(set(EXECUTABLE_BINDING_IDS))
    assert [e.binding_id for e in PARSER_CONTRACT_EXPECTATIONS_V1] == [
        r.binding_id for r in PARSER_BINDING_EXPECTATIONS_V1
    ]
    from tests.workflow_kernel.reference_project_events_v1 import (
        REFERENCE_KNOWN_VECTOR_SEQUENCES_V1 as _SEQS,
    )
    good_obs = copy.deepcopy(_SEQS["observed"][2]["payload"]["observation"])
    good_rcpt = copy.deepcopy(_SEQS["receipt_terminal_applied"][3]["payload"]["receipt"])

    def _invoke_parser_first(row, e) -> None:
        fn_name = e.validator_function.split(".")[-1]
        # Direct exact declared owner (body helpers or parsers/validators).
        owner_fn = getattr(effect_contracts, fn_name, None)
        msg = e.expected_message
        if msg == "schema_version must be effect_observation_v1":
            candidate = copy.deepcopy(good_obs)
            candidate["schema_version"] = "WRONG_OBS"
            # Body owner accepts mapping candidate shape directly.
            raised = _call_expect(owner_fn, candidate)
        elif msg == "schema_version must be effect_receipt_v1":
            candidate = copy.deepcopy(good_rcpt)
            candidate["schema_version"] = "WRONG_RCPT"
            raised = _call_expect(owner_fn, candidate)
        elif msg == "receipt_digest does not hash the canonical receipt body":
            candidate = copy.deepcopy(good_rcpt)
            candidate["receipt_digest"] = "sha256:" + ("00" * 32)
            raised = _call_expect(owner_fn, candidate)
        else:
            # Full-event parser-first path for payload-local coherence rows.
            raised = _parser_first_from_case_mutation(e.binding_id, msg)
        assert raised is not None, e.binding_id
        code = getattr(raised, "code", None)
        assert code == e.expected_code == "effect_contract_invalid", (e.binding_id, code, raised)
        assert str(raised) == e.expected_message == row.message, (e.binding_id, str(raised), e.expected_message)
        # Public append remap must not be treated as the owner (parser fires first).
        assert not e.validator_function.startswith("workflow_effect_ledger.")
        # Exact declared owner name must be the callable we resolved (when direct).
        if owner_fn is not None and msg in {
            "schema_version must be effect_observation_v1",
            "schema_version must be effect_receipt_v1",
            "receipt_digest does not hash the canonical receipt body",
        }:
            assert owner_fn.__name__ == fn_name == row.native_function.split(".")[-1]

    def _call_expect(fn, candidate):
        assert fn is not None
        try:
            fn(candidate)
        except Exception as exc:  # noqa: BLE001
            return exc
        return None

    def _parser_first_from_case_mutation(binding_id: str, expected_message: str):
        contract = PARSER_CONTRACT_CASES_V1[binding_id]
        with tempfile.TemporaryDirectory() as td:
            root = P(td)
            meta = _build_accept_chain(root, contract.accept_builder)
            et, good_payload, key, at = _good_destination_payload(contract.relation_id, root, meta)
            good_event = _as_plain(_materialize_good_event(root, et, good_payload, key, at))
            bad = _mutate_accepted_good_event(contract, good_event, meta)
            try:
                effect_contracts.parse_workflow_effect_ledger_event_v1(bad)
            except Exception as exc:  # noqa: BLE001
                return exc
            return None

    for e, row in zip(PARSER_CONTRACT_EXPECTATIONS_V1, PARSER_GUARD_ROWS_V1, strict=True):
        assert e.binding_id == row.case_id
        assert e.validator_function.startswith("effect_contracts.")
        assert e.validator_function == row.native_function == row.validator_function
        assert e.expected_code == "effect_contract_invalid" == row.code
        assert e.expected_message == row.message
        assert "{" not in e.expected_message
        assert row.source_line >= 1
        _invoke_parser_first(row, e)



def test_parser_cases_expectations_guard_rows_exact_field_equality() -> None:
    """Exhaustive exact equality across all 34 parser cases / expectations / guard rows."""
    from tests.workflow_kernel.guard_authority_v1 import (
        PARSER_GUARD_ROWS_V1,
        PARSER_BINDING_EXPECTATIONS_V1,
        PARSER_CASE_IDS_V1,
    )

    assert len(PARSER_CONTRACT_CASES_V1) == len(PARSER_CONTRACT_EXPECTATIONS_V1) == len(PARSER_GUARD_ROWS_V1) == 34
    assert list(PARSER_CASE_IDS_V1) == [r.case_id for r in PARSER_GUARD_ROWS_V1]
    assert sorted(PARSER_CONTRACT_CASES_V1) == sorted(PARSER_CASE_IDS_V1)
    for case_id, exp, row in zip(
        PARSER_CASE_IDS_V1,
        PARSER_CONTRACT_EXPECTATIONS_V1,
        PARSER_GUARD_ROWS_V1,
        strict=True,
    ):
        case = PARSER_CONTRACT_CASES_V1[case_id]
        assert case.binding_id == exp.binding_id == row.case_id == case_id
        assert case.relation_id == exp.relation_id == row.relation_id
        assert case.source_event == exp.source_event == row.source_event
        assert case.source_path == exp.source_path == row.source_path
        assert case.destination_event == exp.destination_event == row.destination_event_name
        assert case.destination_path == exp.destination_path == row.destination_path
        assert (
            case.validator_function
            == exp.validator_function
            == row.validator_function
            == row.native_function
        )
        assert case.expected_code == exp.expected_code == row.code == "effect_contract_invalid"
        assert case.expected_message == exp.expected_message == row.message
        assert case.exec_mode == "parser_metadata"
        # Stale all-path ledger/schema ownership must not remain.
        assert not case.validator_function.startswith("workflow_effect_ledger.")
        assert case.expected_code not in {"schema_invalid", "payload_invalid"}


def test_authority_ast_span_owner_validation_and_sabotage() -> None:
    """AST span validation for every native+parser row; wrong-owner/line sabotage fails."""
    import ast
    from pathlib import Path as P
    from tests.workflow_kernel.guard_authority_v1 import NATIVE_GUARD_ROWS_V1, PARSER_GUARD_ROWS_V1
    from tests.workflow_kernel import _cross_event_binding_data as data_mod

    repo = P(__file__).resolve().parents[2]

    def spans(path: P) -> dict[str, tuple[int, int]]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        out = {}
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[n.name] = (n.lineno, n.end_lineno)
        return out

    for row in (*NATIVE_GUARD_ROWS_V1, *PARSER_GUARD_ROWS_V1):
        src = repo / row.source_file
        sp = spans(src)
        fn = row.native_function.rsplit(".", 1)[-1]
        assert fn in sp, (row.guard_id, fn)
        lo, hi = sp[fn]
        assert lo <= row.source_line <= hi, (row.guard_id, row.source_line, lo, hi)

    # Sabotage: wrong owner name for a known body-line row must fail closed.
    body_row = next(r for r in PARSER_GUARD_ROWS_V1 if r.source_line == 670)
    class _Fake:
        guard_id = body_row.guard_id + "::sabotage"
        native_function = "effect_contracts.parse_effect_observation_v1"  # wrong owner for line 670
        source_file = body_row.source_file
        source_line = body_row.source_line
        guard_marker = body_row.guard_marker
        code = body_row.code
        message = body_row.message
        validator_function = "effect_contracts.parse_effect_observation_v1"

    with pytest.raises(RuntimeError) as ei:
        data_mod._authority_source_ok(_Fake())  # type: ignore[attr-defined]
    assert "outside named owner AST span" in str(ei.value) or "source_line outside" in str(ei.value)

    # Sabotage: wrong source_line outside true owner span fails.
    good = next(r for r in PARSER_GUARD_ROWS_V1 if r.native_function.endswith("effect_observation_body_v1"))
    class _FakeLine:
        guard_id = good.guard_id + "::line_sabotage"
        native_function = good.native_function
        source_file = good.source_file
        source_line = 1  # module top, outside function
        guard_marker = f"{good.source_file}:1:{good.code}"
        code = good.code
        message = good.message
        validator_function = good.validator_function

    with pytest.raises(RuntimeError) as ei2:
        data_mod._authority_source_ok(_FakeLine())  # type: ignore[attr-defined]
    assert "outside named owner AST span" in str(ei2.value) or "message not at source_line" in str(ei2.value)


def test_loaded_module_canary_detects_extra_and_live_fallback(tmp_path: Path) -> None:
    """Sabotage: extra governed local module or live-fallback path fails exhaustive canary.

    Live-fallback MUST exercise the real helper's LIVE_FALLBACK enumeration + parent
    rejection branch via live_fallback_sabotage injection — no duplicated detector.
    """
    import inspect
    import os
    from pathlib import Path as P
    from tests.workflow_kernel import cross_event_binding_harness as harness
    from tests.workflow_kernel.cross_event_binding_harness import (
        assert_iso_only_generator_workflow_kernel_imports,
        copy_generator_self_contained_tree,
    )

    repo = P(__file__).resolve().parents[2]
    iso = tmp_path / "iso_extra"
    copy_generator_self_contained_tree(repo, iso)
    # Inject an extra governed local module under iso and force it to load via package init.
    extra = iso / "workflow_kernel" / "extra_canary_module_v1.py"
    extra.write_text("VALUE = 1\n", encoding="utf-8")
    init_py = iso / "workflow_kernel" / "__init__.py"
    init_py.write_text(
        init_py.read_text(encoding="utf-8")
        + "\nfrom workflow_kernel import extra_canary_module_v1  # canary sabotage\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}}
    env["PYTHONPATH"] = str(iso)
    with pytest.raises(AssertionError) as ei:
        assert_iso_only_generator_workflow_kernel_imports(iso, cwd=tmp_path, env=env, label="extra-mod")
    assert "actual loaded governed set != literal ten" in str(ei.value) or "extra=" in str(ei.value)

    # Live-fallback sabotage: plant governed module outside iso via REAL helper injection.
    iso2 = tmp_path / "iso_live"
    copy_generator_self_contained_tree(repo, iso2)
    live = tmp_path / "live_outside"
    live.mkdir()
    (live / "workflow_kernel").mkdir()
    planted_name = "workflow_kernel.planted_live_fallback_v1"
    planted_path = live / "workflow_kernel" / "planted_live_fallback_v1.py"
    planted_path.write_text("Y = 1\n", encoding="utf-8")
    env2 = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}}
    env2["PYTHONPATH"] = str(iso2)
    live_marker = "live fallback governed module(s) outside iso"
    with pytest.raises(AssertionError) as ei_live:
        assert_iso_only_generator_workflow_kernel_imports(
            iso2,
            cwd=tmp_path,
            env=env2,
            label="live-fallback",
            live_fallback_sabotage=(planted_name, str(planted_path.resolve())),
        )
    live_msg = str(ei_live.value)
    assert live_marker in live_msg, live_msg
    assert "live-fallback" in live_msg, live_msg
    assert planted_name in live_msg, live_msg
    planted_res = str(planted_path.resolve())
    assert planted_res in live_msg, live_msg

    # Source assertion: test body has no duplicated sys.modules/live-fallback detector.
    # Read file bytes and inspect only the executable sabotage region (before this marker).
    _here = P(__file__).resolve()
    _raw = _here.read_text(encoding="utf-8")
    _start = _raw.index("def test_loaded_module_canary_detects_extra_and_live_fallback")
    _marker = "# Source assertion: test body has no duplicated"
    _region = _raw[_start : _raw.index(_marker, _start)]
    assert "live_fallback_sabotage=" in _region
    assert "assert_iso_only_generator_workflow_kernel_imports" in _region
    # Forbidden secondary-detector artifacts (split so this assertion text cannot self-match).
    _forbidden = (
        "LIVE_FALLBACK" + "_DETECTED",
        "live_fallback_canary" + "_probe",
        "for name, mm in list(sys.modules.items())",
        "for name, m in list(sys.modules.items())",
        "raise SystemExit(",
    )
    for _needle in _forbidden:
        assert _needle not in _region, _needle

    # Mutation-style: if real helper live-fallback rejection branch is disabled,
    # sabotage must not be caught by any secondary detector (helper would pass).
    helper_src = inspect.getsource(assert_iso_only_generator_workflow_kernel_imports)
    assert "if live_pairs:" in helper_src
    assert live_marker in helper_src
    assert "live_fallback_sabotage" in helper_src
    assert "PLANTED_LIVE_FALLBACK" in helper_src

    real_helper = harness.assert_iso_only_generator_workflow_kernel_imports

    def _disabled_live_rejection(*args, **kwargs):
        try:
            return real_helper(*args, **kwargs)
        except AssertionError as exc:
            if live_marker in str(exc):
                # Simulate disabled parent rejection branch: swallow only that raise.
                return None
            raise

    harness.assert_iso_only_generator_workflow_kernel_imports = _disabled_live_rejection  # type: ignore[assignment]
    try:
        # Call through the same public name the test uses; with rejection disabled,
        # live sabotage must NOT fail independently (no secondary detector).
        out = harness.assert_iso_only_generator_workflow_kernel_imports(
            iso2,
            cwd=tmp_path,
            env=env2,
            label="live-fallback-mutation",
            live_fallback_sabotage=(planted_name, str(planted_path.resolve())),
        )
        assert out is None
    finally:
        harness.assert_iso_only_generator_workflow_kernel_imports = real_helper  # type: ignore[assignment]


def test_native_rows_parser_coherent_before_public_remap(tmp_path: Path) -> None:
    """Independent: every native executable candidate passes parser first, then hits exact ledger guard."""
    import tempfile
    from pathlib import Path as P
    from tests.workflow_kernel.guard_authority_v1 import NATIVE_GUARD_ROWS_V1
    from workflow_kernel import effect_contracts
    from workflow_kernel import workflow_effect_ledger as _led

    row_by = {r.case_id: r for r in NATIVE_GUARD_ROWS_V1}
    for case_id in EXECUTABLE_BINDING_IDS:
        contract = CROSS_EVENT_BINDING_CASES[case_id]
        row = row_by[case_id]
        with tempfile.TemporaryDirectory() as td:
            root = P(td)
            meta = _build_accept_chain(root, contract.accept_builder)
            if contract.negative_mode == "prior_terminal_then_destination":
                # Exercise via the full binding runner (parser-coherent + exact ledger gate).
                deps = _deps()
                from tests.workflow_kernel.cross_event_binding_harness import run_cross_event_binding_case

                run_cross_event_binding_case(contract, P(td) / "pt", deps)
                continue
            if contract.relation_id == "cross_event.state_event_type_transition":
                deps = _deps()
                from tests.workflow_kernel.cross_event_binding_harness import run_cross_event_binding_case

                run_cross_event_binding_case(contract, P(td) / "st", deps)
                continue
            et, good_payload, key, at = _good_destination_payload(contract.relation_id, root, meta)
            if contract.relation_id == "cross_event.envelope_idempotency_replay_identity":
                good_event = _as_plain(_materialize_good_event(root, et, good_payload, key, at))
            else:
                dest_et = contract.destination_event_type
                good_event = _as_plain(
                    _materialize_good_event(
                        root,
                        dest_et if contract.relation_id != "cross_event.state_event_type_transition" else et,
                        good_payload,
                        key,
                        at,
                    )
                )
            if (
                contract.negative_mode == "raw_replay_copy"
                and contract.relation_id == "cross_event.effect_id_chain_mixed_effect"
            ):
                deps = _deps()
                from tests.workflow_kernel.cross_event_binding_harness import run_cross_event_binding_case

                run_cross_event_binding_case(contract, P(td) / "mx", deps)
                continue
            bad = _mutate_accepted_good_event(contract, good_event, meta)
            # Parser must pass (native rows are parser-coherent).
            parsed = effect_contracts.parse_workflow_effect_ledger_event_v1(bad)
            assert parsed.event_id == bad["event_id"]
            # Public append must then fail with exact ledger code/message (not effect_contract_invalid).
            neg = P(td) / "neg"
            meta_n = _build_accept_chain(neg, contract.accept_builder)
            try:
                if contract.negative_mode == "raw_replay_copy":
                    from workflow_kernel.runtime_state import write_private_file_atomic

                    path = _led.effect_ledger_path_v1(private_root=neg, effect_id="effect-1")
                    events = list(_led.read_effect_events_v1(private_root=neg, effect_id="effect-1"))
                    raw = b"".join(
                        effect_contracts.canonical_json_v1(e).encode() + b"\n" for e in [*events, bad]
                    )
                    write_private_file_atomic(path, raw, private_root=neg)
                    _led.read_effect_events_v1(private_root=neg, effect_id="effect-1")
                    raise AssertionError(f"expected ledger failure for {case_id}")
                else:
                    _append(
                        neg,
                        "effect-1",
                        str(bad["event_type"]),
                        dict(bad["payload"]),
                        str(bad["idempotency_key"]),
                        str(bad["occurred_at"]),
                    )
                    raise AssertionError(f"expected ledger failure for {case_id}")
            except _led.WorkflowEffectLedgerError as exc:
                assert exc.code == row.code == contract.expected_code, (case_id, exc.code, row.code)
                assert str(exc) == row.message == contract.expected_message, (case_id, str(exc), row.message)
                assert exc.code != "effect_contract_invalid"


def test_executable_binding_deinflation_audit() -> None:
    from tests.workflow_kernel.guard_authority_v1 import (
        DESCRIPTOR_PATH_TO_GUARD_ID_V1,
        EXECUTABLE_CASE_IDS_V1,
        NATIVE_GUARD_ROWS_V1,
        PARSER_GUARD_ROWS_V1,
    )
    import ast
    from pathlib import Path as P

    assert_zero_duplicate_execution_groups()
    assert len(EXECUTABLE_BINDING_IDS) == len(set(EXECUTABLE_BINDING_IDS)) == len(NATIVE_GUARD_ROWS_V1) == 69
    assert list(EXECUTABLE_BINDING_IDS) == list(EXECUTABLE_CASE_IDS_V1)
    assert len(EXECUTABLE_BINDING_IDS) < len(ALL_PATH_BINDING_CASES_V1)
    assert len(DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1) == len(DESCRIPTOR_PATH_TO_GUARD_ID_V1) == 156
    assert len(PARSER_GUARD_ROWS_V1) == 34
    assert len(DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1) == len(
        [c for c in ALL_PATH_BINDING_CASES_V1.values() if c.binding_id not in PARSER_CONTRACT_CASES_V1]
    )
    assert len(DESCRIPTOR_PATH_TO_GUARD_ID_V1) + len(PARSER_CONTRACT_CASES_V1) == len(ALL_PATH_BINDING_CASES_V1) == 190
    for path_id, probe_id in DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1.items():
        assert probe_id in EXECUTABLE_BINDING_IDS
    # Authority module is AST-literal-only (no comprehensions/loops/generation).
    src = (P(__file__).resolve().parent / "guard_authority_v1.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = [
        type(n).__name__
        for n in ast.walk(tree)
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.For, ast.While))
    ]
    assert bad == []


def test_prior_13_misowned_native_rows_disposition() -> None:
    """Enumerate the 13 4A.5B.13 misowned native rows and assert current parser disposition.

    Review 4A.5B.13 found 13 of 82 native authority rows were parser-first. They are now
    literal parser contracts (not native executable). Manual counts: native 69, parser 34,
    path map 156, all-path 190 (=156+34).
    """
    from tests.workflow_kernel.guard_authority_v1 import (
        DESCRIPTOR_PATH_TO_GUARD_ID_V1,
        EXECUTABLE_CASE_IDS_V1,
        NATIVE_GUARD_ROWS_V1,
        PARSER_GUARD_ROWS_V1,
        PARSER_CASE_IDS_V1,
    )

    # Exact 13 former executable native representatives (one per distinct parser-first guard),
    # chosen as first table occurrence per (native_function, message) among post-orig10 parser rows.
    PRIOR_MISOWNED_NATIVE_CASE_IDS_V1 = (
        "cross_event.compensation_intent_local_facts_to_receipt::compensation_intent:payload.target_digest->compensation_receipt:payload.receipt.target",
        "cross_event.compensation_observation_to_receipt_chain::compensation_observation:payload.classification->compensation_receipt:payload.receipt.result_classification",
        "cross_event.compensation_observation_to_receipt_chain::compensation_observation:payload.observation->compensation_receipt:payload.receipt_digest",
        "cross_event.compensation_observation_to_receipt_chain::compensation_observation:payload.observation.effect_id->compensation_receipt:payload.observation.effect_id",
        "cross_event.compensation_observation_to_receipt_chain::compensation_observation:payload.observation.fence->compensation_receipt:payload.observation.fence",
        "cross_event.compensation_observation_to_receipt_chain::compensation_observation:payload.observation.readback->compensation_receipt:payload.observation.readback",
        "cross_event.compensation_original_receipt_bind_observation::compensation_intent:payload.original_receipt.effect_id->compensation_observation:payload.original_receipt.effect_id",
        "cross_event.compensation_original_receipt_bind_observation::compensation_intent:payload.original_receipt.result_classification->compensation_observation:payload.original_receipt.result_classification",
        "cross_event.compensation_original_receipt_bind_receipt::compensation_intent:payload.original_receipt.effect_id->compensation_receipt:payload.original_receipt.effect_id",
        "cross_event.compensation_original_receipt_bind_receipt::compensation_intent:payload.original_receipt.result_classification->compensation_receipt:payload.original_receipt.result_classification",
        "cross_event.observation_to_receipt_body::observation_recorded:payload.observation.effect_id->receipt_terminal:payload.observation.effect_id",
        "cross_event.observation_to_receipt_body::observation_recorded:payload.observation.fence->receipt_terminal:payload.observation.fence",
        "cross_event.receipt_target_body_digest::receipt_terminal:payload.receipt.target->receipt_terminal:payload.target_digest",
    )
    assert len(PRIOR_MISOWNED_NATIVE_CASE_IDS_V1) == 13
    assert len(set(PRIOR_MISOWNED_NATIVE_CASE_IDS_V1)) == 13
    native_ids = {r.case_id for r in NATIVE_GUARD_ROWS_V1}
    parser_ids = set(PARSER_CASE_IDS_V1)
    for cid in PRIOR_MISOWNED_NATIVE_CASE_IDS_V1:
        assert cid not in native_ids
        assert cid not in set(EXECUTABLE_CASE_IDS_V1)
        assert cid in parser_ids
        assert cid not in DESCRIPTOR_PATH_TO_GUARD_ID_V1
    # Disposition: each is a literal parser contract with effect_contracts owner + effect_contract_invalid.
    by_id = {r.case_id: r for r in PARSER_GUARD_ROWS_V1}
    for cid in PRIOR_MISOWNED_NATIVE_CASE_IDS_V1:
        row = by_id[cid]
        assert row.native_function.startswith("effect_contracts.")
        assert row.code == "effect_contract_invalid"
        assert row.source_file == "workflow_kernel/effect_contracts.py"
        assert row.source_line >= 1
        assert row.guard_marker == f"{row.source_file}:{row.source_line}:{row.code}"
    # Manual count recomputation (no dynamic generation).
    assert len(NATIVE_GUARD_ROWS_V1) == 69
    assert len(EXECUTABLE_CASE_IDS_V1) == 69
    assert len(PARSER_GUARD_ROWS_V1) == 34
    assert len(DESCRIPTOR_PATH_TO_GUARD_ID_V1) == 156
    assert 69 + 13 == 82  # prior native population after moving the 13
    assert 156 + 34 == 190  # path map + parser contracts = all path cases


def test_idempotency_index_rejects_extra_and_missing_identity_fields(tmp_path: Path) -> None:
    from workflow_kernel.workflow_effect_ledger import _validate_index_document

    base = {
        "schema_version": "workflow_effect_idempotency_index_v1",
        "state": "bound",
        "idempotency_key": "k",
        "request_digest": DIGEST,
        "effect_id": "effect-1",
        "target_digest": TARGET_DIGEST,
        "event_id": "wel_" + ("ab" * 32),
        "fence": 1,
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
    }
    _validate_index_document(dict(base), idempotency_key="k")
    with pytest.raises(ledger.WorkflowEffectLedgerError) as extra:
        _validate_index_document({**base, "unexpected": True}, idempotency_key="k")
    assert extra.value.code == "index_corrupt"
    for field in (
        "fence",
        "command_digest",
        "adapter_id",
        "operation",
        "payload_digest",
        "event_id",
        "request_digest",
    ):
        bad = dict(base)
        del bad[field]
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            _validate_index_document(bad, idempotency_key="k")
        assert raised.value.code == "index_corrupt"


@pytest.mark.parametrize(
    ("state", "event_id", "fence", "valid"),
    [
        ("pending", None, None, True),
        ("pending", "wel_" + ("ab" * 32), None, False),
        ("pending", None, 1, False),
        ("pending", "wel_" + ("ab" * 32), 1, False),
        ("bound", "wel_" + ("ab" * 32), 1, True),
        ("bound", None, 1, False),
        ("bound", "wel_" + ("ab" * 32), None, False),
        ("bound", "wel_" + ("ab" * 32), 0, False),
        ("bound", "not-an-event", 1, False),
    ],
)
def test_idempotency_index_pending_and_bound_values_are_exhaustive(
    state: str, event_id: str | None, fence: int | None, valid: bool
) -> None:
    from workflow_kernel.workflow_effect_ledger import _validate_index_document

    document = {
        "schema_version": "workflow_effect_idempotency_index_v1",
        "state": state,
        "idempotency_key": "k",
        "request_digest": DIGEST,
        "effect_id": "effect-1",
        "target_digest": TARGET_DIGEST,
        "event_id": event_id,
        "fence": fence,
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
    }
    if valid:
        assert _validate_index_document(document, idempotency_key="k") is document
    else:
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            _validate_index_document(document, idempotency_key="k")
        assert raised.value.code == "index_corrupt"


def _mp_claim_payload(
    *,
    effect_id: str,
    request_digest: str = DIGEST,
    owner: str = "owner-a",
    idempotency_key: str = "shared-key",
) -> dict[str, object]:
    return {
        "command_digest": DIGEST,
        "target_digest": TARGET_DIGEST,
        "owner": owner,
        "request_digest": request_digest,
        "idempotency_key": idempotency_key,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "payload_digest": DIGEST,
        "effect_id": effect_id,
    }


def _mp_crash_claim_at_phase(root_s: str, crash_phase: str) -> None:
    """Spawn-importable worker that reaches a real fsync crash hook (never fabricates residue)."""
    from workflow_kernel import workflow_effect_ledger as led

    led.claim_effect_v1(
        private_root=root_s,
        effect_id="effect-a",
        payload=_mp_claim_payload(effect_id="effect-a"),
        idempotency_key="effect-a:claim",
        occurred_at="2026-08-02T00:00:00Z",
        crash_phase=crash_phase,
    )
    os._exit(97)  # pragma: no cover - the real hook must terminate first


def _mp_claim_and_report(
    root_s: str,
    result_q,
    *,
    effect_id: str,
    request_digest: str,
    owner: str,
    occurred_at: str,
) -> None:
    """Spawn-importable recovery/conflict claimant with a data-only result receipt."""
    from workflow_kernel import workflow_effect_ledger as led

    try:
        result = led.claim_effect_v1(
            private_root=root_s,
            effect_id=effect_id,
            payload=_mp_claim_payload(
                effect_id=effect_id,
                request_digest=request_digest,
                owner=owner,
            ),
            idempotency_key=f"{effect_id}:claim",
            occurred_at=occurred_at,
        )
        result_q.put(
            {
                "ok": True,
                "effect_id": result["effect_id"],
                "event_id": result["event_id"],
                "fence": result["fence"],
                "replayed": result["replayed"],
            }
        )
    except led.WorkflowEffectLedgerError as exc:
        result_q.put({"ok": False, "effect_id": effect_id, "code": exc.code})


def _join_spawned_exact(proc, expected_exitcode: int, *, timeout: float = 10.0) -> None:
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        pytest.fail(f"spawned worker {proc.name} exceeded {timeout}s and was terminated")
    assert not proc.is_alive()
    assert proc.exitcode == expected_exitcode


@pytest.mark.parametrize(
    ("crash_phase", "crash_exitcode", "claim_exists_before_recovery", "replayed"),
    [
        ("post_pending_fsync", ledger.CRASH_EXIT_POST_PENDING_FSYNC, False, False),
        (
            "post_claim_fsync_pre_bound",
            ledger.CRASH_EXIT_POST_CLAIM_FSYNC_PRE_BOUND,
            True,
            True,
        ),
    ],
)
def test_real_claim_crash_recovers_and_rejects_conflict_in_fresh_processes(
    tmp_path: Path,
    crash_phase: str,
    crash_exitcode: int,
    claim_exists_before_recovery: bool,
    replayed: bool,
) -> None:
    """Each real crash residue is recovered and conflict-checked by separate spawned processes."""
    root = tmp_path / crash_phase
    ctx = mp.get_context("spawn")
    result_q = ctx.Queue()
    children: list = []
    pre_recovery_identity: tuple[str, int] | None = None
    try:
        crash = ctx.Process(
            target=_mp_crash_claim_at_phase,
            args=(str(root), crash_phase),
            name=f"crash-{crash_phase}",
        )
        children.append(crash)
        crash.start()
        _join_spawned_exact(crash, crash_exitcode)

        pending = json.loads(
            ledger._idempotency_index_path(root, "shared-key").read_text(encoding="utf-8")
        )
        assert pending["state"] == "pending"
        assert pending["event_id"] is None
        assert pending["fence"] is None
        before = ledger.read_effect_events_v1(private_root=root, effect_id="effect-a")
        assert len(before) == int(claim_exists_before_recovery)
        assert len(list((root / "fences").glob("*.json"))) == int(claim_exists_before_recovery)
        if claim_exists_before_recovery:
            pre_recovery_identity = (
                str(before[0]["event_id"]),
                int(before[0]["payload"]["fence"]),
            )

        recovery = ctx.Process(
            target=_mp_claim_and_report,
            args=(str(root), result_q),
            kwargs={
                "effect_id": "effect-a",
                "request_digest": DIGEST,
                "owner": "owner-a",
                "occurred_at": "2026-08-02T00:00:01Z",
            },
            name=f"recover-{crash_phase}",
        )
        children.append(recovery)
        recovery.start()
        _join_spawned_exact(recovery, 0)
        recovered = result_q.get(timeout=2)
        assert recovered["ok"] is True
        assert recovered["effect_id"] == "effect-a"
        assert recovered["replayed"] is replayed

        if pre_recovery_identity is not None:
            assert (recovered["event_id"], recovered["fence"]) == pre_recovery_identity

        conflict = ctx.Process(
            target=_mp_claim_and_report,
            args=(str(root), result_q),
            kwargs={
                "effect_id": "effect-b",
                "request_digest": OTHER_DIGEST,
                "owner": "owner-b",
                "occurred_at": "2026-08-02T00:00:02Z",
            },
            name=f"conflict-{crash_phase}",
        )
        children.append(conflict)
        conflict.start()
        _join_spawned_exact(conflict, 0)
        rejected = result_q.get(timeout=2)
        assert rejected == {
            "ok": False,
            "effect_id": "effect-b",
            "code": "idempotency_conflict",
        }

        events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-a")
        assert len(events) == 1
        assert events[0]["event_type"] == "claimed"
        assert events[0]["event_id"] == recovered["event_id"]
        assert events[0]["payload"]["fence"] == recovered["fence"] == 1
        fence_docs = list((root / "fences").glob("*.json"))
        assert len(fence_docs) == 1
        assert json.loads(fence_docs[0].read_text(encoding="utf-8")) == {
            "schema_version": "workflow_effect_target_fence_v1",
            "target_digest": TARGET_DIGEST,
            "fence": 1,
        }
        # Effect paths are deliberately hashed; public replay above proves the sole file's owner.
        effect_files = list((root / "effects").glob("*.jsonl"))
        assert len(effect_files) == 1
        assert ledger.project_effect_v1(private_root=root, effect_id="effect-b")["status"] == "absent"
        bound = json.loads(
            ledger._idempotency_index_path(root, "shared-key").read_text(encoding="utf-8")
        )
        assert bound["state"] == "bound"
        assert (bound["event_id"], bound["fence"]) == (
            recovered["event_id"],
            recovered["fence"],
        )
    finally:
        for proc in children:
            if proc.is_alive():
                proc.terminate()
                proc.join(2)
        assert not [proc.name for proc in children if proc.is_alive()]
        result_q.close()
        result_q.join_thread()


def _mp_owner_claim_while_holding_target(
    root_s: str, owner_holds, release_owner, result_q
) -> None:
    from workflow_kernel import workflow_effect_ledger as led

    try:
        with led.hold_target_ownership_v1(
            private_root=root_s, target_digest=TARGET_DIGEST
        ) as ownership:
            owner_holds.set()
            if not release_owner.wait(10):
                raise TimeoutError("parent did not release target owner")
            result = led.claim_effect_v1(
                private_root=root_s,
                effect_id="effect-owner",
                payload=_mp_claim_payload(
                    effect_id="effect-owner", request_digest=DIGEST, owner="owner-held"
                ),
                idempotency_key="effect-owner:claim",
                occurred_at="2026-08-02T00:00:00Z",
                ownership=ownership,
            )
        result_q.put(
            {
                "worker": "owner",
                "ok": True,
                "event_id": result["event_id"],
                "fence": result["fence"],
            }
        )
    except BaseException as exc:  # pragma: no cover - surfaced as a failed receipt
        result_q.put({"worker": "owner", "ok": False, "error": f"{type(exc).__name__}:{exc}"})


def _mp_direct_claim_signaling_before_target_lock(root_s: str, target_attempt, result_q) -> None:
    from contextlib import contextmanager

    from workflow_kernel import workflow_effect_ledger as led

    original_lock = led._exclusive_lock_at_dirfd
    target_lock_name = led._target_lock_name(TARGET_DIGEST)

    @contextmanager
    def signaling_lock(dir_fd: int, lock_name: str, *, verify_identity=None):
        if lock_name == target_lock_name:
            target_attempt.set()
        with original_lock(
            dir_fd, lock_name, verify_identity=verify_identity
        ) as lock_fd:
            yield lock_fd

    led._exclusive_lock_at_dirfd = signaling_lock
    try:
        led.claim_effect_v1(
            private_root=root_s,
            effect_id="effect-direct",
            payload=_mp_claim_payload(
                effect_id="effect-direct", request_digest=OTHER_DIGEST, owner="owner-direct"
            ),
            idempotency_key="effect-direct:claim",
            occurred_at="2026-08-02T00:00:01Z",
        )
        result_q.put({"worker": "direct", "ok": True})
    except led.WorkflowEffectLedgerError as exc:
        result_q.put({"worker": "direct", "ok": False, "code": exc.code})
    except BaseException as exc:  # pragma: no cover - surfaced as a failed receipt
        result_q.put({"worker": "direct", "ok": False, "error": f"{type(exc).__name__}:{exc}"})


def test_mixed_owned_and_direct_claims_use_target_then_idempotency_lock_order(tmp_path: Path) -> None:
    """Barrier schedule deadlocks under the historical idempotency->target inversion."""
    root = tmp_path / "mixed-lock-order"
    ctx = mp.get_context("spawn")
    owner_holds = ctx.Event()
    target_attempt = ctx.Event()
    release_owner = ctx.Event()
    result_q = ctx.Queue()
    owner = ctx.Process(
        target=_mp_owner_claim_while_holding_target,
        args=(str(root), owner_holds, release_owner, result_q),
        name="target-owner",
    )
    direct = ctx.Process(
        target=_mp_direct_claim_signaling_before_target_lock,
        args=(str(root), target_attempt, result_q),
        name="direct-claimant",
    )
    children = [owner, direct]
    try:
        owner.start()
        assert owner_holds.wait(10), "owner never acquired target lock"
        direct.start()
        assert target_attempt.wait(10), "direct claimant never reached target lock"
        # At this barrier, target->idempotency means direct holds no idempotency lock.
        # Releasing owner can therefore complete; the inverse order deadlocks here.
        release_owner.set()
        outcomes = [result_q.get(timeout=10), result_q.get(timeout=10)]
        _join_spawned_exact(owner, 0)
        _join_spawned_exact(direct, 0)
        by_worker = {item["worker"]: item for item in outcomes}
        assert by_worker["owner"]["ok"] is True
        assert by_worker["owner"]["fence"] == 1
        assert by_worker["direct"] == {
            "worker": "direct",
            "ok": False,
            "code": "idempotency_conflict",
        }
        events = ledger.read_effect_events_v1(private_root=root, effect_id="effect-owner")
        assert len(events) == 1
        assert events[0]["event_type"] == "claimed"
        assert events[0]["event_id"] == by_worker["owner"]["event_id"]
        assert events[0]["payload"]["fence"] == 1
        assert ledger.project_effect_v1(
            private_root=root, effect_id="effect-direct"
        )["status"] == "absent"
        # Effect paths are deliberately hashed; public replay above proves the sole file's owner.
        assert len(list((root / "effects").glob("*.jsonl"))) == 1
        assert len(list((root / "fences").glob("*.json"))) == 1
    finally:
        release_owner.set()
        for proc in children:
            if proc.is_alive():
                proc.terminate()
                proc.join(2)
        assert not [proc.name for proc in children if proc.is_alive()]
        result_q.close()
        result_q.join_thread()


def test_root_identity_holds_descriptor_and_public_read_layout_corrupt(tmp_path: Path) -> None:
    root, _ = _paths(tmp_path)
    claim = ledger.claim_effect_v1(
        private_root=root,
        effect_id="effect-1",
        payload=_claim_payload(),
        idempotency_key="effect-1:claim",
        occurred_at="2026-08-02T00:00:00Z",
    )
    assert "fence" in claim
    # Replace effects child; public project must be layout_corrupt, not silent absence.
    effects = root / "effects"
    relocated = root / "effects.old"
    empty = root / "effects.new"
    empty.mkdir()
    os.rename(effects, relocated)
    os.rename(empty, effects)
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        ledger.project_effect_v1(private_root=root, effect_id="effect-1")
    assert raised.value.code == "layout_corrupt"



