#!/usr/bin/env python3
"""Check 4A.5B.2 semantic relation SSOT inventory + parity artifact.

Modes:
  --check   recompute independently and fail on drift (no fixture mutation)
  --write   ordinary validation-only zero-write: identical to --check success when already
            matching; refuses any drift and never publishes/migrates SSOT/corpus/parity.
            Migrations are closed on this path.

Ordinary --write is validation-only zero-write. It does not rewrite SSOT, corpus, or parity.
"""
from __future__ import annotations

import sys

# Prevent __pycache__ creation for any subsequent project imports / ordinary check/write.
sys.dont_write_bytecode = True

import argparse
import copy
import json
from pathlib import Path
from typing import Any

# Location-independent import bootstrap.
# When invoked as `python /abs/repo/scripts/<this>.py`, CPython sets sys.path[0] to
# the scripts/ directory. That shadows the checkout root and can also let a foreign
# editable install win for `workflow_kernel`. Pin repo root first, before any
# project imports, without relying on caller PYTHONPATH / cwd / editable install.
_SCRIPT_DIR = Path(__file__).resolve().parent
REPO = _SCRIPT_DIR.parent


def _bootstrap_repo_import_path() -> None:
    repo_s = str(REPO)
    # Prefer replacing the script-dir entry Python injected as path[0].
    if sys.path and Path(sys.path[0]).resolve() == _SCRIPT_DIR:
        sys.path[0] = repo_s
    else:
        while repo_s in sys.path:
            sys.path.remove(repo_s)
        sys.path.insert(0, repo_s)
    # Drop residual script-dir entries so scripts/ cannot shadow package imports.
    cleaned: list[str] = []
    for p in sys.path:
        try:
            if p != repo_s and Path(p).resolve() == _SCRIPT_DIR:
                continue
        except (OSError, ValueError):
            pass
        cleaned.append(p)
    sys.path[:] = cleaned
    # Ensure repo root remains first after filtering.
    if not sys.path or sys.path[0] != repo_s:
        while repo_s in sys.path:
            sys.path.remove(repo_s)
        sys.path.insert(0, repo_s)
    # If a foreign copy was already imported (e.g. in-process reuse), drop it.
    for name in list(sys.modules):
        if name != "workflow_kernel" and not name.startswith("workflow_kernel."):
            continue
        mod = sys.modules.get(name)
        origin = getattr(mod, "__file__", None) if mod is not None else None
        if origin is None:
            del sys.modules[name]
            continue
        try:
            resolved = str(Path(origin).resolve())
        except (OSError, ValueError):
            del sys.modules[name]
            continue
        if resolved != repo_s and not resolved.startswith(repo_s + "/"):
            del sys.modules[name]


_bootstrap_repo_import_path()

SSOT_PATH = REPO / "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json"
CORPUS_PATH = REPO / "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json"
PARITY_PATH = REPO / "tests/workflow_kernel/fixtures/archives/semantic-relation-parity-4a5b.json"
# Immutable canonical corpus manifest (outside live corpus). Ordinary --write/--check
# never regenerate this file; only a separately closed migration tool may update it.
CANONICAL_CORPUS_MANIFEST_RAW_SHA256_V1 = "c0f935c09ae6bbf16ec8eb066bdad1a5b1426cc8be6a6be5da8eed6f658e40fd"
BASELINE_MANIFEST_RAW_SHA256_V1 = "fa4566baeb9fe32a8e8146a9201f1142fdc8040d0a5ed450b47814f2a9f0b960"
CORPUS_CANONICAL_MANIFEST_PATH = (
    REPO
    / "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-canonical-4a5b11.manifest.json"
)
TRANSACTION_JOURNAL_NAME = ".hgwk-semantic-ssot-write-journal-v1.json"

EVENT_TYPES = [
    "claimed",
    "intent_recorded",
    "observation_recorded",
    "reconciled",
    "receipt_terminal",
    "compensation_intent",
    "compensation_observation",
    "compensation_receipt",
]

ENVELOPE_FIELDS = [
    "schema_version",
    "event_id",
    "effect_id",
    "sequence",
    "previous_event_id",
    "event_type",
    "payload",
    "idempotency_key",
    "occurred_at",
]

# Exact payload keys from public validators (typed roots only; dynamic JSON at root).
PAYLOAD_KEYS: dict[str, list[str]] = {
    "claimed": [
        "adapter_id",
        "command_digest",
        "effect_id",
        "fence",
        "idempotency_key",
        "operation",
        "owner",
        "payload_digest",
        "request_digest",
        "target_digest",
    ],
    "intent_recorded": [
        "adapter_id",
        "authority_binding_digest",
        "command_digest",
        "effect_id",
        "fence",
        "idempotency_key",
        "operation",
        "payload_digest",
        "preflight_readback",
        "preflight_readback_digest",
        "request_digest",
        "target_digest",
    ],
    "observation_recorded": [
        "adapter_id",
        "adapter_result_digest",
        "authority_binding_digest",
        "classification",
        "command_digest",
        "effect_id",
        "fence",
        "idempotency_key",
        "observation",
        "operation",
        "payload_digest",
        "readback",
        "readback_digest",
        "request_digest",
        "target_digest",
    ],
    "reconciled": [
        "adapter_id",
        "authority_binding_digest",
        "classification",
        "command_digest",
        "effect_id",
        "evidence_digest",
        "fence",
        "idempotency_key",
        "operation",
        "payload_digest",
        "readback",
        "readback_digest",
        "request_digest",
        "target_digest",
    ],
    "receipt_terminal": [
        "adapter_id",
        "authority_binding_digest",
        "command_digest",
        "effect_id",
        "fence",
        "idempotency_key",
        "observation",
        "observation_digest",
        "operation",
        "payload_digest",
        "receipt",
        "receipt_digest",
        "request_digest",
        "result_classification",
        "target_digest",
    ],
    "compensation_intent": [
        "adapter_id",
        "authority_binding_digest",
        "command_digest",
        "compensation_ref",
        "effect_id",
        "fence",
        "idempotency_key",
        "operation",
        "original_receipt",
        "original_receipt_digest",
        "payload_digest",
        "preflight_readback",
        "preflight_readback_digest",
        "request_digest",
        "target_digest",
    ],
    "compensation_observation": [
        "adapter_id",
        "authority_binding_digest",
        "classification",
        "command_digest",
        "effect_id",
        "fence",
        "idempotency_key",
        "observation",
        "operation",
        "original_receipt",
        "original_receipt_digest",
        "payload_digest",
        "readback",
        "readback_digest",
        "request_digest",
        "target_digest",
    ],
    "compensation_receipt": [
        "adapter_id",
        "authority_binding_digest",
        "command_digest",
        "effect_id",
        "fence",
        "idempotency_key",
        "observation",
        "observation_digest",
        "operation",
        "original_receipt",
        "original_receipt_digest",
        "payload_digest",
        "receipt",
        "receipt_digest",
        "request_digest",
        "target_digest",
    ],
}

OBSERVATION_BODY_FIELDS = [
    "adapter_result_digest",
    "classification",
    "effect_id",
    "fence",
    "observation_id",
    "observed_at",
    "readback",
    "schema_version",
]

RECEIPT_BODY_FIELDS = [
    "adapter_id",
    "authority_binding_digest",
    "command_digest",
    "compensation_ref",
    "effect_id",
    "fence",
    "idempotency_key",
    "operation",
    "payload_digest",
    "post_readback",
    "preflight_readback",
    "receipt_digest",
    "reconcile_ref",
    "result_classification",
    "schema_version",
    "target",
]

# Dynamic admitted-JSON subtrees classified at typed root only.
# EffectTargetV1 is typed (target_kind/target_ref); only admitted-JSON maps stay dynamic.
DYNAMIC_JSON_ROOTS = {
    "payload.preflight_readback",
    "payload.readback",
    "payload.observation.readback",
    "payload.receipt.preflight_readback",
    "payload.receipt.post_readback",
    "payload.original_receipt.preflight_readback",
    "payload.original_receipt.post_readback",
}

