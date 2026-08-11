"""Authority-neutral contract validation for adaptive delegation expansion.

This module composes with the canonical WorkflowDefinitionV1 compiler.  It does
not construct children, schedule work, grant authority, consume budget, log, or
persist anything.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from itertools import combinations
from typing import Any, NoReturn

import jsonschema

from workflow_kernel.workflow_definition import (
    WorkflowDefinitionError,
    compile_workflow_definition_v1,
)


_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 20_000
_MAX_JSON_STRING = 1_048_576


@dataclass(frozen=True, slots=True)
class DelegationExpansionError(ValueError):
    """Stable fail-closed contract error."""

    code: str
    message: str
    path: str = "$"

    def __str__(self) -> str:
        return f"{self.code} at {self.path}: {self.message}"


def _fail(code: str, message: str, path: str = "$") -> NoReturn:
    raise DelegationExpansionError(code=code, message=message, path=path)


def _snapshot_plain_json(
    value: object,
    *,
    source: str,
    path: str = "$",
    depth: int = 0,
    counter: list[int] | None = None,
) -> Any:
    """Detach exact built-in JSON values while rejecting executable subclasses."""

    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
        _fail(f"{source}_input_too_large", "JSON input exceeds structural bounds", path)

    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail(f"{source}_input_not_plain_json", "non-finite number is not JSON", path)
        return value
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING:
            _fail(f"{source}_input_too_large", "string exceeds input bound", path)
        return value
    if type(value) is list:
        return [
            _snapshot_plain_json(
                item,
                source=source,
                path=f"{path}[{index}]",
                depth=depth + 1,
                counter=counter,
            )
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        snapshot: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                _fail(
                    f"{source}_input_not_plain_json",
                    "object keys must be exact strings",
                    path,
                )
            snapshot[key] = _snapshot_plain_json(
                item,
                source=source,
                path=f"{path}.{key}",
                depth=depth + 1,
                counter=counter,
            )
        return snapshot
    _fail(
        f"{source}_input_not_plain_json",
        f"unsupported JSON value type {type(value).__name__!r}",
        path,
    )


@lru_cache(maxsize=2)
def _schema_validator(schema_name: str) -> jsonschema.Draft202012Validator:
    schema_text = (
        resources.files("workflow_kernel.schemas")
        .joinpath(schema_name)
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _validate_proposal_schema(proposal: Any) -> None:
    errors = sorted(
        _schema_validator("delegation-expansion-proposal-v1.schema.json").iter_errors(
            proposal
        ),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    )
    _fail("proposal_schema_invalid", error.message, path)


def _canonical_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _ancestor_sets(task_by_id: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    ancestors = {
        task_id: set(binding["depends_on"])
        for task_id, binding in task_by_id.items()
    }
    changed = True
    while changed:
        changed = False
        for task_id in sorted(ancestors):
            expanded = set(ancestors[task_id])
            for dependency in tuple(ancestors[task_id]):
                expanded.update(ancestors[dependency])
            if expanded != ancestors[task_id]:
                ancestors[task_id] = expanded
                changed = True
    return ancestors


def validate_delegation_expansion_v1(
    definition: object,
    proposal: object,
) -> dict[str, Any]:
    """Validate a plan-bound fan-out frontier and return inert projection data.

    The canonical workflow compiler owns all graph, authority, write-ownership,
    evidence, verifier, and loop invariants.  This boundary adds only
    delegation-specific frontier and synthesis relationships.
    """

    definition_snapshot = _snapshot_plain_json(definition, source="definition")
    proposal_snapshot = _snapshot_plain_json(proposal, source="proposal")

    if type(proposal_snapshot) is dict:
        children = proposal_snapshot.get("child_task_ids")
        if type(children) is list and all(type(item) is str for item in children):
            if len(set(children)) != len(children):
                _fail(
                    "duplicate_child_task_id",
                    "child_task_ids must be unique",
                    "$.child_task_ids",
                )

    _validate_proposal_schema(proposal_snapshot)
    assert type(proposal_snapshot) is dict

    try:
        plan = compile_workflow_definition_v1(definition_snapshot)
    except WorkflowDefinitionError as error:
        _fail(
            f"definition_{error.code}",
            error.message,
            error.path,
        )

    if proposal_snapshot["plan_digest"] != plan["plan_digest"]:
        _fail(
            "plan_digest_mismatch",
            "proposal is not bound to the compiled canonical plan",
            "$.plan_digest",
        )

    task_by_id = {
        binding["task_id"]: binding
        for binding in plan["task_bindings"]
    }
    child_ids = proposal_snapshot["child_task_ids"]
    synthesis_id = proposal_snapshot["synthesis_task_id"]

    for child_id in child_ids:
        if child_id not in task_by_id:
            _fail(
                "unknown_child_task",
                f"unknown child task {child_id!r}",
                "$.child_task_ids",
            )
    if synthesis_id not in task_by_id:
        _fail(
            "unknown_synthesis_task",
            f"unknown synthesis task {synthesis_id!r}",
            "$.synthesis_task_id",
        )
    if synthesis_id in child_ids:
        _fail(
            "synthesis_is_child",
            "synthesis task must be distinct from every selected child",
            "$.synthesis_task_id",
        )
    ancestors = _ancestor_sets(task_by_id)
    for left_id, right_id in combinations(sorted(child_ids), 2):
        if left_id in ancestors[right_id] or right_id in ancestors[left_id]:
            _fail(
                "child_frontier_not_antichain",
                "selected child tasks must form one dependency antichain",
                "$.child_task_ids",
            )

    missing_join = sorted(set(child_ids) - ancestors[synthesis_id])
    if missing_join:
        _fail(
            "synthesis_not_descendant",
            "synthesis task is not transitively downstream of every child: "
            + ", ".join(missing_join),
            "$.synthesis_task_id",
        )
    if task_by_id[synthesis_id]["role"] != "execute":
        _fail(
            "synthesis_role_invalid",
            "synthesis task must use the execute role and remain independently verified",
            "$.synthesis_task_id",
        )

    normalized_proposal = {
        "schema_version": proposal_snapshot["schema_version"],
        "plan_digest": proposal_snapshot["plan_digest"],
        "child_task_ids": sorted(child_ids),
        "synthesis_task_id": synthesis_id,
    }
    projection = {
        "schema_version": "delegation_expansion_projection_v1",
        "definition_digest": plan["definition_digest"],
        "plan_digest": plan["plan_digest"],
        "proposal_digest": _canonical_digest(normalized_proposal),
        "authority_granted": False,
        "executable": False,
        "child_task_ids": normalized_proposal["child_task_ids"],
        "synthesis_task_id": synthesis_id,
        "requested_child_nodes": len(child_ids),
    }
    projection_errors = list(
        _schema_validator("delegation-expansion-projection-v1.schema.json").iter_errors(
            projection
        )
    )
    if projection_errors:
        _fail("projection_schema_invalid", projection_errors[0].message)
    return projection
