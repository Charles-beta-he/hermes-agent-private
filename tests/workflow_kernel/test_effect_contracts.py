"""Slice 1: additive effect/v2 Python contract surface."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from workflow_kernel import effect_contracts


REPO_ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + ("ab" * 32)
V1_HISTORICAL_SHA = "0f57c84d49d2f34ed00b1c9d62998ddaf017e378ea9a439dbdd0d78892f3c010"


def materialize_shared_corpus_value(node: object) -> object:
    """Expand fixture-only UTF-16 recipes; markers never enter admitted domain."""
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {"$materialize_utf16_units"}:
            units = node["$materialize_utf16_units"]
            assert isinstance(units, list)
            return "".join(chr(int(u)) for u in units)
        if keys == {"$materialize_utf16_key_entry"}:
            spec = node["$materialize_utf16_key_entry"]
            assert isinstance(spec, dict)
            key = "".join(chr(int(u)) for u in spec["key_units"])
            return {key: materialize_shared_corpus_value(spec.get("value"))}
        if keys == {"$materialize_merge_utf16_key"}:
            spec = node["$materialize_merge_utf16_key"]
            assert isinstance(spec, dict)
            base = materialize_shared_corpus_value(spec["base"])
            assert isinstance(base, dict)
            key = "".join(chr(int(u)) for u in spec["key_units"])
            out = dict(base)
            out[key] = materialize_shared_corpus_value(spec.get("value"))
            return out
        return {k: materialize_shared_corpus_value(v) for k, v in node.items()}
    if isinstance(node, list):
        return [materialize_shared_corpus_value(x) for x in node]
    return node


def load_shared_corpus() -> dict:
    raw = json.loads(
        (REPO_ROOT / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json").read_text(
            encoding="utf-8"
        )
    )
    return raw


def _command_body() -> dict[str, object]:
    return {
        "schema_version": "effect_command_v1",
        "command_id": "cmd-effect-1",
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "target": {
            "target_kind": "injected_callable",
            "target_ref": "target/alpha",
        },
        "payload_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "issuer_id": "issuer.local-test",
        "policy_revision": "policy-rev-1",
        "config_revision": "config-rev-1",
        "side_effect_tier": "local_write",
        "compensation_ref": "compensation:test.restore",
        "reconcile_ref": "reconcile:test.readback",
    }


def test_effect_command_v1_parses_and_derives_request_digest() -> None:
    command = effect_contracts.parse_effect_command_v1(_command_body())
    assert command.schema_version == "effect_command_v1"
    assert command.request_digest.startswith("sha256:")
    assert len(command.request_digest) == len("sha256:") + 64
    # Canonical serialized shape excludes request_digest.
    serialized = effect_contracts.effect_command_to_dict(command)
    assert "request_digest" not in serialized
    assert serialized == effect_contracts.effect_command_body_v1(command)


def test_request_digest_rederived_and_forged_digest_rejected() -> None:
    command = effect_contracts.parse_effect_command_v1(_command_body())
    forged = dataclasses.replace(command, request_digest="sha256:" + ("ff" * 32))
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.rederive_effect_command_v1(forged)
    # Public serialized shape rejects ANY present request_digest (null/valid/malformed/mismatch).
    body = _command_body()
    derived = effect_contracts.derive_effect_command_request_digest_v1(body)
    for present in (None, derived, "sha256:" + ("ee" * 32), "nope"):
        tainted = dict(body)
        tainted["request_digest"] = present
        with pytest.raises(effect_contracts.EffectContractError) as raised:
            effect_contracts.parse_effect_command_v1(tainted)
        assert "request_digest" in str(raised.value).lower()
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.derive_effect_command_request_digest_v1(tainted)
    # Official serializer is the sole supported serialized shape (no request_digest).
    serialized = effect_contracts.effect_command_to_dict(command)
    assert "request_digest" not in serialized
    # dataclass may carry derived digest; public body helper strips it.
    assert command.request_digest.startswith("sha256:")
    assert "request_digest" not in effect_contracts.effect_command_body_v1(command)
    assert "request_digest" not in effect_contracts.effect_command_to_dict(command)
    # dataclasses.asdict is unsupported: nested mappingproxy freezes raise TypeError.
    with pytest.raises(TypeError):
        dataclasses.asdict(command)


def test_nested_command_target_is_deeply_frozen() -> None:
    command = effect_contracts.parse_effect_command_v1(_command_body())
    with pytest.raises(TypeError):
        command.target["target_ref"] = "mutated"  # type: ignore[index]


def test_effect_observation_and_terminal_receipt_contracts() -> None:
    observation = effect_contracts.parse_effect_observation_v1(
        {
            "schema_version": "effect_observation_v1",
            "effect_id": "effect-1",
            "observation_id": "obs-1",
            "classification": "applied",
            "fence": 1,
            "observed_at": "2026-08-02T00:00:02Z",
            "adapter_result_digest": DIGEST,
            "readback": {"present": True, "content_digest": DIGEST},
        }
    )
    assert observation.classification == "applied"

    body = {
        "schema_version": "effect_receipt_v1",
        "effect_id": "effect-1",
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "target": {
            "target_kind": "injected_callable",
            "target_ref": "target/alpha",
        },
        "payload_digest": DIGEST,
        "authority_binding_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "fence": 1,
        "preflight_readback": {"ok": True},
        "post_readback": {"present": True, "content_digest": DIGEST},
        "result_classification": "applied",
        "compensation_ref": "compensation:test.restore",
        "reconcile_ref": "reconcile:test.readback",
    }
    receipt = effect_contracts.parse_effect_receipt_v1(
        {**body, "receipt_digest": effect_contracts.canonical_digest_v1(body)}
    )
    assert receipt.result_classification == "applied"

    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_effect_receipt_v1({**body, "receipt_digest": DIGEST})


def test_effect_receipt_rejects_unknown_as_terminal() -> None:
    body = {
        "schema_version": "effect_receipt_v1",
        "effect_id": "effect-1",
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "target": {
            "target_kind": "injected_callable",
            "target_ref": "target/alpha",
        },
        "payload_digest": DIGEST,
        "authority_binding_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "fence": 1,
        "preflight_readback": {"ok": True},
        "post_readback": {"present": False, "content_digest": None},
        "result_classification": "unknown",
        "compensation_ref": "compensation:test.restore",
        "reconcile_ref": "reconcile:test.readback",
    }
    with pytest.raises(effect_contracts.EffectContractError) as raised:
        effect_contracts.parse_effect_receipt_v1(
            {**body, "receipt_digest": effect_contracts.canonical_digest_v1(body)}
        )
    assert raised.value.code == "effect_receipt_classification_invalid"


def test_effect_ledger_event_and_execution_event_v2_contracts() -> None:
    body = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": "effect-1",
        "sequence": 1,
        "previous_event_id": None,
        "idempotency_key": "effect-1:claimed",
        "occurred_at": "2026-08-02T00:00:00Z",
        "event_type": "claimed",
        "payload": {
            "command_digest": DIGEST,
            "target_digest": DIGEST,
            "owner": "owner-1",
            "request_digest": DIGEST,
            "idempotency_key": "effect-cmd-1",
            "adapter_id": "test.injected_adapter",
            "operation": "noop_apply",
            "fence": 1,
            "payload_digest": DIGEST,
            "effect_id": "effect-1",
        },
    }
    event = effect_contracts.parse_workflow_effect_ledger_event_v1(body)
    assert event.event_type == "claimed"
    assert event.event_id == effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)

    # Incomplete claimed payload rejected by public parse with or without event_id.
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_effect_ledger_event_v1(
            {
                **body,
                "payload": {"fence": 1},
            }
        )
    # Recomputed-ID counterpart: complete envelope + derived id still rejects incomplete payload.
    incomplete = {
        **body,
        "payload": {"fence": 1},
    }
    incomplete_with_id = {
        **incomplete,
        "event_id": effect_contracts.derive_workflow_effect_ledger_event_id_v1(incomplete),
    }
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_effect_ledger_event_v1(incomplete_with_id)

    # event_id presence semantics: omit derives; present null/empty/malformed/wrong-prefix/mismatch reject.
    omitted = effect_contracts.parse_workflow_effect_ledger_event_v1(body)
    assert omitted.event_id == effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
    valid = effect_contracts.parse_workflow_effect_ledger_event_v1(
        {**body, "event_id": omitted.event_id}
    )
    assert valid.event_id == omitted.event_id
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_effect_ledger_event_v1({**body, "event_id": None})
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_effect_ledger_event_v1({**body, "event_id": ""})
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_effect_ledger_event_v1(
            {**body, "event_id": "wel_nothex"}
        )
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_effect_ledger_event_v1(
            {**body, "event_id": "we2_" + ("a" * 64)}
        )
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_effect_ledger_event_v1(
            {**body, "event_id": "wel_" + ("0" * 64)}
        )

    v2_body = {
        "schema_version": "workflow_execution_event_v2",
        "run_id": "run-1",
        "sequence": 1,
        "previous_event_id": None,
        "idempotency_key": "run-1:waiting",
        "occurred_at": "2026-08-02T00:00:00Z",
        "event_type": "run_waiting_reconcile",
        "payload": {
            "effect_id": "effect-1",
            "effect_receipt_digest": None,
            "reason": "unknown_observation",
        },
    }
    v2 = effect_contracts.parse_workflow_execution_event_v2(v2_body)
    assert v2.event_type == "run_waiting_reconcile"
    assert v2.event_id == effect_contracts.derive_workflow_execution_event_id_v2(v2_body)
    assert (
        effect_contracts.parse_workflow_execution_event_v2(
            {**v2_body, "event_id": v2.event_id}
        ).event_id
        == v2.event_id
    )
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2({**v2_body, "event_id": None})
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2({**v2_body, "event_id": ""})
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2(
            {**v2_body, "event_id": "we2_zzz"}
        )
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2(
            {**v2_body, "event_id": "wel_" + ("a" * 64)}
        )
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2(
            {**v2_body, "event_id": "we2_" + ("0" * 64)}
        )
    # v1 predecessor rejected
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2(
            {
                **v2_body,
                "previous_event_id": "wee_" + ("a" * 64),
            }
        )
    # invalid timestamp rejected
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2(
            {**v2_body, "occurred_at": "not-a-timestamp"}
        )
    # year 0099 accepted by Python calendar parser
    early = effect_contracts.parse_workflow_execution_event_v2(
        {**v2_body, "occurred_at": "0099-06-01T00:00:00Z"}
    )
    assert early.occurred_at.startswith("0099-")
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2(
            {**v2_body, "occurred_at": "2024-02-30T00:00:00Z"}
        )


def test_schema_equivalent_identifier_constraints() -> None:
    body = _command_body()
    body["adapter_id"] = "BAD Adapter"
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_effect_command_v1(body)


def test_generated_schemas_exist_and_are_mirrored() -> None:
    names = [
        "workflow-execution-event-v2.schema.json",
        "workflow-effect-ledger-event-v1.schema.json",
        "effect-command-v1.schema.json",
        "effect-observation-v1.schema.json",
        "effect-receipt-v1.schema.json",
    ]
    for name in names:
        root = REPO_ROOT / "schemas" / name
        packaged = REPO_ROOT / "workflow_kernel" / "schemas" / name
        assert root.is_file(), name
        assert packaged.is_file(), name
        assert root.read_bytes() == packaged.read_bytes()
        payload = json.loads(root.read_text(encoding="utf-8"))
        assert payload["$id"].endswith(name)


def test_shared_command_fixture_digest_stable() -> None:
    body = _command_body()
    digest = effect_contracts.derive_effect_command_request_digest_v1(body)
    again = effect_contracts.derive_effect_command_request_digest_v1(
        effect_contracts.parse_effect_command_v1(body)
    )
    assert digest == again


def test_shared_contract_corpus_python_parity() -> None:
    import math

    corpus = load_shared_corpus()
    parsers = {
        "effect_command_v1": effect_contracts.parse_effect_command_v1,
        "effect_observation_v1": effect_contracts.parse_effect_observation_v1,
        "effect_receipt_v1": effect_contracts.parse_effect_receipt_v1,
        "workflow_effect_ledger_event_v1": effect_contracts.parse_workflow_effect_ledger_event_v1,
        "workflow_execution_event_v2": effect_contracts.parse_workflow_execution_event_v2,
    }
    case_ids = [case["id"] for case in corpus["cases"]]
    assert len(case_ids) >= 44
    assert len(set(case_ids)) == len(case_ids)
    baseline = json.loads(
        (REPO_ROOT / "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert baseline["case_count"] == 44
    assert all(i in set(case_ids) for i in baseline["case_ids"])
    # Additive 4A.5B relation matrix preserves baseline IDs and extends with rel_* cases.
    rel_cases = [c for c in corpus["cases"] if str(c["id"]).startswith("rel_")]
    assert len(rel_cases) == len(case_ids) - 44
    for case in corpus["cases"]:
        kind = case["kind"]
        if kind == "ledger_event_relation":
            kind = "workflow_effect_ledger_event_v1"
        parser = parsers[kind]
        value = materialize_shared_corpus_value(case["value"])
        if case["expect"] == "accept":
            parsed = parser(value)
            # Public results are deeply frozen (MappingProxyType / nested tuples).
            payload = getattr(parsed, "payload", None)
            if payload is not None:
                with pytest.raises((TypeError, AttributeError)):
                    payload["__mut__"] = True  # type: ignore[index]
            if "expected_event_id" in case and case["expected_event_id"]:
                assert parsed.event_id == case["expected_event_id"]
            if "expected_request_digest" in case:
                assert parsed.request_digest == case["expected_request_digest"]
        else:
            with pytest.raises(effect_contracts.EffectContractError):
                parser(value)

    for gold in corpus.get("golden_canonical", []):
        value = materialize_shared_corpus_value(gold["value"])
        assert effect_contracts.canonical_json_v1(value) == gold["canonical_json"]
        assert effect_contracts.canonical_digest_v1(value) == gold["digest"]

    for bad in corpus.get("canonical_reject", []):
        value = materialize_shared_corpus_value(bad["value"])
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.canonical_json_v1(value)

    # Language-local nonfinite rejects (JSON corpus cannot encode NaN/Inf literally).
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert math.isnan(bad) or math.isinf(bad)
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.canonical_json_v1({"n": bad})

    # Non-string key / collision rejects at public normalize/digest boundary.
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.canonical_json_v1({1: "a", "1": "b"})
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.normalize_admitted_json_v1({1: "a"})

    # Schema boundary vectors (jsonschema) for admitted nested integers + surrogate recipes.
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover
        jsonschema = None  # type: ignore[assignment]
    if jsonschema is not None:
        paths = {vector["path"] for vector in corpus.get("schema_boundary_vectors", [])}
        assert paths == {
            "effect-command-v1.schema.json",
            "effect-observation-v1.schema.json",
            "effect-receipt-v1.schema.json",
            "workflow-effect-ledger-event-v1.schema.json",
            "workflow-execution-event-v2.schema.json",
        }
        for vector in corpus.get("schema_boundary_vectors", []):
            schema_path = REPO_ROOT / "schemas" / vector["path"]
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema)
            value = materialize_shared_corpus_value(vector["value"])
            errors = list(validator.iter_errors(value))
            if vector["expect"] == "accept":
                assert errors == [], (vector["id"], errors)
            else:
                assert errors, vector["id"]


def test_unpaired_surrogates_rejected_across_all_python_parsers_helpers_ids() -> None:
    lone_high = "\ud800"
    lone_low = "\udc00"
    bad_values = [
        {"s": lone_high},
        {"s": lone_low},
        {lone_high: 1},
        {lone_low: 1},
        json.loads('{"s":"\\ud800"}'),
        json.loads('{"s":"\\udc00"}'),
    ]
    cmd = _command_body()
    obs = {
        "schema_version": "effect_observation_v1",
        "effect_id": "effect-1",
        "observation_id": "obs-1",
        "classification": "applied",
        "fence": 1,
        "observed_at": "2026-08-02T00:00:00Z",
        "adapter_result_digest": None,
        "readback": {"present": True},
    }
    receipt_body = {
        "schema_version": "effect_receipt_v1",
        "effect_id": "effect-1",
        "command_digest": DIGEST,
        "adapter_id": "test.injected_adapter",
        "operation": "noop_apply",
        "target": {"target_kind": "injected_callable", "target_ref": "target/alpha"},
        "payload_digest": DIGEST,
        "authority_binding_digest": DIGEST,
        "idempotency_key": "effect-cmd-1",
        "fence": 1,
        "preflight_readback": {"ok": True},
        "post_readback": {"present": True},
        "result_classification": "applied",
        "compensation_ref": "compensation:test.restore",
        "reconcile_ref": "reconcile:test.readback",
    }
    ledger_body = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": "effect-1",
        "sequence": 1,
        "previous_event_id": None,
        "idempotency_key": "effect-1:claimed",
        "occurred_at": "2026-08-02T00:00:00Z",
        "event_type": "claimed",
        "payload": {
            "command_digest": DIGEST,
            "target_digest": DIGEST,
            "owner": "owner-a",
            "request_digest": DIGEST,
            "idempotency_key": "effect-cmd-1",
            "adapter_id": "test.injected_adapter",
            "operation": "noop_apply",
            "fence": 1,
            "payload_digest": DIGEST,
            "effect_id": "effect-1",
        },
    }
    v2_body = {
        "schema_version": "workflow_execution_event_v2",
        "run_id": "run-1",
        "sequence": 1,
        "previous_event_id": None,
        "idempotency_key": "run-1:waiting",
        "occurred_at": "2026-08-02T00:00:00Z",
        "event_type": "run_waiting_reconcile",
        "payload": {"effect_id": "effect-1"},
    }

    helpers = [
        effect_contracts.canonical_json_v1,
        effect_contracts.canonical_digest_v1,
        effect_contracts.normalize_admitted_json_v1,
        effect_contracts.freeze_admitted_json_v1,
    ]
    for bad in bad_values:
        for helper in helpers:
            with pytest.raises(effect_contracts.EffectContractError) as raised:
                helper(bad)
            assert raised.value.code == "effect_contract_not_canonical"
            assert "surrogate" in str(raised.value).lower()
            assert not isinstance(raised.value.__cause__, UnicodeEncodeError)
        if isinstance(bad, dict) and "s" in bad:
            with pytest.raises(effect_contracts.EffectContractError):
                effect_contracts.parse_effect_command_v1(
                    {
                        **cmd,
                        "target": {
                            "target_kind": "injected_callable",
                            "target_ref": bad["s"],
                        },
                    }
                )
            with pytest.raises(effect_contracts.EffectContractError):
                effect_contracts.derive_effect_command_request_digest_v1(
                    {
                        **cmd,
                        "target": {
                            "target_kind": "injected_callable",
                            "target_ref": bad["s"],
                        },
                    }
                )
            with pytest.raises(effect_contracts.EffectContractError):
                effect_contracts.derive_workflow_effect_ledger_event_id_v1(
                    {
                        **ledger_body,
                        "payload": {**ledger_body["payload"], "owner": bad["s"]},
                    }
                )
        # observation/readback and other admitted object fields
        rb = bad if isinstance(bad, dict) else {"v": bad}
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.parse_effect_observation_v1({**obs, "readback": rb})
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.parse_effect_receipt_v1(
                {
                    **receipt_body,
                    "post_readback": rb,
                    "receipt_digest": effect_contracts.canonical_digest_v1(receipt_body),
                }
            )
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.canonical_digest_v1({**receipt_body, "post_readback": rb})
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.parse_workflow_execution_event_v2({**v2_body, "payload": rb})
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.derive_workflow_execution_event_id_v2({**v2_body, "payload": rb})
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.parse_workflow_effect_ledger_event_v1(
                {
                    **ledger_body,
                    "event_id": effect_contracts.derive_workflow_effect_ledger_event_id_v1(
                        ledger_body
                    ),
                    "payload": {
                        **{
                            "fence": 1,
                            "command_digest": DIGEST,
                            "request_digest": DIGEST,
                            "target_digest": DIGEST,
                            "authority_binding_digest": DIGEST,
                            "preflight_readback": rb,
                            "preflight_readback_digest": DIGEST,
                            "adapter_id": "test.injected_adapter",
                            "operation": "noop_apply",
                            "payload_digest": DIGEST,
                            "idempotency_key": "effect-cmd-1",
                            "effect_id": "effect-1",
                        }
                    },
                    "event_type": "intent_recorded",
                    "sequence": 2,
                    "previous_event_id": "wel_" + ("a" * 64),
                    "idempotency_key": "effect-1:intent",
                    "occurred_at": "2026-08-02T00:00:01Z",
                }
            )

    # Positive: supplementary-plane scalar and emoji.
    positive = {"emoji": "😀", "plane": "\U00010000"}
    text = effect_contracts.canonical_json_v1(positive)
    assert "😀" in text
    assert "\U00010000" in text
    assert effect_contracts.canonical_digest_v1(positive).startswith("sha256:")
    ordered = effect_contracts.canonical_json_v1({chr(0xE000): 2, chr(0x10000): 1})
    assert ordered.index(json.dumps(chr(0x10000), ensure_ascii=False)) < ordered.index(
        json.dumps(chr(0xE000), ensure_ascii=False)
    )


def test_js_safe_integer_and_strict_keys() -> None:
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_workflow_execution_event_v2(
            {
                "schema_version": "workflow_execution_event_v2",
                "run_id": "run-1",
                "sequence": 9007199254740992,
                "previous_event_id": None,
                "idempotency_key": "k",
                "occurred_at": "2026-08-02T00:00:00Z",
                "event_type": "run_started",
                "payload": {},
            }
        )
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_effect_observation_v1(
            {
                "schema_version": "effect_observation_v1",
                "effect_id": "e",
                "observation_id": "o",
                "classification": "applied",
                "fence": 1,
                "observed_at": "2026-08-02T00:00:00Z",
                "adapter_result_digest": None,
                "readback": {},
                "extra": True,
            }
        )


def test_nested_list_deep_freeze_and_invalid_calendar() -> None:
    obs = effect_contracts.parse_effect_observation_v1(
        {
            "schema_version": "effect_observation_v1",
            "effect_id": "effect-1",
            "observation_id": "obs-1",
            "classification": "applied",
            "fence": 1,
            "observed_at": "2026-08-02T00:00:02Z",
            "adapter_result_digest": DIGEST,
            "readback": {"items": [["before"]]},
        }
    )
    nested = obs.readback["items"]
    with pytest.raises(TypeError):
        nested[0][0] = "after"  # type: ignore[index]
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_effect_observation_v1(
            {
                "schema_version": "effect_observation_v1",
                "effect_id": "effect-1",
                "observation_id": "obs-1",
                "classification": "applied",
                "fence": 1,
                "observed_at": "2026-02-30T00:00:02Z",
                "adapter_result_digest": DIGEST,
                "readback": {"ok": True},
            }
        )


def test_canonical_float_number_forms() -> None:
    d1 = effect_contracts.canonical_digest_v1({"n": 1.0})
    d2 = effect_contracts.canonical_digest_v1({"n": 1})
    assert d1 == d2
    assert effect_contracts.canonical_json_v1({"n": 1.0}) == '{"n":1}'
    d0 = effect_contracts.canonical_digest_v1({"n": -0.0})
    assert d0 == effect_contracts.canonical_digest_v1({"n": 0})
    assert effect_contracts.canonical_json_v1({"n": -0.0}) == '{"n":0}'
    # Fail-closed admitted domain: non-integer JSON numbers rejected identically.
    for bad in (1e-7, 1e21, 0.10000000000000002, float("nan"), float("inf")):
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.canonical_json_v1({"n": bad})
    # UTF-16 key ordering: U+10000 (surrogate pair) sorts before U+E000.
    ordered = effect_contracts.canonical_json_v1({chr(0xE000): 2, chr(0x10000): 1})
    assert ordered == effect_contracts.canonical_json_v1({chr(0x10000): 1, chr(0xE000): 2})
    assert ordered.index(json.dumps(chr(0x10000), ensure_ascii=False)) < ordered.index(
        json.dumps(chr(0xE000), ensure_ascii=False)
    )


def test_unpaired_surrogates_rejected_with_contract_error() -> None:
    lone_high = "\ud800"
    lone_low = "\udc00"
    for bad in (
        {"s": lone_high},
        {"s": lone_low},
        {lone_high: 1},
        {lone_low: 1},
        json.loads('{"s":"\\ud800"}'),
        json.loads('{"s":"\\udc00"}'),
    ):
        with pytest.raises(effect_contracts.EffectContractError) as raised:
            effect_contracts.canonical_json_v1(bad)
        assert raised.value.code == "effect_contract_not_canonical"
        assert "surrogate" in str(raised.value).lower()
        with pytest.raises(effect_contracts.EffectContractError) as d:
            effect_contracts.canonical_digest_v1(bad)
        assert d.value.code == "effect_contract_not_canonical"
        assert not isinstance(d.value.__cause__, UnicodeEncodeError)
    # Positive: supplementary-plane scalar and emoji.
    positive = {"emoji": "😀", "plane": "\U00010000"}
    text = effect_contracts.canonical_json_v1(positive)
    assert "😀" in text
    assert "\U00010000" in text
    assert effect_contracts.canonical_digest_v1(positive).startswith("sha256:")
    ordered = effect_contracts.canonical_json_v1({chr(0xE000): 2, chr(0x10000): 1})
    assert ordered.index(json.dumps(chr(0x10000), ensure_ascii=False)) < ordered.index(
        json.dumps(chr(0xE000), ensure_ascii=False)
    )


def test_timestamp_year_domain_0001_9999() -> None:
    base = {
        "schema_version": "effect_observation_v1",
        "effect_id": "effect-1",
        "observation_id": "obs-1",
        "classification": "unknown",
        "fence": 1,
        "adapter_result_digest": None,
        "readback": {"present": False},
    }
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_effect_observation_v1({**base, "observed_at": "0000-01-01T00:00:00Z"})
    effect_contracts.parse_effect_observation_v1({**base, "observed_at": "0001-01-01T00:00:00Z"})
    effect_contracts.parse_effect_observation_v1({**base, "observed_at": "0099-12-31T23:59:59Z"})
    effect_contracts.parse_effect_observation_v1({**base, "observed_at": "2024-02-29T00:00:00Z"})
    with pytest.raises(effect_contracts.EffectContractError):
        effect_contracts.parse_effect_observation_v1({**base, "observed_at": "2026-02-29T00:00:00Z"})


def test_shared_corpus_command_request_digest_matrix_and_helper_probes() -> None:
    corpus = load_shared_corpus()
    for case in corpus["cases"]:
        if not str(case["id"]).startswith("command_request_digest") and case["id"] != "command_absent_request_digest_accept":
            continue
        value = materialize_shared_corpus_value(case["value"])
        if case["expect"] == "accept":
            parsed = effect_contracts.parse_effect_command_v1(value)
            assert parsed.request_digest == case["expected_request_digest"]
            assert "request_digest" not in effect_contracts.effect_command_to_dict(parsed)
            assert (
                effect_contracts.derive_effect_command_request_digest_v1(value)
                == case["expected_request_digest"]
            )
        else:
            with pytest.raises(effect_contracts.EffectContractError):
                effect_contracts.parse_effect_command_v1(value)
            with pytest.raises(effect_contracts.EffectContractError):
                effect_contracts.derive_effect_command_request_digest_v1(value)
    # Direct helper matrix from corpus.
    body = _command_body()
    derived = effect_contracts.derive_effect_command_request_digest_v1(body)
    for row in corpus.get("request_digest_helper_matrix", []):
        if row["id"] == "absent":
            assert effect_contracts.derive_effect_command_request_digest_v1(body) == derived
            continue
        tainted = dict(body)
        tainted["request_digest"] = row["value"]
        with pytest.raises(effect_contracts.EffectContractError):
            effect_contracts.derive_effect_command_request_digest_v1(tainted)


def _semantic_diff_paths(a: object, b: object, prefix: str = "") -> list[str]:
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


def test_semantic_relation_ssot_and_matrix_python() -> None:
    """4A.5B.3: inventory exact-cover, accept/reject twins, explicit bookkeeping, read-only parity."""
    from workflow_kernel.workflow_effect_ledger import _map_effect_contract_error
    from workflow_kernel.effect_ledger_relation_manifests import (
        CROSS_EVENT_RELATION_MANIFEST_V1,
        INTRA_EVENT_RELATION_MANIFEST_V1,
    )
    import importlib.util

    gen_path = REPO_ROOT / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    spec = importlib.util.spec_from_file_location("semantic_ssot_gen_4a5b2", gen_path)
    assert spec and spec.loader
    ssot_gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ssot_gen)

    corpus = load_shared_corpus()
    ssot = json.loads(
        (
            REPO_ROOT
            / "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json"
        ).read_text(encoding="utf-8")
    )
    rel_ids = sorted(r["relation_id"] for r in ssot["relations"])
    assert rel_ids
    assert len(rel_ids) == len(set(rel_ids))
    assert set(rel_ids) == set(INTRA_EVENT_RELATION_MANIFEST_V1)

    # Exhaustive path inventory mechanically derived; future field additions fail.
    derived = {(et, path) for et, path in ssot_gen.derive_path_set()}
    inventory = {(row["event_type"], row["path"]) for row in ssot["path_inventory"]}
    assert inventory == derived
    assert len(ssot["path_inventory"]) == len(inventory)
    assert len(ssot["path_inventory"]) >= 307
    classes = {row["classification"] for row in ssot["path_inventory"]}
    assert classes <= {
        "structural_primitive_local",
        "intra_event_relation",
        "cross_event_replay_relation",
    }
    cross_ids = {c["relation_id"] for c in ssot["cross_event_relations"]}
    assert cross_ids == set(CROSS_EVENT_RELATION_MANIFEST_V1)
    # Exactly one classification per path already implied by set equality.
    for row in ssot["path_inventory"]:
        if row["classification"] == "intra_event_relation":
            assert row["relation_id"] in set(rel_ids)
        elif row["classification"] == "cross_event_replay_relation":
            assert row["relation_id"] in cross_ids
            if row.get("relation_ids"):
                assert set(row["relation_ids"]) <= cross_ids
                assert row["relation_id"] in row["relation_ids"]
        else:
            assert row.get("rationale")
            low = str(row["rationale"]).lower()
            assert "cross-event" not in low
            assert "replay" not in low
            assert "durable" not in low

    # No uncovered relation: every SSOT relation appears as accept+reject matrix rows.
    matrix = [c for c in corpus["cases"] if str(c["id"]).startswith("rel_")]
    by_rel: dict[str, dict[str, dict]] = {}
    for case in matrix:
        rid = case["relation_id"]
        by_rel.setdefault(rid, {})[case["expect"]] = case
    assert sorted(by_rel) == rel_ids
    for rid in rel_ids:
        assert set(by_rel[rid]) == {"accept", "reject"}, rid

    # Primitives are labeled, not silently omitted; only non-bindable locals remain opaque.
    assert ssot["primitives_opaque"]
    assert all(p.get("rationale") for p in ssot["primitives_opaque"])
    opaque_paths = {(p["event_type"], p["path"]) for p in ssot["primitives_opaque"]}
    for et, path in opaque_paths:
        inv_row = next(r for r in ssot["path_inventory"] if r["event_type"] == et and r["path"] == path)
        assert inv_row["classification"] == "structural_primitive_local"

    # Cross-event table is explicit with source/dest/validator/parameterized test IDs.
    assert ssot["cross_event_relations"]
    for x in ssot["cross_event_relations"]:
        assert x.get("scope") == "cross_event_python_replay"
        for key in (
            "relation_id",
            "source_event",
            "source_fact",
            "destination_event",
            "destination_path",
            "validator",
            "focused_test_id",
            "rationale",
        ):
            assert x.get(key), (x.get("relation_id"), key)
        rid = x["relation_id"]
        assert x["focused_test_id"] == f"test_cross_event_relation_enforced[{rid}]"

    required_missing = {
        "compensation_observation.original_receipt_effect_id_eq_payload",
        "compensation_receipt.observation_fence_eq_payload",
    }
    assert required_missing <= set(rel_ids)

    # Corpus metadata must match actual relation twin counts (not stale 79/158).
    meta = corpus.get("semantic_relation_matrix") or {}
    assert int(meta["relation_count"]) == len(rel_ids)
    assert int(meta["cases_added"]) == len(rel_ids) * 2

    parity_rows = []
    for rid in rel_ids:
        acc = by_rel[rid]["accept"]
        rej = by_rel[rid]["reject"]
        acc_val = materialize_shared_corpus_value(acc["value"])
        rej_val = materialize_shared_corpus_value(rej["value"])
        parsed = effect_contracts.parse_workflow_effect_ledger_event_v1(acc_val)
        assert parsed.event_id == acc.get("expected_event_id") or parsed.event_id == (
            effect_contracts.derive_workflow_effect_ledger_event_id_v1(
                {k: v for k, v in acc_val.items() if k != "event_id"}  # type: ignore[union-attr]
            )
        )
        with pytest.raises(effect_contracts.EffectContractError) as raised:
            effect_contracts.parse_workflow_effect_ledger_event_v1(rej_val)
        reason = _map_effect_contract_error(raised.value)
        expected = rej.get("expected_reason_class") or "payload_invalid"
        assert reason == expected, (rid, reason, str(raised.value))

        # Mechanical true one-field assertion vs accept twin using explicit bookkeeping_paths.
        mutated = rej.get("mutated_path")
        if mutated:
            assert "bookkeeping_paths" in rej, rid
            bookkeeping = list(rej.get("bookkeeping_paths") or [])
            diff_paths = _semantic_diff_paths(acc_val, rej_val)
            collapsed: list[str] = []
            for p in diff_paths:
                if p == mutated or p.startswith(mutated + "."):
                    if mutated not in collapsed:
                        collapsed.append(mutated)
                else:
                    collapsed.append(p)
            assert set(collapsed) == {mutated, *bookkeeping}, (rid, collapsed, mutated, bookkeeping, diff_paths)

        parity_rows.append(
            {
                "event_type": rej["event_type"],
                "relation_id": rid,
                "expected": expected,
                "python_actual": reason,
            }
        )

    # Read-only parity artifact: generator owns writes; tests never mutate fixtures.
    parity_path = REPO_ROOT / "tests/workflow_kernel/fixtures/archives/semantic-relation-parity-4a5b.json"
    before = parity_path.read_bytes()
    artifact = json.loads(before.decode("utf-8"))
    py_rows = artifact.get("python_rows") or []
    assert py_rows, "parity artifact missing python_rows; ordinary generator --write is validation-only zero-write — restore the checked-in parity artifact or run a closed migration tool"
    py_by = {r["relation_id"]: r for r in py_rows}
    assert sorted(py_by) == rel_ids
    assert all(r.get("python_actual") is not None for r in py_rows)
    # mismatch_count recomputed, not trusted as hardcoded sole authority
    recomputed = sum(1 for r in py_rows if r.get("python_actual") != r.get("expected"))
    # if TS column present and non-null, include pairwise mismatches
    if any(r.get("ts_actual") is not None for r in py_rows):
        assert all(r.get("ts_actual") is not None for r in py_rows), "no missing TS row loophole"
        recomputed = sum(
            1
            for r in py_rows
            if r["python_actual"] != r["expected"]
            or r["ts_actual"] != r["expected"]
            or r["python_actual"] != r["ts_actual"]
        )
    assert artifact.get("relation_count") == len(rel_ids)
    assert artifact.get("mismatch_count") == recomputed
    assert recomputed == 0
    # live python outcomes match artifact
    for row in parity_rows:
        assert py_by[row["relation_id"]]["python_actual"] == row["python_actual"]
    assert parity_path.read_bytes() == before


def test_observation_classification_bogus_is_payload_invalid() -> None:
    """P1.2: exact one-field wrapper classification='bogus' maps to payload_invalid in Python."""
    from workflow_kernel.workflow_effect_ledger import _map_effect_contract_error

    corpus = load_shared_corpus()
    acc = next(
        c
        for c in corpus["cases"]
        if c["id"] == "rel_observation_recorded.wrapper_classification_eq_body_accept"
    )
    val = materialize_shared_corpus_value(json.loads(json.dumps(acc["value"])))
    assert isinstance(val, dict)
    payload = dict(val["payload"])  # type: ignore[index]
    # Mutate ONLY wrapper payload.classification; keep embedded observation.classification.
    payload["classification"] = "bogus"
    body = {k: v for k, v in val.items() if k != "event_id"}
    body["payload"] = payload
    event = dict(body)
    event["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
    with pytest.raises(effect_contracts.EffectContractError) as raised:
        effect_contracts.parse_workflow_effect_ledger_event_v1(event)
    assert "unsupported classification" in str(raised.value).lower() or "classification" in str(raised.value).lower()
    assert _map_effect_contract_error(raised.value) == "payload_invalid"

    # Unsupported ledger event_type remains schema_invalid (one-field + recomputed event_id).
    good = materialize_shared_corpus_value(json.loads(json.dumps(acc["value"])))
    assert isinstance(good, dict)
    bad_type = dict(good)
    bad_type["event_type"] = "not_a_ledger_event"
    bad_type["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(
        {k: v for k, v in bad_type.items() if k != "event_id"}
    )
    with pytest.raises(effect_contracts.EffectContractError) as raised2:
        effect_contracts.parse_workflow_effect_ledger_event_v1(bad_type)
    assert _map_effect_contract_error(raised2.value) == "schema_invalid"

    # Unsupported schema_version → schema_invalid
    bad_sv = dict(good)
    bad_sv["schema_version"] = "nope"
    bad_sv["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(
        {k: v for k, v in bad_sv.items() if k != "event_id"}
    )
    with pytest.raises(effect_contracts.EffectContractError) as raised3:
        effect_contracts.parse_workflow_effect_ledger_event_v1(bad_sv)
    assert _map_effect_contract_error(raised3.value) == "schema_invalid"


def test_bookkeeping_guard_detects_second_command_digest_mutation() -> None:
    """P2: injecting command_digest as a second mutation must fail the twin-diff guard."""
    corpus = load_shared_corpus()
    acc = next(
        c
        for c in corpus["cases"]
        if c["id"] == "rel_observation_recorded.wrapper_classification_eq_body_accept"
    )
    rej = next(
        c
        for c in corpus["cases"]
        if c["id"] == "rel_observation_recorded.wrapper_classification_eq_body_reject"
    )
    acc_val = materialize_shared_corpus_value(json.loads(json.dumps(acc["value"])))
    rej_val = materialize_shared_corpus_value(json.loads(json.dumps(rej["value"])))
    assert isinstance(acc_val, dict) and isinstance(rej_val, dict)
    # Inject second semantic mutation on command_digest
    payload = dict(rej_val["payload"])  # type: ignore[index]
    payload["command_digest"] = "sha256:" + ("cd" * 32)
    rej_val = dict(rej_val)
    rej_val["payload"] = payload
    body = {k: v for k, v in rej_val.items() if k != "event_id"}
    rej_val["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
    mutated = rej["mutated_path"]
    bookkeeping = list(rej.get("bookkeeping_paths") or [])
    diffs = _semantic_diff_paths(acc_val, rej_val)
    collapsed: list[str] = []
    for p in diffs:
        if p == mutated or p.startswith(str(mutated) + "."):
            if mutated not in collapsed:
                collapsed.append(mutated)
        else:
            collapsed.append(p)
    assert set(collapsed) != {mutated, *bookkeeping}
    assert any(p.endswith("command_digest") or p == "payload.command_digest" for p in collapsed)


def test_semantic_ssot_generator_check_mode_clean() -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_semantic_ssot_generator_check_mode_location_independent(tmp_path) -> None:
    """Generator --check must work from arbitrary cwd without caller PYTHONPATH.

    Regression for producer env-leak: invoking the absolute script path makes
    CPython set sys.path[0]=scripts/, which previously missed this checkout's
    workflow_kernel (or lost to a foreign editable) unless PYTHONPATH was injected.
    """
    import hashlib
    import os
    import subprocess
    import sys

    gen_path = REPO_ROOT / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    governed = [
        REPO_ROOT / "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json",
        REPO_ROOT / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json",
        REPO_ROOT / "tests/workflow_kernel/fixtures/archives/semantic-relation-parity-4a5b.json",
        REPO_ROOT / "workflow_kernel/effect_ledger_relation_manifests.py",
        REPO_ROOT / "apps/workflow-contracts/src/effect-ledger-relation-manifests.ts",
        REPO_ROOT / "scripts/fill_semantic_relation_parity_ts.mjs",
        gen_path,
    ]

    def _sha_map() -> dict[str, str]:
        out: dict[str, str] = {}
        for p in governed:
            data = p.read_bytes()
            out[str(p.relative_to(REPO_ROOT))] = hashlib.sha256(data).hexdigest()
        return out

    before = _sha_map()
    external_cwd = tmp_path / "external-cwd"
    external_cwd.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, str(gen_path), "--check"],
        cwd=str(external_cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = _sha_map()
    assert after == before, f"check mode must not write governed artifacts: {before!r} vs {after!r}"


def test_semantic_ssot_generator_check_adversarial_nonzero() -> None:
    """P2: adversarial fixture mutations must make --check fail (no self-referential success)."""
    import copy
    import importlib.util
    import tempfile
    from pathlib import Path as P

    gen_path = REPO_ROOT / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    spec = importlib.util.spec_from_file_location("semantic_ssot_gen_adv", gen_path)
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    ssot = json.loads((REPO_ROOT / "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json").read_text())
    corpus = json.loads((REPO_ROOT / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json").read_text())
    parity = json.loads((REPO_ROOT / "tests/workflow_kernel/fixtures/archives/semantic-relation-parity-4a5b.json").read_text())

    def run_check(ssot_obj, corpus_obj, parity_obj) -> int:
        with tempfile.TemporaryDirectory() as td:
            root = P(td)
            s_path = root / "ssot.json"
            c_path = root / "corpus.json"
            p_path = root / "parity.json"
            s_path.write_text(json.dumps(ssot_obj), encoding="utf-8")
            c_path.write_text(json.dumps(corpus_obj), encoding="utf-8")
            p_path.write_text(json.dumps(parity_obj), encoding="utf-8")
            old_ssot, old_corpus, old_parity = gen.SSOT_PATH, gen.CORPUS_PATH, gen.PARITY_PATH
            try:
                gen.SSOT_PATH = s_path
                gen.CORPUS_PATH = c_path
                gen.PARITY_PATH = p_path
                # Avoid spawning node in adversarial in-process checks by patching shutil.which
                import shutil

                real_which = shutil.which

                def no_node(cmd, *a, **k):
                    if cmd == "node":
                        return None
                    return real_which(cmd, *a, **k)

                shutil.which = no_node  # type: ignore[assignment]
                try:
                    return int(gen.main.__wrapped__() if hasattr(gen.main, "__wrapped__") else _run_check_main(gen))
                finally:
                    shutil.which = real_which  # type: ignore[assignment]
            finally:
                gen.SSOT_PATH = old_ssot
                gen.CORPUS_PATH = old_corpus
                gen.PARITY_PATH = old_parity

    def _run_check_main(module) -> int:
        import sys as _sys

        argv = list(_sys.argv)
        try:
            _sys.argv = ["gen", "--check"]
            try:
                return int(module.main())
            except SystemExit as exc:
                code = exc.code
                if code is None:
                    return 0
                if isinstance(code, int):
                    return code
                return 1
        finally:
            _sys.argv = argv

    # 1) remove one complete relation + twins + parity row
    victim = ssot["relations"][0]["relation_id"]
    ssot1 = copy.deepcopy(ssot)
    ssot1["relations"] = [r for r in ssot1["relations"] if r["relation_id"] != victim]
    corpus1 = copy.deepcopy(corpus)
    corpus1["cases"] = [c for c in corpus1["cases"] if c.get("relation_id") != victim]
    parity1 = copy.deepcopy(parity)
    parity1["python_rows"] = [r for r in parity1["python_rows"] if r["relation_id"] != victim]
    parity1["rows"] = parity1["python_rows"]
    parity1["relation_count"] = len(parity1["python_rows"])
    parity1["mismatch_count"] = 0
    assert run_check(ssot1, corpus1, parity1) != 0

    # 2) reclassify claimed.sequence as local
    ssot2 = copy.deepcopy(ssot)
    for row in ssot2["path_inventory"]:
        if row["event_type"] == "claimed" and row["path"] == "sequence":
            row["classification"] = "structural_primitive_local"
            row.pop("relation_id", None)
            row["rationale"] = "adversarial local misclassification"
    assert run_check(ssot2, copy.deepcopy(corpus), copy.deepcopy(parity)) != 0

    # 3) stale python_actual with mismatch_count=0
    parity3 = copy.deepcopy(parity)
    parity3["python_rows"][0]["python_actual"] = "schema_invalid"
    parity3["mismatch_count"] = 0
    assert run_check(copy.deepcopy(ssot), copy.deepcopy(corpus), parity3) != 0

    # 4) stale corpus metadata 79/158
    corpus4 = copy.deepcopy(corpus)
    corpus4["semantic_relation_matrix"] = {
        **(corpus4.get("semantic_relation_matrix") or {}),
        "relation_count": 79,
        "cases_added": 158,
    }
    assert run_check(copy.deepcopy(ssot), corpus4, copy.deepcopy(parity)) != 0