EFFECT_TARGET_FIELDS = [
    "target_kind",
    "target_ref",
]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def derive_path_set() -> list[tuple[str, str]]:
    """Return exhaustive (event_type, path) inventory from discriminated schema keys.

    Recurses every typed nested object (including EffectTargetV1 under all five
    embedded receipt/original_receipt targets). Stops only at genuine admitted-JSON
    dynamic roots listed in DYNAMIC_JSON_ROOTS.
    """
    out: list[tuple[str, str]] = []
    for et in EVENT_TYPES:
        for f in ENVELOPE_FIELDS:
            out.append((et, f))
        for k in PAYLOAD_KEYS[et]:
            out.append((et, f"payload.{k}"))
        # Embedded typed roots
        if et in {"observation_recorded", "compensation_observation", "receipt_terminal", "compensation_receipt"}:
            if "observation" in PAYLOAD_KEYS[et]:
                for f in OBSERVATION_BODY_FIELDS:
                    path = f"payload.observation.{f}"
                    out.append((et, path))
                    # observation.readback is dynamic admitted-JSON root — do not expand
        if et in {"receipt_terminal", "compensation_receipt", "compensation_intent", "compensation_observation"}:
            if "receipt" in PAYLOAD_KEYS[et]:
                for f in RECEIPT_BODY_FIELDS:
                    path = f"payload.receipt.{f}"
                    out.append((et, path))
                    if f == "target":
                        for tf in EFFECT_TARGET_FIELDS:
                            out.append((et, f"{path}.{tf}"))
            if "original_receipt" in PAYLOAD_KEYS[et]:
                for f in RECEIPT_BODY_FIELDS:
                    path = f"payload.original_receipt.{f}"
                    out.append((et, path))
                    if f == "target":
                        for tf in EFFECT_TARGET_FIELDS:
                            out.append((et, f"{path}.{tf}"))
    # stable unique
    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str]] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


NEW_INTRA_RELATIONS = [
    {
        "relation_id": "compensation_observation.original_receipt_effect_id_eq_payload",
        "event_type": "compensation_observation",
        "mutated_path": "payload.original_receipt.effect_id",
        "expected_reason_class": "payload_invalid",
        "scope": "intra_event",
        "note": "original_receipt.effect_id must equal payload.effect_id",
        "kind": "equality_or_digest_binding",
    },
    {
        "relation_id": "compensation_receipt.observation_fence_eq_payload",
        "event_type": "compensation_receipt",
        "mutated_path": "payload.observation.fence",
        "expected_reason_class": "payload_invalid",
        "scope": "intra_event",
        "note": "observation.fence must equal payload.fence",
        "kind": "equality_or_digest_binding",
    },
]


