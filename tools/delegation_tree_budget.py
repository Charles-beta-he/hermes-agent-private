"""Pure, authority-neutral delegation tree-capacity preflight operations.

This module can consume an already validated Phase 2 expansion projection or
recompute that projection from a supplied definition and proposal. It does not
reserve capacity, mutate a ledger, construct children, grant authority, or
touch the live delegate path.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, NoReturn

import jsonschema

from tools.delegation_expansion import validate_delegation_expansion_v1


_SNAPSHOT_SCHEMA = "delegation-tree-budget-snapshot-v1.schema.json"
_PREFLIGHT_SCHEMA = "delegation-tree-budget-preflight-v1.schema.json"
_PROJECTION_SCHEMA = "delegation-expansion-projection-v1.schema.json"
_MAX_NODE_CAPACITY = 1_000_000
_MAX_SNAPSHOT_REVISION = 9_007_199_254_740_991
_SNAPSHOT_KEYS = {
    "schema_version",
    "tree_budget_id",
    "snapshot_revision",
    "remaining_node_capacity",
}
_PROJECTION_KEYS = {
    "schema_version",
    "definition_digest",
    "plan_digest",
    "proposal_digest",
    "authority_granted",
    "executable",
    "child_task_ids",
    "synthesis_task_id",
    "requested_child_nodes",
}
_PREFLIGHT_KEYS = {
    "schema_version",
    "tree_budget_id",
    "snapshot_revision",
    "budget_snapshot_digest",
    "proposal_digest",
    "requested_child_nodes",
    "capacity_fit_at_snapshot",
    "reason_codes",
    "authority_granted",
    "executable",
    "reservation_committed",
    "ledger_mutated",
}


@dataclass(frozen=True, slots=True)
class DelegationTreeBudgetError(ValueError):
    """Stable fail-closed contract error."""

    code: str
    message: str
    path: str = "$"

    def __str__(self) -> str:
        return f"{self.code} at {self.path}: {self.message}"


def _fail(code: str, message: str, path: str = "$") -> NoReturn:
    raise DelegationTreeBudgetError(code=code, message=message, path=path)


@lru_cache(maxsize=3)
def _schema_validator(schema_name: str) -> jsonschema.Draft202012Validator:
    schema_text = (
        resources.files("workflow_kernel.schemas")
        .joinpath(schema_name)
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _validate_schema(value: dict[str, Any], schema_name: str, code: str) -> None:
    errors = sorted(
        _schema_validator(schema_name).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    )
    _fail(code, error.message, path)


def _require_exact_object(
    value: object,
    *,
    source: str,
    keys: set[str],
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(f"{source}_input_not_plain_json", "input must be an exact object")
    assert type(value) is dict
    if any(type(key) is not str for key in value):
        _fail(
            f"{source}_input_not_plain_json",
            "object keys must be exact strings",
        )
    actual = set(value)
    if actual != keys:
        unknown = sorted(actual - keys)
        missing = sorted(keys - actual)
        detail = []
        if unknown:
            detail.append("unknown=" + ",".join(unknown))
        if missing:
            detail.append("missing=" + ",".join(missing))
        _fail(f"{source}_schema_invalid", "; ".join(detail))
    return value


def _canonical_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _require_wire_integer(
    value: object,
    *,
    path: str,
    minimum: int,
    maximum: int,
) -> int:
    """Normalize JSON integral numbers while rejecting host-only integer subclasses."""

    if type(value) is int:
        normalized = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        normalized = int(value)
    else:
        _fail("invalid_integer", f"{path} must be an integral JSON number", path)
    if normalized < minimum or normalized > maximum:
        _fail(
            "integer_out_of_range",
            f"{path} must be between {minimum} and {maximum}",
            path,
        )
    return normalized


def _normalize_snapshot(snapshot: object) -> dict[str, Any]:
    source = _require_exact_object(snapshot, source="snapshot", keys=_SNAPSHOT_KEYS)
    if type(source["schema_version"]) is not str:
        _fail("snapshot_input_not_plain_json", "schema_version must be an exact string")
    if type(source["tree_budget_id"]) is not str:
        _fail("snapshot_input_not_plain_json", "tree_budget_id must be an exact string")
    snapshot_revision = _require_wire_integer(
        source["snapshot_revision"],
        path="$.snapshot_revision",
        minimum=1,
        maximum=_MAX_SNAPSHOT_REVISION,
    )
    remaining_node_capacity = _require_wire_integer(
        source["remaining_node_capacity"],
        path="$.remaining_node_capacity",
        minimum=0,
        maximum=_MAX_NODE_CAPACITY,
    )
    normalized = {
        "schema_version": source["schema_version"],
        "tree_budget_id": source["tree_budget_id"],
        "snapshot_revision": snapshot_revision,
        "remaining_node_capacity": remaining_node_capacity,
    }
    _validate_schema(normalized, _SNAPSHOT_SCHEMA, "snapshot_schema_invalid")
    return normalized


def _normalize_projection(projection: object) -> dict[str, Any]:
    source = _require_exact_object(
        projection,
        source="projection",
        keys=_PROJECTION_KEYS,
    )
    string_fields = (
        "schema_version",
        "definition_digest",
        "plan_digest",
        "proposal_digest",
        "synthesis_task_id",
    )
    for field in string_fields:
        if type(source[field]) is not str:
            _fail(
                "projection_input_not_plain_json",
                f"{field} must be an exact string",
                f"$.{field}",
            )
    for field in ("authority_granted", "executable"):
        if type(source[field]) is not bool:
            _fail(
                "projection_input_not_plain_json",
                f"{field} must be an exact boolean",
                f"$.{field}",
            )
    requested_child_nodes = _require_wire_integer(
        source["requested_child_nodes"],
        path="$.requested_child_nodes",
        minimum=1,
        maximum=_MAX_NODE_CAPACITY,
    )
    children = source["child_task_ids"]
    if type(children) is not list or any(type(item) is not str for item in children):
        _fail(
            "projection_input_not_plain_json",
            "child_task_ids must be an exact list of exact strings",
            "$.child_task_ids",
        )
    normalized = {
        "schema_version": source["schema_version"],
        "definition_digest": source["definition_digest"],
        "plan_digest": source["plan_digest"],
        "proposal_digest": source["proposal_digest"],
        "authority_granted": source["authority_granted"],
        "executable": source["executable"],
        "child_task_ids": list(children),
        "synthesis_task_id": source["synthesis_task_id"],
        "requested_child_nodes": requested_child_nodes,
    }
    if len(set(children)) != len(children):
        _fail(
            "duplicate_child_task_id",
            "child_task_ids must be unique",
            "$.child_task_ids",
        )
    if normalized["requested_child_nodes"] != len(children):
        _fail(
            "requested_child_nodes_mismatch",
            "requested_child_nodes must equal child_task_ids length",
            "$.requested_child_nodes",
        )
    _validate_schema(normalized, _PROJECTION_SCHEMA, "projection_schema_invalid")
    normalized_proposal = {
        "schema_version": "delegation_expansion_proposal_v1",
        "plan_digest": normalized["plan_digest"],
        "child_task_ids": sorted(children),
        "synthesis_task_id": normalized["synthesis_task_id"],
    }
    if _canonical_digest(normalized_proposal) != normalized["proposal_digest"]:
        _fail(
            "proposal_digest_mismatch",
            "projection is not bound to its normalized Phase 2 proposal",
            "$.proposal_digest",
        )
    return normalized


def evaluate_delegation_tree_budget_preflight_from_sources_v1(
    definition: object,
    proposal: object,
    snapshot: object,
) -> dict[str, Any]:
    """Recompute Phase 2 from canonical sources, then evaluate Phase 3."""

    projection = validate_delegation_expansion_v1(definition, proposal)
    return evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)


def evaluate_delegation_tree_budget_preflight_v1(
    snapshot: object,
    expansion_projection: object,
) -> dict[str, Any]:
    """Evaluate capacity at one snapshot without admission or reservation."""

    normalized_snapshot = _normalize_snapshot(snapshot)
    normalized_projection = _normalize_projection(expansion_projection)
    requested = normalized_projection["requested_child_nodes"]
    fits = requested <= normalized_snapshot["remaining_node_capacity"]
    result = {
        "schema_version": "hermes_delegation_tree_budget_preflight_v1",
        "tree_budget_id": normalized_snapshot["tree_budget_id"],
        "snapshot_revision": normalized_snapshot["snapshot_revision"],
        "budget_snapshot_digest": _canonical_digest(normalized_snapshot),
        "proposal_digest": normalized_projection["proposal_digest"],
        "requested_child_nodes": requested,
        "capacity_fit_at_snapshot": fits,
        "reason_codes": [
            "tree_capacity_available_at_snapshot"
            if fits
            else "tree_capacity_insufficient_at_snapshot"
        ],
        "authority_granted": False,
        "executable": False,
        "reservation_committed": False,
        "ledger_mutated": False,
    }
    return validate_delegation_tree_budget_preflight_v1(result)


def validate_delegation_tree_budget_preflight_v1(value: object) -> dict[str, Any]:
    """Validate and detach a preflight projection, including derived relations."""

    source = _require_exact_object(value, source="preflight", keys=_PREFLIGHT_KEYS)
    strings = (
        "schema_version",
        "tree_budget_id",
        "budget_snapshot_digest",
        "proposal_digest",
    )
    for field in strings:
        if type(source[field]) is not str:
            _fail(
                "preflight_input_not_plain_json",
                f"{field} must be an exact string",
                f"$.{field}",
            )
    snapshot_revision = _require_wire_integer(
        source["snapshot_revision"],
        path="$.snapshot_revision",
        minimum=1,
        maximum=_MAX_SNAPSHOT_REVISION,
    )
    requested_child_nodes = _require_wire_integer(
        source["requested_child_nodes"],
        path="$.requested_child_nodes",
        minimum=1,
        maximum=_MAX_NODE_CAPACITY,
    )
    for field in (
        "capacity_fit_at_snapshot",
        "authority_granted",
        "executable",
        "reservation_committed",
        "ledger_mutated",
    ):
        if type(source[field]) is not bool:
            _fail(
                "preflight_input_not_plain_json",
                f"{field} must be an exact boolean",
                f"$.{field}",
            )
    reasons = source["reason_codes"]
    if type(reasons) is not list or any(type(item) is not str for item in reasons):
        _fail(
            "preflight_input_not_plain_json",
            "reason_codes must be an exact list of exact strings",
            "$.reason_codes",
        )
    normalized = {
        "schema_version": source["schema_version"],
        "tree_budget_id": source["tree_budget_id"],
        "snapshot_revision": snapshot_revision,
        "budget_snapshot_digest": source["budget_snapshot_digest"],
        "proposal_digest": source["proposal_digest"],
        "requested_child_nodes": requested_child_nodes,
        "capacity_fit_at_snapshot": source["capacity_fit_at_snapshot"],
        "reason_codes": list(reasons),
        "authority_granted": source["authority_granted"],
        "executable": source["executable"],
        "reservation_committed": source["reservation_committed"],
        "ledger_mutated": source["ledger_mutated"],
    }
    expected_reason = (
        "tree_capacity_available_at_snapshot"
        if normalized["capacity_fit_at_snapshot"]
        else "tree_capacity_insufficient_at_snapshot"
    )
    if normalized["reason_codes"] != [expected_reason]:
        _fail(
            "preflight_reason_mismatch",
            "reason_codes must match capacity_fit_at_snapshot",
            "$.reason_codes",
        )
    _validate_schema(normalized, _PREFLIGHT_SCHEMA, "preflight_schema_invalid")
    return normalized


__all__ = [
    "DelegationTreeBudgetError",
    "evaluate_delegation_tree_budget_preflight_from_sources_v1",
    "evaluate_delegation_tree_budget_preflight_v1",
    "validate_delegation_tree_budget_preflight_v1",
]