def _relation_path_index(relations: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    idx: dict[tuple[str, str], str] = {}
    for r in relations:
        et = r["event_type"]
        mp = r["mutated_path"]
        idx[(et, mp)] = r["relation_id"]
        # event_id / schema_version / envelope effect bindings already explicit
    return idx


def build_cross_event_table() -> list[dict[str, Any]]:
    """Import structured descriptors adjacent to Python replay validators.

    Generator must not declare standalone ID-only cross rows. Coverage and SSOT
    cross_event_relations are derived from effect_ledger_cross_relation_descriptors.
    """
    from workflow_kernel.effect_ledger_cross_relation_descriptors import (
        cross_event_relation_descriptors,
    )

    return cross_event_relation_descriptors()


def extract_cross_path_coverage(cross: list[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    """Mechanically map (event_type, path) -> relation_ids from structured bindings.

    Prefer exact source_bindings/destination_bindings (no claim|intent cross-product).
    Falls back to source_event/source_paths only when bindings absent.
    Envelope wildcards ('*') expand to all EVENT_TYPES for envelope-level paths.
    projected.status / canonical_event are not inventory paths.
    """
    cov: dict[tuple[str, str], set[str]] = {}

    def add(et: str, path: str, rid: str) -> None:
        if path in {"projected.status", "canonical_event"}:
            return
        cov.setdefault((et, path), set()).add(rid)

    def expand_events(spec: str) -> list[str]:
        parts = [p.strip() for p in spec.split("|") if p.strip()]
        out: list[str] = []
        for p in parts:
            if p == "*":
                out.extend(EVENT_TYPES)
            else:
                out.append(p)
        return out

    for rel in cross:
        rid = rel["relation_id"]
        src_bindings = rel.get("source_bindings")
        dst_bindings = rel.get("destination_bindings")
        if src_bindings or dst_bindings:
            for b in src_bindings or []:
                events = expand_events(str(b["event"]))
                for path in b.get("paths") or []:
                    for et in events:
                        add(et, path, rid)
            for b in dst_bindings or []:
                events = expand_events(str(b["event"]))
                for path in b.get("paths") or []:
                    for et in events:
                        add(et, path, rid)
            continue
        # Legacy fallback (should not be used once descriptors ship bindings)
        src_events = expand_events(rel["source_event"])
        dst_events = expand_events(rel["destination_event"])
        for path in rel.get("source_paths") or []:
            for et in src_events:
                add(et, path, rid)
        for path in rel.get("destination_paths") or []:
            for et in dst_events:
                add(et, path, rid)
    return cov


def classify_path(
    et: str,
    path: str,
    rel_by_mutated: dict[tuple[str, str], str],
    cross_ids: set[str],
    cross_coverage: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, Any]:
    """Classify each path exactly once.

    Cross-event whole-body / field relations use classification
    cross_event_replay_relation and point at explicit relation ID(s).
    Multi-relation participation uses relation_ids while the path appears once.
    Local rationales never imply replay/durable/cross-event binding.
    Prefer cross coverage over intra dual/mutated when a leaf participates in
    a replay-retained whole-body or digest relation.
    """
    path_id = f"{et}.{path.replace('.', '_')}"

    def local(rationale: str) -> dict[str, Any]:
        banned = (
            "cross-event",
            "cross event",
            "cross_event",
            "replay",
            "durable",
            "bind",
            "cross",
        )
        low = rationale.lower()
        for b in banned:
            if b in low:
                raise AssertionError(f"local rationale implies binding for {et}:{path}: {rationale}")
        return {
            "path_id": path_id,
            "event_type": et,
            "path": path,
            "classification": "structural_primitive_local",
            "rationale": rationale,
        }

    def intra(rid: str) -> dict[str, Any]:
        return {
            "path_id": path_id,
            "event_type": et,
            "path": path,
            "classification": "intra_event_relation",
            "relation_id": rid,
        }

    def cross(rids: str | list[str]) -> dict[str, Any]:
        ids = [rids] if isinstance(rids, str) else list(rids)
        for rid in ids:
            if rid not in cross_ids and not rid.startswith("cross_event."):
                raise AssertionError(f"unknown cross relation {rid} for {et}:{path}")
        out: dict[str, Any] = {
            "path_id": path_id,
            "event_type": et,
            "path": path,
            "classification": "cross_event_replay_relation",
            "relation_id": ids[0],
        }
        if len(ids) > 1:
            out["relation_ids"] = ids
        return out

    # Mechanical coverage from relation descriptors wins for retained leaves.
    if cross_coverage and (et, path) in cross_coverage:
        ids = sorted(cross_coverage[(et, path)])
        return cross(ids if len(ids) > 1 else ids[0])

    # Explicit cross_map for paths not fully expressed as descriptor leaves
    # (kept as a safety net; prefer descriptor coverage).
    cross_map: dict[tuple[str, str], str | list[str]] = {}

    # Envelope common (also covered by descriptors for most)
    if path == "schema_version":
        return intra(f"{et}.schema_version_fixed")
    if path == "event_id":
        return intra(f"{et}.event_id_derivation")
    if path == "payload.effect_id":
        return intra(f"{et}.envelope_effect_id_eq_payload_effect_id")
    if path == "effect_id":
        return cross("cross_event.effect_id_chain_mixed_effect")
    if path == "event_type":
        return cross("cross_event.state_event_type_transition")
    if path == "payload":
        return local("payload object root; children classified individually")
    if path == "sequence":
        return cross("cross_event.sequence_contiguity")
    if path == "previous_event_id":
        return cross("cross_event.previous_event_id_link")
    if path == "idempotency_key":
        return cross("cross_event.envelope_idempotency_replay_identity")
    if path == "occurred_at":
        return local("timestamp metadata; not order-compared by validators")

    # Intra-event relation by mutated_path direct hit (after cross coverage)
    if (et, path) in rel_by_mutated:
        return intra(rel_by_mutated[(et, path)])

    # Object roots
    if path == "payload.observation":
        return local("embedded observation object root; children classified via coverage or intra")
    if path == "payload.receipt":
        return local("embedded receipt object root; children classified via coverage or intra")
    if path == "payload.original_receipt":
        return local("embedded original_receipt object root; children classified via coverage or intra")

    # Residual cross_map removed: structured descriptor bindings are the sole
    # mechanical cross coverage source. Paths not in coverage fall through to
    # dual_hints / local_rationales / mutated-path intra / KeyError.

    # Intra dual path hints (only when not cross-covered)
    dual_hints: dict[tuple[str, str], str] = {
        ("observation_recorded", "payload.adapter_result_digest"): "observation_recorded.wrapper_adapter_result_digest_eq_body",
        ("receipt_terminal", "payload.receipt.effect_id"): "receipt_terminal.body_effect_id_eq_wrapper",
        ("receipt_terminal", "payload.receipt.fence"): "receipt_terminal.wrapper_fence_eq_body",
        ("receipt_terminal", "payload.receipt.command_digest"): "receipt_terminal.wrapper_command_digest_eq_body",
        ("receipt_terminal", "payload.receipt.adapter_id"): "receipt_terminal.wrapper_adapter_id_eq_body",
        ("receipt_terminal", "payload.receipt.operation"): "receipt_terminal.wrapper_operation_eq_body",
        ("receipt_terminal", "payload.receipt.payload_digest"): "receipt_terminal.wrapper_payload_digest_eq_body",
        ("receipt_terminal", "payload.receipt.idempotency_key"): "receipt_terminal.wrapper_idempotency_key_eq_body",
        ("receipt_terminal", "payload.receipt.authority_binding_digest"): "receipt_terminal.wrapper_authority_eq_body",
        ("receipt_terminal", "payload.receipt.result_classification"): "receipt_terminal.wrapper_classification_eq_body",
        ("receipt_terminal", "payload.receipt.receipt_digest"): "receipt_terminal.wrapper_receipt_digest_eq_body",
        ("receipt_terminal", "payload.observation_digest"): "receipt_terminal.observation_digest_eq_canonical",
        ("compensation_receipt", "payload.receipt.effect_id"): "compensation_receipt.body_effect_id_eq_wrapper",
        ("compensation_receipt", "payload.receipt.fence"): "compensation_receipt.wrapper_fence_eq_body",
        ("compensation_receipt", "payload.receipt.command_digest"): "compensation_receipt.wrapper_command_digest_eq_body",
        ("compensation_receipt", "payload.receipt.adapter_id"): "compensation_receipt.wrapper_adapter_id_eq_body",
        ("compensation_receipt", "payload.receipt.operation"): "compensation_receipt.wrapper_operation_eq_body",
        ("compensation_receipt", "payload.receipt.payload_digest"): "compensation_receipt.wrapper_payload_digest_eq_body",
        ("compensation_receipt", "payload.receipt.idempotency_key"): "compensation_receipt.wrapper_idempotency_key_eq_body",
        ("compensation_receipt", "payload.receipt.authority_binding_digest"): "compensation_receipt.wrapper_authority_eq_body",
        ("compensation_receipt", "payload.receipt.receipt_digest"): "compensation_receipt.wrapper_receipt_digest_eq_body",
        ("compensation_receipt", "payload.observation_digest"): "compensation_receipt.observation_digest_eq_canonical",
        ("compensation_receipt", "payload.original_receipt.effect_id"): "compensation_receipt.original_effect_id_eq_wrapper",
        ("compensation_observation", "payload.original_receipt.effect_id"): "compensation_observation.original_receipt_effect_id_eq_payload",
        ("compensation_intent", "payload.original_receipt.effect_id"): "compensation_intent.original_effect_id_eq_wrapper",
    }
    if (et, path) in dual_hints:
        rid = dual_hints[(et, path)]
        if rid.startswith("cross_event."):
            return cross(rid)
        return intra(rid)

    # Truly local non-bindable facts only (no replay/durable/bind/cross wording).
    local_rationales: dict[tuple[str, str], str] = {
        ("claimed", "payload.owner"): "claim-local owner text; no dual body",
        ("reconciled", "payload.evidence_digest"): "external reconcile evidence digest without embedded body",
        ("receipt_terminal", "payload.receipt.compensation_ref"): "body-local reference text",
        ("receipt_terminal", "payload.receipt.reconcile_ref"): "body-local reference text",
        ("receipt_terminal", "payload.receipt.schema_version"): "embedded schema tag fixed by receipt schema parse",
        ("compensation_receipt", "payload.receipt.reconcile_ref"): "body-local reference",
        ("compensation_receipt", "payload.receipt.schema_version"): "embedded schema tag",
    }
    if (et, path) in local_rationales:
        return local(local_rationales[(et, path)])

    # Fallback: try suffix match on mutated paths
    for (ret, rpath), rid in rel_by_mutated.items():
        if ret == et and rpath == path:
            return intra(rid)

    raise KeyError(f"unclassified path {et}:{path}")


def assert_path_relation_coverage(
    inv: list[dict[str, Any]],
    cross: list[dict[str, Any]],
    cross_coverage: dict[tuple[str, str], set[str]],
) -> None:
    """Assert retained leaves are cross; exact coverage equality; local clean."""
    from workflow_kernel.effect_ledger_cross_relation_descriptors import (
        FALSE_INTENT_MEMBERSHIPS_V1,
    )

    cross_ids = {c["relation_id"] for c in cross}
    by_path = {(r["event_type"], r["path"]): r for r in inv}

    # Reviewer examples must be cross with complete relation_ids
    required_cross = {
        ("intent_recorded", "payload.authority_binding_digest"),
        ("observation_recorded", "payload.observation.observation_id"),
        ("observation_recorded", "payload.observation.observed_at"),
        ("observation_recorded", "payload.observation.schema_version"),
        ("observation_recorded", "payload.observation.fence"),
        ("observation_recorded", "payload.observation.readback"),
        ("observation_recorded", "payload.observation.classification"),
        ("receipt_terminal", "payload.receipt.target.target_kind"),
        ("receipt_terminal", "payload.receipt.target.target_ref"),
        # 14 terminal receipt whole-body leaves retained and later consumed
        ("receipt_terminal", "payload.receipt"),
        ("receipt_terminal", "payload.receipt.adapter_id"),
        ("receipt_terminal", "payload.receipt.authority_binding_digest"),
        ("receipt_terminal", "payload.receipt.command_digest"),
        ("receipt_terminal", "payload.receipt.compensation_ref"),
        ("receipt_terminal", "payload.receipt.effect_id"),
        ("receipt_terminal", "payload.receipt.fence"),
        ("receipt_terminal", "payload.receipt.idempotency_key"),
        ("receipt_terminal", "payload.receipt.operation"),
        ("receipt_terminal", "payload.receipt.payload_digest"),
        ("receipt_terminal", "payload.receipt.receipt_digest"),
        ("receipt_terminal", "payload.receipt.reconcile_ref"),
        ("receipt_terminal", "payload.receipt.result_classification"),
        ("receipt_terminal", "payload.receipt.schema_version"),
        ("receipt_terminal", "payload.receipt_digest"),
    }
    for key in required_cross:
        row = by_path[key]
        assert row["classification"] == "cross_event_replay_relation", key
        ids = set(row.get("relation_ids") or [row["relation_id"]])
        assert ids <= cross_ids and ids, key

    # Nine original-receipt effect_id/operation/result_classification rows must be cross
    for et in ("compensation_intent", "compensation_observation", "compensation_receipt"):
        for leaf in (
            "payload.original_receipt.effect_id",
            "payload.original_receipt.operation",
            "payload.original_receipt.result_classification",
        ):
            row = by_path[(et, leaf)]
            assert row["classification"] == "cross_event_replay_relation", (et, leaf, row)
            ids = set(row.get("relation_ids") or [row["relation_id"]])
            assert ids & {
                "cross_event.compensation_original_receipt_bind_observation",
                "cross_event.compensation_original_receipt_bind_receipt",
                "cross_event.compensation_requires_applied_terminal_state",
            }, (et, leaf, ids)

    # Compensation terminal source paths must not invent receipt_terminal.payload.original_receipt
    for c in cross:
        if c["relation_id"] in {
            "cross_event.compensation_requires_applied_terminal_state",
            "cross_event.compensation_original_receipt_bind_observation",
            "cross_event.compensation_original_receipt_bind_receipt",
        }:
            for b in c.get("source_bindings") or []:
                if b.get("event") == "receipt_terminal":
                    for p in b.get("paths") or []:
                        assert not str(p).startswith("payload.original_receipt"), (
                            c["relation_id"],
                            p,
                        )
                        if p not in {"projected.status"}:
                            assert str(p).startswith("payload.receipt"), (c["relation_id"], p)

    # False-positive: compensation request/authority must NOT claim claim_to_compensation_facts
    for et in ("compensation_intent", "compensation_observation", "compensation_receipt"):
        for path in ("payload.request_digest", "payload.authority_binding_digest"):
            row = by_path[(et, path)]
            assert row["classification"] == "cross_event_replay_relation", (et, path)
            ids = set(row.get("relation_ids") or [row["relation_id"]])
            assert "cross_event.claim_to_compensation_facts" not in ids, (et, path, ids)
            assert ids & {
                "cross_event.compensation_intent_local_facts_to_observation",
                "cross_event.compensation_intent_local_facts_to_receipt",
            }, (et, path, ids)

    # Claim request_digest must not list claim_to_compensation_facts
    claim_req = by_path[("claimed", "payload.request_digest")]
    ids = set(claim_req.get("relation_ids") or [claim_req["relation_id"]])
    assert "cross_event.claim_to_compensation_facts" not in ids

    # 32 hardcoded negative false memberships (intent claim-copy × claim-fact relations)
    assert len(FALSE_INTENT_MEMBERSHIPS_V1) == 32
    for et, path, rid in FALSE_INTENT_MEMBERSHIPS_V1:
        row = by_path[(et, path)]
        ids = set(row.get("relation_ids") or [row.get("relation_id")])
        assert rid not in ids, f"false membership {et}:{path} -> {rid} in {ids}"

    # Intent authority must keep the later authority-consuming relations
    auth = by_path[("intent_recorded", "payload.authority_binding_digest")]
    auth_ids = set(auth.get("relation_ids") or [auth["relation_id"]])
    for rid in (
        "cross_event.claim_to_observation_authority",
        "cross_event.claim_to_reconcile_facts",
        "cross_event.claim_to_receipt_authority",
    ):
        assert rid in auth_ids, auth_ids

    # Exact equality: classified relation_ids == mechanical descriptor coverage
    for row in inv:
        if row["classification"] != "cross_event_replay_relation":
            continue
        ids = set(row.get("relation_ids") or [row["relation_id"]])
        assert ids <= cross_ids, (row["event_type"], row["path"], ids - cross_ids)
        cov = cross_coverage.get((row["event_type"], row["path"]))
        assert cov is not None, (
            f"cross path missing mechanical coverage: {row['event_type']}:{row['path']}"
        )
        assert ids == cov, (
            row["event_type"],
            row["path"],
            "classified",
            sorted(ids),
            "coverage",
            sorted(cov),
        )

    # Reverse coverage hard gate: every descriptor source/destination path (except
    # closed non-inventory envelope allowlist) must exist in the exact schema path set.
    from workflow_kernel.effect_ledger_cross_relation_descriptors import (
        NON_INVENTORY_ENVELOPE_PATH_ALLOWLIST_V1,
    )

    inv_path_set = {(r["event_type"], r["path"]) for r in inv}
    schema_paths_by_event: dict[str, set[str]] = {}
    for et, path in inv_path_set:
        schema_paths_by_event.setdefault(et, set()).add(path)

    def _expand_events(spec: str) -> list[str]:
        parts = [p.strip() for p in str(spec).split("|") if p.strip()]
        out: list[str] = []
        for p in parts:
            if p == "*":
                out.extend(EVENT_TYPES)
            else:
                out.append(p)
        return out

    descriptor_only: list[tuple[str, str, str]] = []
    for rel in cross:
        rid = str(rel["relation_id"])
        for b in list(rel.get("source_bindings") or []) + list(rel.get("destination_bindings") or []):
            events = _expand_events(str(b.get("event")))
            for path in b.get("paths") or []:
                p = str(path)
                if p in NON_INVENTORY_ENVELOPE_PATH_ALLOWLIST_V1:
                    continue
                for et in events:
                    if (et, p) not in inv_path_set:
                        descriptor_only.append((rid, et, p))
    assert not descriptor_only, (
        "descriptor coverage contains nonexistent schema paths "
        f"(coverage − inventory must be empty): {descriptor_only[:20]}"
    )

    # Explicit adversarial probes: former nine reconciled.observation paths + synthetic child.
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
        assert ("reconciled", bad_path) not in inv_path_set
        assert ("reconciled", bad_path) not in cross_coverage
        assert bad_path not in schema_paths_by_event.get("reconciled", set())

    # Wildcard may not hide invalid event-specific paths: '*' expansions must all exist
    # unless path is on the closed non-inventory allowlist.
    for rel in cross:
        for b in list(rel.get("source_bindings") or []) + list(rel.get("destination_bindings") or []):
            if str(b.get("event")) != "*":
                continue
            for path in b.get("paths") or []:
                p = str(path)
                if p in NON_INVENTORY_ENVELOPE_PATH_ALLOWLIST_V1:
                    continue
                missing_ets = [et for et in EVENT_TYPES if (et, p) not in inv_path_set]
                assert not missing_ets, (
                    f"wildcard path {p!r} missing from schema for events {missing_ets} "
                    f"in {rel['relation_id']}"
                )

    # Local rationales clean
    banned = ("replay", "durable", "bind", "cross")
    for row in inv:
        if row["classification"] != "structural_primitive_local":
            continue
        low = str(row.get("rationale") or "").lower()
        for b in banned:
            if b in low:
                raise AssertionError(
                    f"local rationale banned token {b!r} at {row['event_type']}:{row['path']}: {row.get('rationale')}"
                )


def assert_adversarial_false_relation_id_rejected(
    inv: list[dict[str, Any]],
    cross: list[dict[str, Any]],
    cross_coverage: dict[tuple[str, str], set[str]],
) -> None:
    """One correct + one false relation ID must fail exact coverage equality."""
    poisoned = [dict(r) for r in inv]
    # Find a real cross path with a single correct relation id
    target = None
    for row in poisoned:
        if row["classification"] != "cross_event_replay_relation":
            continue
        ids = list(row.get("relation_ids") or [row["relation_id"]])
        if len(ids) >= 1:
            target = row
            break
    assert target is not None
    false_id = "cross_event.__adversarial_false_relation__"
    good = list(target.get("relation_ids") or [target["relation_id"]])
    target["relation_ids"] = sorted(set(good) | {false_id})
    target["relation_id"] = target["relation_ids"][0]
    try:
        assert_path_relation_coverage(poisoned, cross, cross_coverage)
    except AssertionError:
        return
    raise AssertionError("adversarial false relation id did not fail coverage equality")


def build_path_inventory(relations: list[dict[str, Any]], cross: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rel_by_mutated = _relation_path_index(relations)
    cross_ids = {c["relation_id"] for c in cross}
    cross_coverage = extract_cross_path_coverage(cross)
    inv = []
    for et, path in derive_path_set():
        inv.append(classify_path(et, path, rel_by_mutated, cross_ids, cross_coverage))
    # exact once
    keys = [(x["event_type"], x["path"]) for x in inv]
    assert len(keys) == len(set(keys)), "duplicate path inventory entries"
    assert_path_relation_coverage(inv, cross, cross_coverage)
    assert_adversarial_false_relation_id_rejected(inv, cross, cross_coverage)
    return inv


def build_primitives_from_inventory(inv: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in inv:
        if row["classification"] != "structural_primitive_local":
            continue
        # Skip pure structural envelope roots that are not "opaque facts"
        if row["path"] in {"payload", "event_type", "effect_id", "occurred_at"}:
            # keep them in inventory only; primitives_opaque is for non-bindable facts
            if row["path"] in {"payload", "event_type"}:
                continue
        out.append(
            {
                "field_id": row["path_id"],
                "event_type": row["event_type"],
                "path": row["path"],
                "kind": "primitive_opaque",
                "scope": "intra_event",
                "rationale": row.get("rationale") or "local non-bindable fact",
            }
        )
    return out


def _set_path(obj: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur: Any = obj
    for p in parts[:-1]:
        if not isinstance(cur[p], dict):
            cur[p] = dict(cur[p])
        else:
            cur[p] = dict(cur[p])
        cur = cur[p]
    cur[parts[-1]] = value


def _get_path(obj: dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for p in dotted.split("."):
        cur = cur[p]
    return cur


def recompute_event(event: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from workflow_kernel import effect_contracts as ec

    body = {k: v for k, v in event.items() if k != "event_id"}
    # Recompute nested digests that are self-hashes when bodies changed
    payload = body.get("payload")
    if isinstance(payload, dict):
        payload = copy.deepcopy(payload)
        # observation digest
        if isinstance(payload.get("observation"), dict) and "observation_digest" in payload:
            payload["observation_digest"] = ec.canonical_digest_v1(payload["observation"])
        if isinstance(payload.get("readback"), dict) and "readback_digest" in payload:
            payload["readback_digest"] = ec.canonical_digest_v1(payload["readback"])
        if isinstance(payload.get("preflight_readback"), dict) and "preflight_readback_digest" in payload:
            payload["preflight_readback_digest"] = ec.canonical_digest_v1(payload["preflight_readback"])
        # receipt self digest
        if isinstance(payload.get("receipt"), dict):
            rc = dict(payload["receipt"])
            if "receipt_digest" in rc:
                tmp = {k: v for k, v in rc.items() if k != "receipt_digest"}
                rc["receipt_digest"] = ec.canonical_digest_v1(tmp)
                payload["receipt"] = rc
                if "receipt_digest" in payload:
                    payload["receipt_digest"] = rc["receipt_digest"]
        if isinstance(payload.get("original_receipt"), dict):
            rc = dict(payload["original_receipt"])
            if "receipt_digest" in rc:
                tmp = {k: v for k, v in rc.items() if k != "receipt_digest"}
                rc["receipt_digest"] = ec.canonical_digest_v1(tmp)
                payload["original_receipt"] = rc
                if "original_receipt_digest" in payload:
                    payload["original_receipt_digest"] = rc["receipt_digest"]
        body["payload"] = payload
    eid = ec.derive_workflow_effect_ledger_event_id_v1(body)
    out = dict(body)
    out["event_id"] = eid
    return out


def fix_and_extend_corpus(corpus: dict[str, Any], relations: list[dict[str, Any]]) -> dict[str, Any]:
    """Ordinary path: pure identity validator over governed corpus rows.

    Never mutates existing governed values/paths/reasons/declarations/IDs/ordering/metadata.
    Migration of corpus content is closed on the ordinary --write/--check path.
    """
    cases = list(corpus["cases"])
    by_id = {c["id"]: c for c in cases}
    rel_ids = sorted(r["relation_id"] for r in relations)

    # Every relation must already have accept+reject twins; no bootstrap/migration.
    for rid in rel_ids:
        acc_id = None
        rej_id = None
        # Find by relation_id field when present
        acc = next((c for c in cases if c.get("relation_id") == rid and c.get("expect") == "accept"), None)
        rej = next((c for c in cases if c.get("relation_id") == rid and c.get("expect") == "reject"), None)
        if acc is None or rej is None:
            raise SystemExit(
                f"missing governed corpus twins for relation {rid} "
                f"(migration-only bootstrap unavailable on ordinary --write/--check)"
            )
        # Identity: do not rewrite value/path/reason/bookkeeping.
        if "value" not in acc or "value" not in rej:
            raise SystemExit(f"governed twin missing value for {rid}")

    # Special classification reject must remain as checked-in; validate presence only.
    rej_id = "rel_compensation_receipt.observation_classification_eq_receipt_reject"
    acc_id = "rel_compensation_receipt.observation_classification_eq_receipt_accept"
    if rej_id not in by_id or acc_id not in by_id:
        raise SystemExit(f"missing governed special classification rows {acc_id}/{rej_id}")

    # Return corpus unchanged (identity). Ordering/metadata must already be correct.
    meta = dict(corpus.get("semantic_relation_matrix") or {})
    if int(meta.get("relation_count", -1)) != len(relations):
        raise SystemExit(
            f"corpus metadata relation_count drift {meta.get('relation_count')} != {len(relations)} "
            "(ordinary generator does not rewrite metadata)"
        )
    if int(meta.get("cases_added", -1)) != len(relations) * 2:
        raise SystemExit(
            f"corpus metadata cases_added drift {meta.get('cases_added')} != {len(relations)*2} "
            "(ordinary generator does not rewrite metadata)"
        )
    return corpus



def compute_parity_rows(corpus: dict[str, Any], relations: list[dict[str, Any]]) -> dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from workflow_kernel import effect_contracts as ec
    from workflow_kernel.workflow_effect_ledger import _map_effect_contract_error
    from tests.workflow_kernel.test_effect_contracts import materialize_shared_corpus_value

    rel_ids = sorted(r["relation_id"] for r in relations)
    matrix = [c for c in corpus["cases"] if str(c["id"]).startswith("rel_")]
    by_rel: dict[str, dict[str, dict]] = {}
    for case in matrix:
        by_rel.setdefault(case["relation_id"], {})[case["expect"]] = case
    if sorted(by_rel) != rel_ids:
        missing = sorted(set(rel_ids) - set(by_rel))
        extra = sorted(set(by_rel) - set(rel_ids))
        raise SystemExit(f"relation coverage mismatch missing={missing} extra={extra}")

    python_rows = []
    for rid in rel_ids:
        acc = by_rel[rid]["accept"]
        rej = by_rel[rid]["reject"]
        parsed = ec.parse_workflow_effect_ledger_event_v1(materialize_shared_corpus_value(acc["value"]))
        assert parsed.event_id
        try:
            ec.parse_workflow_effect_ledger_event_v1(materialize_shared_corpus_value(rej["value"]))
            raise SystemExit(f"reject accepted: {rid}")
        except ec.EffectContractError as exc:
            reason = _map_effect_contract_error(exc)
        expected = rej.get("expected_reason_class") or "payload_invalid"
        if reason != expected:
            raise SystemExit(f"reason mismatch {rid}: {reason} != {expected}")
        python_rows.append(
            {
                "event_type": rej["event_type"],
                "relation_id": rid,
                "expected": expected,
                "python_actual": reason,
                "ts_actual": None,  # filled by TS generator companion / tests require non-null after both
            }
        )
    # mismatch_count recomputed (python self-consistency)
    mismatch_count = sum(1 for r in python_rows if r["python_actual"] != r["expected"])
    return {
        "schema_version": "semantic_relation_parity_v1",
        "relation_count": len(rel_ids),
        "mismatch_count": mismatch_count,
        "python_rows": python_rows,
        "rows": python_rows,  # alias for dual consumers
        "generator": "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py",
    }


def build_ssot(old: dict[str, Any]) -> dict[str, Any]:
    from workflow_kernel.effect_ledger_relation_manifests import (
        CROSS_EVENT_RELATION_MANIFEST_V1,
        INTRA_EVENT_RELATION_MANIFEST_V1,
    )

    relations = list(old["relations"])
    existing = {r["relation_id"] for r in relations}
    for nr in NEW_INTRA_RELATIONS:
        if nr["relation_id"] not in existing:
            relations.append(nr)
    # stable sort by event order then id
    et_order = {e: i for i, e in enumerate(EVENT_TYPES)}
    relations.sort(key=lambda r: (et_order.get(r["event_type"], 99), r["relation_id"]))
    # Manifest gate: SSOT intra set must exact-equal independently maintained code manifest.
    rel_ids = {r["relation_id"] for r in relations}
    if rel_ids != set(INTRA_EVENT_RELATION_MANIFEST_V1):
        missing = sorted(set(INTRA_EVENT_RELATION_MANIFEST_V1) - rel_ids)
        extra = sorted(rel_ids - set(INTRA_EVENT_RELATION_MANIFEST_V1))
        raise SystemExit(f"intra manifest drift missing={missing} extra={extra}")

    cross = build_cross_event_table()
    cross_ids = {c["relation_id"] for c in cross}
    if cross_ids != set(CROSS_EVENT_RELATION_MANIFEST_V1):
        missing = sorted(set(CROSS_EVENT_RELATION_MANIFEST_V1) - cross_ids)
        extra = sorted(cross_ids - set(CROSS_EVENT_RELATION_MANIFEST_V1))
        raise SystemExit(f"cross manifest drift missing={missing} extra={extra}")

    inv = build_path_inventory(relations, cross)
    primitives = build_primitives_from_inventory(inv)
    notes = [
        "Envelope idempotency_key is ledger event-replay identity and intentionally distinct from payload.idempotency_key (command key).",
        "path_inventory is mechanically derived from envelope required fields + discriminated payload exact keys + embedded observation/receipt roots + EffectTargetV1 children; dynamic admitted-JSON subtrees are classified at typed roots only.",
        "Each path is classified exactly once as structural_primitive_local, intra_event_relation, or cross_event_replay_relation.",
        "When a path participates in multiple cross relations, relation_ids lists all IDs while the path appears once.",
        "primitive_opaque contains only truly non-bindable local facts with exact rationale; replay-bound facts live in cross_event_relations.",
        "Public Python parse_workflow_effect_ledger_event_v1 and TS parseWorkflowEffectLedgerEventV1 enforce every intra-event relation.",
        "Corpus reject twins mutate exactly one semantic path; bookkeeping_paths lists recomputed digest/event_id fields; mechanical twin diff equals {mutated_path} ∪ bookkeeping_paths.",
        "Relation IDs exact-cover enforceable intra table via code manifests; cross-event relation IDs exact-cover named parameterized replay tests.",
        "Path-to-relation coverage is mechanically extracted from cross relation source_paths/destination_paths; retained-and-later-used leaves classify cross_event_replay_relation.",
        "claim_to_compensation_facts excludes compensation request_digest and authority_binding_digest (validator deliberately does not compare them to original claim); those use compensation_intent_local_facts_to_observation/receipt.",
        "Structured cross relation descriptors live in workflow_kernel/effect_ledger_cross_relation_descriptors.py adjacent to Python replay validators; generator imports them. Python manifest IDs derive from those descriptors. TS cross IDs are projection-only inventory (parser/intra only; no TS replay enforcement).",
    ]
    return {
        "schema_version": "workflow_effect_ledger_semantic_relations_v1",
        "scope": "intra_event_public_parse_plus_cross_event_replay_inventory",
        "event_types": EVENT_TYPES,
        "expected_reason_class": "payload_invalid",
        "envelope_fields": ENVELOPE_FIELDS,
        "relations": relations,
        "path_inventory": inv,
        "primitives_opaque": primitives,
        "cross_event_relations": cross,
        "notes": notes,
    }


def validate_explicit_bookkeeping_paths(corpus: dict[str, Any], *, mode: str) -> list[str]:
    """Validate checked-in bookkeeping declarations; never expand them.

    mode is 'write' or 'check'. Residual undeclared paths are always errors.
    Generator may validate but must never auto-add residual diffs into bookkeeping_paths.
    """
    errors: list[str] = []
    cases = list(corpus["cases"])
    matrix = [c for c in cases if str(c["id"]).startswith("rel_")]
    by_rel: dict[str, dict[str, dict]] = {}
    for c in matrix:
        by_rel.setdefault(c["relation_id"], {})[c["expect"]] = c
    for rid, pair in by_rel.items():
        acc = pair.get("accept")
        rej = pair.get("reject")
        if not acc or not rej:
            continue
        mutated = rej.get("mutated_path")
        if not mutated:
            errors.append(f"{mode}: reject missing mutated_path {rid}")
            continue
        if "bookkeeping_paths" not in rej:
            errors.append(f"{mode}: reject missing explicit bookkeeping_paths {rid}")
            continue
        bk = list(rej.get("bookkeeping_paths") or [])
        # Explicit declarations must be immutable tuples/lists of strings only.
        if any(not isinstance(p, str) for p in bk):
            errors.append(f"{mode}: non-string bookkeeping path {rid}")
            continue
        diffs = _semantic_diff_paths(acc["value"], rej["value"])
        collapsed: list[str] = []
        for p in diffs:
            if p == mutated or p.startswith(str(mutated) + "."):
                if mutated not in collapsed:
                    collapsed.append(mutated)
            else:
                collapsed.append(p)
        expected_set = {str(mutated), *bk}
        actual_set = set(collapsed)
        residual = sorted(actual_set - expected_set)
        missing = sorted(expected_set - actual_set)
        if residual:
            errors.append(
                f"{mode}: residual undeclared bookkeeping paths for {rid}: {residual} "
                f"(collapsed={sorted(actual_set)} declared={sorted(expected_set)})"
            )
        if missing:
            errors.append(
                f"{mode}: declared bookkeeping paths absent from diff for {rid}: {missing}"
            )
    return errors


def annotate_bookkeeping_paths(corpus: dict[str, Any]) -> dict[str, Any]:
    """Hard-fail write planning when bookkeeping declarations are missing/wrong.

    Never initialize, autofill, or expand bookkeeping_paths. Missing key and
    residual undeclared diffs are hard errors in write mode exactly as in check.
    """
    errors = validate_explicit_bookkeeping_paths(corpus, mode="write")
    if errors:
        raise SystemExit("bookkeeping declaration errors:\n" + "\n".join(errors))
    return corpus


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


def _canonical_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _ssot_comparable(ssot: dict[str, Any]) -> dict[str, Any]:
    """Normalize SSOT for full-row comparison (classifications/relation IDs/rationales)."""
    inv = []
    for row in ssot.get("path_inventory", []):
        item = {
            "event_type": row["event_type"],
            "path": row["path"],
            "classification": row["classification"],
            "path_id": row.get("path_id"),
        }
        if "relation_id" in row:
            item["relation_id"] = row["relation_id"]
        if "relation_ids" in row:
            item["relation_ids"] = list(row["relation_ids"])
        if "rationale" in row:
            item["rationale"] = row["rationale"]
        inv.append(item)
    inv.sort(key=lambda r: (r["event_type"], r["path"]))
    rels = sorted(ssot.get("relations", []), key=lambda r: r["relation_id"])
    cross = sorted(ssot.get("cross_event_relations", []), key=lambda r: r["relation_id"])
    prim = sorted(ssot.get("primitives_opaque", []), key=lambda r: (r["event_type"], r["path"]))
    return {
        "relations": rels,
        "path_inventory": inv,
        "cross_event_relations": cross,
        "primitives_opaque": prim,
        "event_types": ssot.get("event_types"),
        "envelope_fields": ssot.get("envelope_fields"),
    }




def _case_canonical_digest(case: dict[str, Any]) -> str:
    import hashlib

    payload = json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _ordered_id_list_digest(ids: list[str]) -> str:
    import hashlib

    return hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()


def validate_canonical_corpus_manifest(corpus: dict[str, Any], *, raw_bytes: bytes, mode: str) -> list[str]:
    """Validate live corpus against immutable checked-in canonical manifest.

    Runs BEFORE desired computation. Catches row swap, baseline observation ID
    change, and twin timestamp+event_id coherent drift.
    """
    import hashlib

    errors: list[str] = []
    if not CORPUS_CANONICAL_MANIFEST_PATH.is_file():
        return [f"canonical corpus manifest missing: {CORPUS_CANONICAL_MANIFEST_PATH}"]
    manifest_file_raw = CORPUS_CANONICAL_MANIFEST_PATH.read_bytes()
    manifest_file_sha = hashlib.sha256(manifest_file_raw).hexdigest()
    if manifest_file_sha != CANONICAL_CORPUS_MANIFEST_RAW_SHA256_V1:
        return [
            f"canonical-manifest raw sha mismatch (file raw sha256) mode={mode} "
            f"live={manifest_file_sha} pinned={CANONICAL_CORPUS_MANIFEST_RAW_SHA256_V1}"
        ]
    try:
        manifest = json.loads(CORPUS_CANONICAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"canonical corpus manifest unreadable: {exc}"]

    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    can_sha = hashlib.sha256(_canonical_json(corpus).encode()).hexdigest()
    if raw_sha != manifest.get("corpus_raw_sha256"):
        errors.append(
            f"canonical-manifest corpus raw sha256 mismatch mode={mode} "
            f"live={raw_sha} manifest={manifest.get('corpus_raw_sha256')}"
        )
    if can_sha != manifest.get("corpus_canonical_sha256"):
        errors.append(
            f"canonical-manifest corpus canonical sha256 mismatch mode={mode} "
            f"live={can_sha} manifest={manifest.get('corpus_canonical_sha256')}"
        )

    cases = list(corpus.get("cases") or [])
    ids = [str(c.get("id")) for c in cases]
    exp_ids = list(manifest.get("ordered_case_ids") or [])
    if ids != exp_ids:
        errors.append(
            f"canonical-manifest ordered case id list mismatch mode={mode} "
            f"live_count={len(ids)} manifest_count={len(exp_ids)}"
        )
    id_digest = _ordered_id_list_digest(ids)
    if id_digest != manifest.get("ordered_case_id_list_digest_sha256"):
        errors.append(
            f"canonical-manifest ordered case id list digest mismatch mode={mode} "
            f"live={id_digest} manifest={manifest.get('ordered_case_id_list_digest_sha256')}"
        )

    per = manifest.get("per_case_canonical_digest_sha256") or {}
    for case in cases:
        cid = str(case.get("id"))
        got = _case_canonical_digest(case)
        exp = per.get(cid)
        if exp is None:
            errors.append(f"canonical-manifest missing per-case digest for id={cid}")
        elif got != exp:
            errors.append(
                f"canonical-manifest per-case digest mismatch id={cid} live={got} manifest={exp}"
            )
    live_set = set(ids)
    for cid in per:
        if cid not in live_set:
            errors.append(f"canonical-manifest extra per-case digest id={cid}")

    counts = manifest.get("expected_counts") or {}
    led = [c for c in cases if c.get("kind") == "workflow_effect_ledger_event_v1"]
    live_counts = {
        "total_cases": len(cases),
        "total_accept": sum(1 for c in cases if c.get("expect") == "accept"),
        "total_reject": sum(1 for c in cases if c.get("expect") == "reject"),
        "ledger_cases": len(led),
        "ledger_accept": sum(1 for c in led if c.get("expect") == "accept"),
        "ledger_reject": sum(1 for c in led if c.get("expect") == "reject"),
    }
    for k, exp in counts.items():
        if live_counts.get(k) != exp:
            errors.append(
                f"canonical-manifest count mismatch {k} live={live_counts.get(k)} manifest={exp}"
            )

    # Baseline provenance: exact baseline manifest raw SHA, archived count/ID subset,
    # and live contains all baseline IDs unchanged.
    prov = manifest.get("provenance") or {}
    baseline_rel = prov.get("pre_4a5b_baseline_manifest")
    baseline_exp = prov.get("pre_4a5b_baseline_manifest_sha256")
    if baseline_exp != BASELINE_MANIFEST_RAW_SHA256_V1:
        errors.append(
            f"canonical-manifest baseline sha pin mismatch mode={mode} "
            f"manifest={baseline_exp} pinned={BASELINE_MANIFEST_RAW_SHA256_V1}"
        )
    if not baseline_rel:
        errors.append(f"canonical-manifest missing baseline path mode={mode}")
    else:
        baseline_path = REPO / str(baseline_rel)
        if not baseline_path.is_file():
            errors.append(f"canonical-manifest baseline archive missing: {baseline_path}")
        else:
            b_raw = baseline_path.read_bytes()
            b_sha = hashlib.sha256(b_raw).hexdigest()
            if b_sha != BASELINE_MANIFEST_RAW_SHA256_V1:
                errors.append(
                    f"canonical-manifest baseline archive raw sha mismatch mode={mode} "
                    f"live={b_sha} pinned={BASELINE_MANIFEST_RAW_SHA256_V1}"
                )
            try:
                baseline = json.loads(b_raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"canonical-manifest baseline unreadable: {exc}")
                baseline = {}
            b_ids = [str(i) for i in list(baseline.get("case_ids") or [])]
            live_ids = [str(c.get("id")) for c in cases]
            live_set = set(live_ids)
            missing = [i for i in b_ids if i not in live_set]
            if missing:
                errors.append(
                    f"canonical-manifest baseline IDs missing from live mode={mode} "
                    f"count={len(missing)} sample={missing[:5]}"
                )
            if baseline.get("case_count") is not None and int(baseline["case_count"]) != len(b_ids):
                errors.append(
                    f"canonical-manifest baseline count mismatch mode={mode} "
                    f"case_count={baseline.get('case_count')} ids={len(b_ids)}"
                )
    return errors


def _detect_incomplete_write_journal() -> list[str]:
    journal = REPO / TRANSACTION_JOURNAL_NAME
    if journal.exists():
        return [
            f"incomplete write journal present at {journal}; "
            "ordinary mode refuses to guess recovery — clear only after deterministic restore"
        ]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not args.write and not args.check:
        args.check = True

    journal_errors = _detect_incomplete_write_journal()
    if journal_errors:
        print("CHECK/WRITE FAILED (incomplete transaction journal):")
        for e in journal_errors:
            print(" -", e)
        return 1

    old_ssot = _load_json(SSOT_PATH)
    corpus_raw = CORPUS_PATH.read_bytes()
    old_corpus = json.loads(corpus_raw.decode("utf-8"))
    pre_mode = "write" if args.write else "check"
    # 4A.5B.13: bookkeeping-precheck BEFORE manifest so missing bookkeeping is not
    # masked by later corpus/manifest digest gates when manifests are present.
    pre_errors = validate_explicit_bookkeeping_paths(old_corpus, mode=pre_mode)
    if pre_errors:
        print("BOOKKEEPING PRECHECK FAILED (bookkeeping-precheck before regeneration):")
        for e in pre_errors:
            print(" -", e)
        return 1
    # Immutable canonical corpus manifest gate BEFORE desired computation.
    manifest_errors = validate_canonical_corpus_manifest(old_corpus, raw_bytes=corpus_raw, mode=pre_mode)
    if manifest_errors:
        print("CANONICAL CORPUS MANIFEST FAILED (before desired computation; no artifacts written):")
        for e in manifest_errors:
            print(" -", e)
        return 1
    new_ssot = build_ssot(old_ssot)
    # For corpus, always compute desired state from current corpus + relations
    desired_corpus = fix_and_extend_corpus(copy.deepcopy(old_corpus), new_ssot["relations"])
    desired_corpus = annotate_bookkeeping_paths(desired_corpus)
    desired_parity = compute_parity_rows(desired_corpus, new_ssot["relations"])

    if args.write:
        # Ordinary --write in this correction slice: refuse ANY drift and perform
        # no publication at all. Corpus is immutable input; SSOT/parity publication
        # is closed unless already matching (vacuous zero writes).
        write_errors: list[str] = []
        write_errors.extend(
            validate_canonical_corpus_manifest(old_corpus, raw_bytes=corpus_raw, mode="write")
        )
        if _canonical_json(old_corpus) != _canonical_json(desired_corpus):
            write_errors.append(
                "ordinary --write refused: desired corpus != current corpus "
                "(governed row migration is closed; corpus is immutable input)"
            )
        if _canonical_json(_ssot_comparable(old_ssot)) != _canonical_json(_ssot_comparable(new_ssot)):
            write_errors.append(
                "ordinary --write refused: SSOT drift vs rebuilt (publication closed in ordinary mode)"
            )
        cur_parity = _load_json(PARITY_PATH) if PARITY_PATH.exists() else None
        if cur_parity is None:
            write_errors.append("ordinary --write refused: missing parity artifact")
        else:
            # Compare python-facing parity fields; full object may include TS columns.
            exp_by = {r["relation_id"]: r for r in desired_parity.get("python_rows") or []}
            cur_rows = cur_parity.get("python_rows") or cur_parity.get("rows") or []
            cur_by = {r.get("relation_id"): r for r in cur_rows}
            if set(cur_by) != set(exp_by):
                write_errors.append(
                    "ordinary --write refused: parity relation id set drift (publication closed)"
                )
            else:
                for rid, er in exp_by.items():
                    cr = cur_by[rid]
                    if cr.get("python_actual") != er.get("python_actual") or cr.get("expected") != er.get("expected"):
                        write_errors.append(
                            f"ordinary --write refused: parity python_actual drift {rid} (publication closed)"
                        )
                        break
            if int(cur_parity.get("relation_count", -1)) != int(desired_parity.get("relation_count", -2)):
                write_errors.append("ordinary --write refused: parity relation_count drift (publication closed)")
        write_errors.extend(validate_explicit_bookkeeping_paths(desired_corpus, mode="write-plan"))
        if write_errors:
            print("WRITE VALIDATION FAILED (no artifacts written):")
            for e in write_errors:
                print(" -", e)
            return 1
        # Zero-write success: everything already matches; do not touch corpus/SSOT/parity/TS.
        print(
            json.dumps(
                {
                    "wrote": False,
                    "publication": "vacuous_zero_write",
                    "relations": len(new_ssot["relations"]),
                    "path_inventory": len(new_ssot["path_inventory"]),
                    "primitives_opaque": len(new_ssot["primitives_opaque"]),
                    "cross_event_relations": len(new_ssot["cross_event_relations"]),
                    "corpus_cases": len(desired_corpus["cases"]),
                    "parity_relation_count": desired_parity["relation_count"],
                    "parity_mismatch_count": desired_parity["mismatch_count"],
                },
                indent=2,
            )
        )
        return 0

    # --check: recompute independently; no writes. Full-row / full-bytes comparisons.
    cur_ssot = old_ssot
    cur_corpus = old_corpus
    cur_parity = _load_json(PARITY_PATH) if PARITY_PATH.exists() else None
    errors: list[str] = []

    # Independent code manifests (not derived from current SSOT alone)
    from workflow_kernel.effect_ledger_relation_manifests import (
        CROSS_EVENT_RELATION_MANIFEST_V1,
        INTRA_EVENT_RELATION_MANIFEST_V1,
    )

    cur_rel = {r["relation_id"] for r in cur_ssot.get("relations", [])}
    exp_rel = {r["relation_id"] for r in new_ssot["relations"]}
    if cur_rel != exp_rel:
        errors.append(f"relations drift missing={sorted(exp_rel-cur_rel)} extra={sorted(cur_rel-exp_rel)}")
    if cur_rel != set(INTRA_EVENT_RELATION_MANIFEST_V1):
        errors.append(
            "ssot intra != python manifest "
            f"missing={sorted(set(INTRA_EVENT_RELATION_MANIFEST_V1)-cur_rel)} "
            f"extra={sorted(cur_rel-set(INTRA_EVENT_RELATION_MANIFEST_V1))}"
        )
    if exp_rel != set(INTRA_EVENT_RELATION_MANIFEST_V1):
        errors.append("rebuilt intra != python manifest")

    cur_cross = {r["relation_id"] for r in cur_ssot.get("cross_event_relations", [])}
    exp_cross = {r["relation_id"] for r in new_ssot["cross_event_relations"]}
    if cur_cross != exp_cross:
        errors.append(f"cross drift missing={sorted(exp_cross-cur_cross)} extra={sorted(cur_cross-exp_cross)}")
    if cur_cross != set(CROSS_EVENT_RELATION_MANIFEST_V1):
        errors.append(
            "ssot cross != python manifest "
            f"missing={sorted(set(CROSS_EVENT_RELATION_MANIFEST_V1)-cur_cross)} "
            f"extra={sorted(cur_cross-set(CROSS_EVENT_RELATION_MANIFEST_V1))}"
        )

    # Full SSOT comparable rows including classifications/relation IDs/rationales
    if _canonical_json(_ssot_comparable(cur_ssot)) != _canonical_json(_ssot_comparable(new_ssot)):
        errors.append("ssot full-row classification/relation/rationale drift vs rebuilt")

    # path inventory exact set + classifications
    cur_inv = {(p["event_type"], p["path"], p["classification"], p.get("relation_id"), tuple(p.get("relation_ids") or ())) for p in cur_ssot.get("path_inventory", [])}
    exp_inv = {(p["event_type"], p["path"], p["classification"], p.get("relation_id"), tuple(p.get("relation_ids") or ())) for p in new_ssot["path_inventory"]}
    if cur_inv != exp_inv:
        errors.append(
            f"path_inventory classified drift "
            f"missing={sorted(exp_inv-cur_inv)[:5]} extra={sorted(cur_inv-exp_inv)[:5]}"
        )

    # Corpus: full cases bytes + metadata
    desired_corpus_norm = copy.deepcopy(desired_corpus)
    # Compare relation twins coverage
    matrix = [c for c in cur_corpus["cases"] if str(c["id"]).startswith("rel_")]
    by_rel: dict[str, set[str]] = {}
    for c in matrix:
        by_rel.setdefault(c["relation_id"], set()).add(c["expect"])
    for rid in sorted(exp_rel):
        if by_rel.get(rid) != {"accept", "reject"}:
            errors.append(f"corpus missing twins for {rid}: {by_rel.get(rid)}")

    # Full corpus case id/value equality for relation matrix + metadata counts
    meta = cur_corpus.get("semantic_relation_matrix") or {}
    if int(meta.get("relation_count", -1)) != len(exp_rel):
        errors.append(f"corpus metadata relation_count stale {meta.get('relation_count')} != {len(exp_rel)}")
    if int(meta.get("cases_added", -1)) != len(exp_rel) * 2:
        errors.append(f"corpus metadata cases_added stale {meta.get('cases_added')} != {len(exp_rel)*2}")

    # FULL current-versus-desired corpus object/bytes equality (not counts/twins only).
    # Ordinary desired corpus is identity over current; any governed value/reason/path drift fails.
    # Canonical manifest already validated raw/canonical/order/per-case digests before desired.
    if _canonical_json(cur_corpus) != _canonical_json(desired_corpus_norm):
        errors.append("corpus full object/bytes drift vs ordinary desired (identity) corpus")
    errors.extend(
        validate_canonical_corpus_manifest(
            cur_corpus, raw_bytes=CORPUS_PATH.read_bytes(), mode="check-reassert"
        )
    )

    # Explicit bookkeeping only — residual undeclared paths fail both write and check.
    errors.extend(validate_explicit_bookkeeping_paths(cur_corpus, mode="check"))
    errors.extend(validate_explicit_bookkeeping_paths(desired_corpus_norm, mode="check-desired"))

    # Adversarial: inject second command_digest mutation while leaving declarations;
    # both write-planning and check-planning must fail/nonzero (no auto bookkeeping).
    adv_corpus = copy.deepcopy(cur_corpus)
    adv_hit = False
    for case in adv_corpus["cases"]:
        if case.get("expect") != "reject" or not case.get("mutated_path"):
            continue
        if case.get("mutated_path") == "payload.command_digest":
            continue
        acc = next(
            (
                c
                for c in adv_corpus["cases"]
                if c.get("relation_id") == case.get("relation_id") and c.get("expect") == "accept"
            ),
            None,
        )
        if not acc:
            continue
        # Inject an extra semantic mutation undeclared in bookkeeping_paths.
        rej_val = copy.deepcopy(case["value"])
        payload = rej_val.get("payload")
        if not isinstance(payload, dict) or "command_digest" not in payload:
            continue
        if payload.get("command_digest") == acc["value"].get("payload", {}).get("command_digest"):
            payload["command_digest"] = "sha256:" + ("99" * 32)
            case["value"] = rej_val
            adv_hit = True
            break
    if not adv_hit:
        errors.append("adversarial second command_digest injection could not locate a host reject case")
    else:
        adv_errors = validate_explicit_bookkeeping_paths(adv_corpus, mode="adversarial-check")
        if not adv_errors:
            errors.append(
                "adversarial second command_digest injection did not fail check planning "
                "(generator must not auto-authorize residual bookkeeping)"
            )
        # write planning path must also refuse expansion
        try:
            annotate_bookkeeping_paths(copy.deepcopy(adv_corpus))
            errors.append(
                "adversarial second command_digest injection did not fail write planning"
            )
        except SystemExit:
            pass

    # Compare full desired corpus cases count
    if len(cur_corpus["cases"]) != len(desired_corpus_norm["cases"]):
        errors.append(
            f"corpus case count drift {len(cur_corpus['cases'])} != {len(desired_corpus_norm['cases'])}"
        )

    # Parity full rows + recomputed mismatch_count (no zero bypass)
    if cur_parity is None:
        errors.append("missing parity artifact")
    else:
        rows = cur_parity.get("python_rows") or cur_parity.get("rows") or []
        row_ids = {r.get("relation_id") for r in rows}
        if row_ids != exp_rel:
            errors.append(f"parity row ids drift missing={sorted(exp_rel-row_ids)} extra={sorted(row_ids-exp_rel)}")
        recomputed = 0
        for r in rows:
            pa = r.get("python_actual")
            ta = r.get("ts_actual")
            exp = r.get("expected")
            if pa is None:
                errors.append(f"null python_actual for {r.get('relation_id')}")
            if ta is None:
                errors.append(f"null ts_actual for {r.get('relation_id')}")
            if pa is not None and pa != exp:
                recomputed += 1
            if ta is not None and ta != exp:
                recomputed += 1
            if pa is not None and ta is not None and pa != ta:
                recomputed += 1
        # mismatch_count must equal recomputed always (no hardcoded-zero bypass)
        if int(cur_parity.get("mismatch_count", -1)) != recomputed:
            errors.append(
                f"parity mismatch_count drift {cur_parity.get('mismatch_count')} != recomputed {recomputed}"
            )
        if int(cur_parity.get("relation_count", -1)) != len(exp_rel):
            errors.append("parity relation_count drift")
        # Compare against freshly computed parity python_actual values
        exp_parity = desired_parity
        exp_by_rel = {r["relation_id"]: r for r in exp_parity["python_rows"]}
        for r in rows:
            er = exp_by_rel.get(r["relation_id"])
            if not er:
                continue
            if r.get("python_actual") != er.get("python_actual") or r.get("expected") != er.get("expected"):
                errors.append(f"parity python_actual drift {r.get('relation_id')}")

    if errors:
        print("CHECK FAILED:")
        for e in errors:
            print(" -", e)
        return 1

    # Also require TS parity companion check when node/tsx available.
    import shutil
    import subprocess

    if shutil.which("node"):
        ts_check = subprocess.run(
            ["node", "--import", "tsx", str(REPO / "scripts/fill_semantic_relation_parity_ts.mjs"), "--check"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=False,
        )
        if ts_check.returncode != 0:
            print("TS parity check failed:")
            print(ts_check.stdout)
            print(ts_check.stderr)
            return ts_check.returncode
    print(
        json.dumps(
            {
                "check": "ok",
                "relations": len(exp_rel),
                "path_inventory": len(new_ssot["path_inventory"]),
                "cross_event_relations": len(exp_cross),
                "corpus_rel_pairs": len(exp_rel),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
