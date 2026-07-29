"""Pure, read-only personal semantic goal reconciliation for Agent OS.

The module validates an explicit input packet and returns deterministic goal-gap,
candidate, intention, authority-projection, and receipt/readback state.  It never
writes lifecycle state and never executes a capability; ``may_execute`` is
always false by contract.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, NoReturn

INPUT_SCHEMA = "PersonalSemanticReconcileInput.v1"
CONTRACT_SCHEMA = "PersonalSemanticGoalReconciler.v1"
RESULT_SCHEMA = "PersonalSemanticReconciliationResult.v1"

_BOUNDARY = {
    "runtime_mutation": False,
    "capability_execution": False,
    "authority_expansion": False,
    "external_side_effects": False,
    "durable_semantic_writeback": False,
}

_SCOPE_KINDS = {"global", "role", "project", "task", "session"}
_EPISTEMIC = {"explicit", "observed", "derived", "inferred", "hypothetical"}
_SOURCE_OWNERS = {
    "user_profile",
    "project_profile",
    "skill_workflow",
    "mission",
    "taskpool",
    "runtime_probe",
    "evidence_store",
    "authority_ledger",
}
_ASSERTED_BY = {"user", "native_system", "deterministic_rule", "model"}
_OPERATORS = {"eq", "ne", "gte", "lte"}
_PRIORITIES = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
_RISK_PENALTIES = {"T0": 0.0, "T1": 0.05, "T2": 0.1, "T3": 0.2, "T4": 0.35, "T5": 0.5}
_SIDE_EFFECTS = {f"S{index}": index for index in range(8)}


class ReconciliationValidationError(ValueError):
    """Structured, fail-closed packet validation error."""

    def __init__(self, findings: list[dict[str, str]]):
        super().__init__("invalid personal semantic reconciliation packet")
        self.findings = findings


class _Validator:
    def __init__(self) -> None:
        self.findings: list[dict[str, str]] = []

    def add(self, path: str, code: str, message: str) -> None:
        self.findings.append({"path": path, "code": code, "message": message})

    def require_mapping(self, value: Any, path: str) -> dict[str, Any] | None:
        if type(value) is not dict:
            self.add(path, "invalid_type", "must be an object")
            return None
        return value

    def require_list(self, value: Any, path: str) -> list[Any] | None:
        if type(value) is not list:
            self.add(path, "invalid_type", "must be an array")
            return None
        return value

    def fields(self, value: dict[str, Any], required: set[str], allowed: set[str], path: str) -> None:
        string_keys = {key for key in value if isinstance(key, str)}
        non_string_keys = sorted(repr(key) for key in value if not isinstance(key, str))
        if non_string_keys:
            self.add(path, "invalid_field_name", f"field names must be strings: {', '.join(non_string_keys)}")
        for key in sorted(required - string_keys):
            self.add(f"{path}.{key}", "missing_field", "required field is missing")
        extras = sorted(string_keys - allowed)
        if extras:
            self.add(path, "unexpected_fields", f"unexpected fields: {', '.join(extras)}")

    def string(self, value: Any, path: str, *, non_empty: bool = True) -> bool:
        if not isinstance(value, str) or (non_empty and not value.strip()):
            self.add(path, "invalid_string", "must be a non-empty string")
            return False
        return True

    def number(self, value: Any, path: str, *, minimum: float | None = None, maximum: float | None = None) -> bool:
        if isinstance(value, bool) or type(value) not in (int, float):
            self.add(path, "invalid_number", "must be a finite number")
            return False
        try:
            numeric = float(value)
        except OverflowError:
            self.add(path, "invalid_number", "must be a finite representable number")
            return False
        if not math.isfinite(numeric):
            self.add(path, "invalid_number", "must be a finite number")
            return False
        if minimum is not None and numeric < minimum:
            self.add(path, "invalid_number", f"must be >= {minimum}")
            return False
        if maximum is not None and numeric > maximum:
            self.add(path, "invalid_number", f"must be <= {maximum}")
            return False
        return True

    def integer(self, value: Any, path: str, *, minimum: int = 0) -> bool:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            self.add(path, "invalid_integer", f"must be an integer >= {minimum}")
            return False
        return True

    def timestamp(self, value: Any, path: str, *, nullable: bool = False) -> datetime | None:
        if value is None and nullable:
            return None
        if not isinstance(value, str):
            self.add(path, "invalid_timestamp", "must be an ISO-8601 timestamp")
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            self.add(path, "invalid_timestamp", "must be an ISO-8601 timestamp")
            return None
        if parsed.tzinfo is None:
            self.add(path, "invalid_timestamp", "timestamp must include a timezone")
            return None
        return parsed

    def finish(self) -> None:
        if self.findings:
            self.findings.sort(key=lambda item: (item["path"], item["code"], item["message"]))
            raise ReconciliationValidationError(self.findings)


def _validate_json_value(validator: _Validator, value: Any, path: str) -> None:
    """Reject Python-only, cyclic, over-deep, and non-finite typed-JSON input."""
    stack: list[tuple[Any, str, int, frozenset[int]]] = [(value, path, 0, frozenset())]
    visited = 0
    while stack:
        current, current_path, depth, ancestors = stack.pop()
        visited += 1
        if visited > 100_000:
            validator.add(path, "json_node_limit", "packet exceeds the typed-JSON node limit")
            return
        if depth > 64:
            validator.add(current_path, "json_depth_limit", "typed-JSON nesting exceeds 64 levels")
            continue
        next_ancestors = ancestors
        if type(current) in (dict, list):
            identity = id(current)
            if identity in ancestors:
                validator.add(current_path, "invalid_json_cycle", "typed JSON cannot contain cycles")
                continue
            next_ancestors = ancestors | {identity}
        if type(current) is dict:
            for index, (key, child) in enumerate(current.items()):
                if type(key) is not str:
                    validator.add(
                        current_path,
                        "invalid_field_name",
                        f"field name at object index {index} must be an exact string",
                    )
                    continue
                stack.append((child, current_path + ".[field]", depth + 1, next_ancestors))
            continue
        if type(current) is list:
            for index, child in enumerate(current):
                stack.append((child, f"{current_path}[{index}]", depth + 1, next_ancestors))
            continue
        if type(current) is float and not math.isfinite(current):
            validator.add(current_path, "invalid_json_number", "JSON numbers must be finite")
            continue
        if current is not None and type(current) not in (str, int, float, bool):
            validator.add(current_path, "invalid_json_type", "value is not representable in JSON")


def _stable_id(prefix: str, *parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def trusted_row_binding(entity_type: str, row: dict[str, Any]) -> str:
    """Bind native-owner trust to the canonical exact bytes of one validated row."""
    return _stable_id("trusted-row", entity_type, row)


def _row_is_trusted(entity_type: str, row: dict[str, Any], bindings: frozenset[str]) -> bool:
    return trusted_row_binding(entity_type, row) in bindings


def _is_vocab_value(value: Any, vocabulary: Any) -> bool:
    """Check membership without letting unhashable JSON values escape validation."""
    return isinstance(value, str) and value in vocabulary


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _is_nonnegative_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _validate_scope(validator: _Validator, value: Any, path: str) -> dict[str, Any] | None:
    scope = validator.require_mapping(value, path)
    if scope is None:
        return None
    validator.fields(scope, {"kind", "ref"}, {"kind", "ref"}, path)
    kind = scope.get("kind")
    if not _is_vocab_value(kind, _SCOPE_KINDS):
        validator.add(f"{path}.kind", "invalid_scope", "unknown scope kind")
    ref = scope.get("ref")
    if kind == "global":
        if ref is not None:
            validator.add(f"{path}.ref", "invalid_scope", "global scope ref must be null")
    elif not isinstance(ref, str) or not ref:
        validator.add(f"{path}.ref", "invalid_scope", "non-global scope requires a ref")
    return scope


def _validate_budget(validator: _Validator, value: Any, path: str, fields: tuple[str, ...]) -> dict[str, Any] | None:
    budget = validator.require_mapping(value, path)
    if budget is None:
        return None
    expected = set(fields)
    validator.fields(budget, expected, expected, path)
    for field in fields:
        if field not in budget:
            continue
        if field == "cost_units":
            validator.number(budget[field], f"{path}.{field}", minimum=0)
        else:
            validator.integer(budget[field], f"{path}.{field}", minimum=0)
    return budget


def _validate_goal(validator: _Validator, goal: Any, path: str) -> dict[str, Any] | None:
    value = validator.require_mapping(goal, path)
    if value is None:
        return None
    required = {
        "goal_id",
        "owner_ref",
        "parent_goal_ref",
        "subject_ref",
        "predicate",
        "operator",
        "desired_value",
        "tolerance",
        "priority",
        "observation_sources",
        "max_observation_age_seconds",
        "scope",
        "budget",
        "side_effect_ceiling",
        "cooldown_seconds",
        "stop_conditions",
        "revision",
    }
    validator.fields(value, required, required, path)
    for field in ("goal_id", "owner_ref", "subject_ref", "predicate", "revision"):
        if field in value:
            validator.string(value[field], f"{path}.{field}")
    parent = value.get("parent_goal_ref")
    if parent is not None:
        validator.string(parent, f"{path}.parent_goal_ref")
    operator = value.get("operator")
    if not _is_vocab_value(operator, _OPERATORS):
        validator.add(f"{path}.operator", "invalid_operator", "operator must be eq, ne, gte, or lte")
    desired = value.get("desired_value")
    if isinstance(desired, (dict, list)) or desired is None:
        validator.add(f"{path}.desired_value", "invalid_scalar", "desired value must be a non-null scalar")
    tolerance = value.get("tolerance")
    if tolerance is not None:
        validator.number(tolerance, f"{path}.tolerance", minimum=0)
    if operator in ("gte", "lte"):
        validator.number(desired, f"{path}.desired_value")
    elif isinstance(desired, (int, float)) and not isinstance(desired, bool):
        validator.number(desired, f"{path}.desired_value")
    if not _is_vocab_value(value.get("priority"), _PRIORITIES):
        validator.add(f"{path}.priority", "invalid_priority", "unknown priority")
    sources = validator.require_list(value.get("observation_sources"), f"{path}.observation_sources")
    if sources is not None:
        if not sources:
            validator.add(f"{path}.observation_sources", "empty_sources", "at least one source is required")
        for index, source in enumerate(sources):
            validator.string(source, f"{path}.observation_sources[{index}]")
    validator.integer(value.get("max_observation_age_seconds"), f"{path}.max_observation_age_seconds", minimum=0)
    _validate_scope(validator, value.get("scope"), f"{path}.scope")
    _validate_budget(validator, value.get("budget"), f"{path}.budget", ("interruptions", "executions", "cost_units"))
    if not _is_vocab_value(value.get("side_effect_ceiling"), _SIDE_EFFECTS):
        validator.add(f"{path}.side_effect_ceiling", "invalid_side_effect_level", "unknown side-effect level")
    validator.integer(value.get("cooldown_seconds"), f"{path}.cooldown_seconds", minimum=0)
    stops = validator.require_list(value.get("stop_conditions"), f"{path}.stop_conditions")
    if stops is not None:
        for index, stop in enumerate(stops):
            validator.string(stop, f"{path}.stop_conditions[{index}]")
    return value


def _scope_is_within(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    if parent.get("kind") == "global":
        return True
    return child == parent


def _validate_parent_goal(validator: _Validator, goal: dict[str, Any] | None, parent: Any) -> None:
    if goal is None:
        return
    parent_ref = goal.get("parent_goal_ref")
    if parent_ref is None:
        if parent is not None:
            validator.add("parent_goal", "unexpected_parent_goal", "root goal must not include parent_goal")
        return
    if parent is None:
        validator.add("parent_goal", "missing_parent_goal", "child goal requires parent goal context")
        return
    parent_value = _validate_goal(validator, parent, "parent_goal")
    if parent_value is None:
        return
    if parent_value.get("goal_id") != parent_ref:
        validator.add("goal.parent_goal_ref", "parent_goal_mismatch", "parent reference does not match parent goal id")
    child_scope = goal.get("scope")
    parent_scope = parent_value.get("scope")
    if isinstance(child_scope, dict) and isinstance(parent_scope, dict) and not _scope_is_within(child_scope, parent_scope):
        validator.add("goal.scope", "child_goal_scope_widening", "child goal scope is not within parent scope")
    child_budget = goal.get("budget")
    parent_budget = parent_value.get("budget")
    if isinstance(child_budget, dict) and isinstance(parent_budget, dict):
        comparable = (
            _is_nonnegative_integer(child_budget.get("interruptions"))
            and _is_nonnegative_integer(parent_budget.get("interruptions"))
            and _is_nonnegative_integer(child_budget.get("executions"))
            and _is_nonnegative_integer(parent_budget.get("executions"))
            and _is_finite_number(child_budget.get("cost_units"))
            and _is_finite_number(parent_budget.get("cost_units"))
        )
        if comparable and any(
            float(child_budget[key]) > float(parent_budget[key])
            for key in ("interruptions", "executions", "cost_units")
        ):
            validator.add("goal.budget", "child_goal_budget_widening", "child goal budget exceeds parent budget")
    child_ceiling_value = goal.get("side_effect_ceiling")
    parent_ceiling_value = parent_value.get("side_effect_ceiling")
    child_ceiling = _SIDE_EFFECTS.get(child_ceiling_value) if isinstance(child_ceiling_value, str) else None
    parent_ceiling = _SIDE_EFFECTS.get(parent_ceiling_value) if isinstance(parent_ceiling_value, str) else None
    if child_ceiling is not None and parent_ceiling is not None and child_ceiling > parent_ceiling:
        validator.add("goal.side_effect_ceiling", "child_goal_side_effect_widening", "child goal side-effect ceiling exceeds parent")


def _validate_assertions(validator: _Validator, assertions: Any, now: datetime | None) -> None:
    values = validator.require_list(assertions, "assertions")
    if values is None:
        return
    required = {
        "assertion_id",
        "subject_ref",
        "predicate",
        "object",
        "epistemic_status",
        "scope",
        "source_ref",
        "source_owner",
        "asserted_by",
        "observed_at",
        "valid_from",
        "valid_until",
        "confidence",
        "revision_of",
        "authority_effect",
    }
    for index, item in enumerate(values):
        path = f"assertions[{index}]"
        value = validator.require_mapping(item, path)
        if value is None:
            continue
        validator.fields(value, required, required, path)
        for field in ("assertion_id", "subject_ref", "predicate", "source_ref"):
            if field in value:
                validator.string(value[field], f"{path}.{field}")
        if not _is_vocab_value(value.get("epistemic_status"), _EPISTEMIC):
            validator.add(f"{path}.epistemic_status", "invalid_epistemic_status", "unknown epistemic status")
        if not _is_vocab_value(value.get("source_owner"), _SOURCE_OWNERS):
            validator.add(f"{path}.source_owner", "invalid_source_owner", "unknown native source owner")
        if not _is_vocab_value(value.get("asserted_by"), _ASSERTED_BY):
            validator.add(f"{path}.asserted_by", "invalid_asserted_by", "unknown asserting actor")
        if value.get("authority_effect") != "none":
            validator.add(f"{path}.authority_effect", "authority_effect_must_be_none", "semantic assertions cannot grant authority")
        _validate_scope(validator, value.get("scope"), f"{path}.scope")
        timestamps = {
            field: validator.timestamp(value.get(field), f"{path}.{field}", nullable=True)
            for field in ("observed_at", "valid_from", "valid_until")
        }
        if timestamps["observed_at"] is not None and now is not None and timestamps["observed_at"] > now:
            validator.add(f"{path}.observed_at", "future_assertion", "assertion observation is after packet now")
        if (
            timestamps["valid_from"] is not None
            and timestamps["valid_until"] is not None
            and timestamps["valid_until"] <= timestamps["valid_from"]
        ):
            validator.add(f"{path}.valid_until", "invalid_assertion_window", "valid_until must be after valid_from")
        confidence = value.get("confidence")
        if confidence is not None:
            validator.number(confidence, f"{path}.confidence", minimum=0, maximum=1)
        revision = value.get("revision_of")
        if revision is not None:
            validator.string(revision, f"{path}.revision_of")
        if value.get("epistemic_status") in ("inferred", "hypothetical") and value.get("asserted_by") == "user":
            validator.add(path, "inferred_assertion_claims_user", "inferred/hypothetical assertion cannot claim the user as asserting actor")


def _validate_observations(
    validator: _Validator,
    observations: Any,
    now: datetime | None,
    goal: dict[str, Any] | None,
) -> None:
    values = validator.require_list(observations, "observations")
    if values is None:
        return
    required = {"observation_id", "subject_ref", "predicate", "value", "observed_at", "source_ref", "provenance_ref"}
    for index, item in enumerate(values):
        path = f"observations[{index}]"
        value = validator.require_mapping(item, path)
        if value is None:
            continue
        validator.fields(value, required, required, path)
        for field in ("observation_id", "subject_ref", "predicate", "source_ref", "provenance_ref"):
            if field in value:
                validator.string(value[field], f"{path}.{field}")
        observed_value = value.get("value")
        if isinstance(observed_value, (dict, list)) or observed_value is None:
            validator.add(f"{path}.value", "invalid_scalar", "observation value must be a non-null scalar")
        elif (
            goal is not None
            and goal.get("operator") in ("gte", "lte")
            and value.get("subject_ref") == goal.get("subject_ref")
            and value.get("predicate") == goal.get("predicate")
        ):
            validator.number(observed_value, f"{path}.value")
        elif isinstance(observed_value, (int, float)) and not isinstance(observed_value, bool):
            validator.number(observed_value, f"{path}.value")
        observed_at = validator.timestamp(value.get("observed_at"), f"{path}.observed_at")
        if observed_at is not None and now is not None and observed_at > now:
            validator.add(f"{path}.observed_at", "future_observation", "observation timestamp is after packet now")


def _validate_actions(validator: _Validator, actions: Any, goal: dict[str, Any] | None) -> None:
    values = validator.require_list(actions, "action_contracts")
    if values is None:
        return
    required = {
        "action_id",
        "capability_ref",
        "serves_predicates",
        "target_ref",
        "expected_effect",
        "risk_class",
        "side_effect_level",
        "required_authority",
        "preconditions",
        "rollback_method",
        "readback_method",
        "estimated_cost_units",
    }
    for index, item in enumerate(values):
        path = f"action_contracts[{index}]"
        value = validator.require_mapping(item, path)
        if value is None:
            continue
        validator.fields(value, required, required, path)
        for field in (
            "action_id",
            "capability_ref",
            "target_ref",
            "expected_effect",
            "required_authority",
            "rollback_method",
            "readback_method",
        ):
            if field in value:
                validator.string(value[field], f"{path}.{field}")
        serves = validator.require_list(value.get("serves_predicates"), f"{path}.serves_predicates")
        if serves is not None:
            if not serves:
                validator.add(f"{path}.serves_predicates", "empty_predicates", "at least one predicate is required")
            for item_index, predicate in enumerate(serves):
                validator.string(predicate, f"{path}.serves_predicates[{item_index}]")
        preconditions = validator.require_list(value.get("preconditions"), f"{path}.preconditions")
        if preconditions is not None:
            for item_index, condition in enumerate(preconditions):
                validator.string(condition, f"{path}.preconditions[{item_index}]")
        if not _is_vocab_value(value.get("risk_class"), _RISK_PENALTIES):
            validator.add(f"{path}.risk_class", "invalid_risk_class", "unknown risk class")
        side_effect = value.get("side_effect_level")
        if not _is_vocab_value(side_effect, _SIDE_EFFECTS):
            validator.add(f"{path}.side_effect_level", "invalid_side_effect_level", "unknown side-effect level")
        elif goal is not None and _is_vocab_value(goal.get("side_effect_ceiling"), _SIDE_EFFECTS):
            if _SIDE_EFFECTS[side_effect] > _SIDE_EFFECTS[goal["side_effect_ceiling"]]:
                validator.add(f"{path}.side_effect_level", "action_exceeds_goal_ceiling", "action exceeds goal side-effect ceiling")
        if goal is not None:
            goal_scope = goal.get("scope")
            if (
                isinstance(goal_scope, dict)
                and isinstance(goal_scope.get("kind"), str)
                and goal_scope.get("kind") in {"role", "project", "task", "session"}
                and isinstance(goal_scope.get("ref"), str)
                and value.get("target_ref") != goal_scope["ref"]
            ):
                validator.add(
                    f"{path}.target_ref",
                    "action_outside_goal_scope",
                    "non-global action target must exactly match the goal scope reference",
                )
        validator.number(value.get("estimated_cost_units"), f"{path}.estimated_cost_units", minimum=0)


def _validate_grants(validator: _Validator, grants: Any, now: datetime | None) -> None:
    values = validator.require_list(grants, "authority_grants")
    if values is None:
        return
    required = {
        "grant_id",
        "granted_by",
        "capability_ref",
        "target_ref",
        "side_effect_level",
        "scope",
        "issued_at",
        "expires_at",
        "budget",
        "provenance_ref",
        "status",
    }
    for index, item in enumerate(values):
        path = f"authority_grants[{index}]"
        value = validator.require_mapping(item, path)
        if value is None:
            continue
        validator.fields(value, required, required, path)
        for field in ("grant_id", "capability_ref", "target_ref", "provenance_ref"):
            if field in value:
                validator.string(value[field], f"{path}.{field}")
        if value.get("granted_by") not in ("user", "configured_policy", "external_authority"):
            validator.add(f"{path}.granted_by", "invalid_grant_principal", "unknown grant principal")
        if not _is_vocab_value(value.get("side_effect_level"), _SIDE_EFFECTS):
            validator.add(f"{path}.side_effect_level", "invalid_side_effect_level", "unknown side-effect level")
        _validate_scope(validator, value.get("scope"), f"{path}.scope")
        issued = validator.timestamp(value.get("issued_at"), f"{path}.issued_at")
        expires = validator.timestamp(value.get("expires_at"), f"{path}.expires_at")
        if issued is not None and expires is not None and expires <= issued:
            validator.add(f"{path}.expires_at", "invalid_grant_window", "grant must expire after issuance")
        if issued is not None and now is not None and issued > now:
            validator.add(f"{path}.issued_at", "future_grant", "grant is not active yet")
        _validate_budget(validator, value.get("budget"), f"{path}.budget", ("executions", "cost_units"))
        if value.get("status") not in ("active", "revoked"):
            validator.add(f"{path}.status", "invalid_grant_status", "grant status must be active or revoked")


def _validate_receipts(validator: _Validator, receipts: Any, now: datetime | None) -> None:
    values = validator.require_list(receipts, "receipts")
    if values is None:
        return
    required = {
        "receipt_id",
        "intention_ref",
        "action_ref",
        "status",
        "started_at",
        "finished_at",
        "result_evidence_refs",
        "post_action_observation_refs",
    }
    for index, item in enumerate(values):
        path = f"receipts[{index}]"
        value = validator.require_mapping(item, path)
        if value is None:
            continue
        validator.fields(value, required, required, path)
        for field in ("receipt_id", "intention_ref", "action_ref"):
            if field in value:
                validator.string(value[field], f"{path}.{field}")
        if value.get("status") not in ("completed", "failed", "uncertain", "blocked"):
            validator.add(f"{path}.status", "invalid_receipt_status", "unknown receipt status")
        started = validator.timestamp(value.get("started_at"), f"{path}.started_at")
        finished = validator.timestamp(value.get("finished_at"), f"{path}.finished_at")
        if started is not None and finished is not None and finished < started:
            validator.add(f"{path}.finished_at", "invalid_receipt_window", "receipt finishes before it starts")
        if finished is not None and now is not None and finished > now:
            validator.add(f"{path}.finished_at", "future_receipt", "receipt finishes after packet now")
        for field in ("result_evidence_refs", "post_action_observation_refs"):
            refs = validator.require_list(value.get(field), f"{path}.{field}")
            if refs is not None:
                if field == "result_evidence_refs" and not refs:
                    validator.add(f"{path}.{field}", "empty_evidence_refs", "receipt requires result evidence")
                for item_index, ref in enumerate(refs):
                    validator.string(ref, f"{path}.{field}[{item_index}]")


def _validate_unique_ids(validator: _Validator, packet: dict[str, Any]) -> None:
    collections = (
        ("assertions", "assertion_id"),
        ("observations", "observation_id"),
        ("action_contracts", "action_id"),
        ("authority_grants", "grant_id"),
        ("receipts", "receipt_id"),
    )
    for collection, field in collections:
        seen: set[str] = set()
        values = packet.get(collection)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict) or not isinstance(item.get(field), str):
                continue
            value = item[field]
            if value in seen:
                validator.add(f"{collection}[{index}].{field}", "duplicate_id", f"duplicate id: {value}")
            seen.add(value)


def _validate_packet(packet: Any, evaluation_time: datetime | None) -> tuple[dict[str, Any], datetime]:
    validator = _Validator()
    if type(evaluation_time) is not datetime or evaluation_time.tzinfo is not timezone.utc:
        validator.add(
            "evaluation_time",
            "invalid_trusted_evaluation_time",
            "must be an out-of-band exact UTC datetime",
        )
        validator.finish()
    assert evaluation_time is not None
    now = evaluation_time
    value = validator.require_mapping(packet, "packet")
    if value is None:
        validator.finish()
        raise AssertionError("unreachable")
    _validate_json_value(validator, value, "packet")
    # Do not semantically traverse Python-only container/scalar subclasses after
    # the typed-JSON gate has rejected them; their methods may be hostile.
    validator.finish()
    required = {
        "schema",
        "intent_id",
        "now",
        "assertions",
        "parent_goal",
        "goal",
        "observations",
        "action_contracts",
        "authority_grants",
        "receipts",
    }
    validator.fields(value, required, required, "packet")
    if value.get("schema") != INPUT_SCHEMA:
        validator.add("packet.schema", "invalid_schema", f"schema must be {INPUT_SCHEMA}")
    validator.string(value.get("intent_id"), "packet.intent_id")
    validator.timestamp(value.get("now"), "packet.now")
    _validate_assertions(validator, value.get("assertions"), now)
    goal = _validate_goal(validator, value.get("goal"), "goal")
    _validate_parent_goal(validator, goal, value.get("parent_goal"))
    _validate_observations(validator, value.get("observations"), now, goal)
    _validate_actions(validator, value.get("action_contracts"), goal)
    _validate_grants(validator, value.get("authority_grants"), now)
    _validate_receipts(validator, value.get("receipts"), now)
    _validate_unique_ids(validator, value)
    validator.finish()
    assert now is not None
    return value, now


def cognitive_schema() -> dict[str, Any]:
    """Return the stable read-only cognitive/reconciliation contract."""
    return {
        "ok": True,
        "schema": CONTRACT_SCHEMA,
        "input_schema": INPUT_SCHEMA,
        "result_schema": RESULT_SCHEMA,
        "object_types": [
            "SemanticAssertion",
            "GoalSpec",
            "Observation",
            "GoalGap",
            "ActionContract",
            "ActionCandidate",
            "Intention",
            "AuthorityGrant",
            "ExecutionReceipt",
            "ReconciliationResult",
        ],
        "states": [
            "converged",
            "needs_observation",
            "candidate",
            "waiting_authority",
            "eligible_for_executor",
            "blocked",
            "uncertain",
        ],
        "invariants": [
            "authority_effect_must_be_none",
            "candidate_creation_has_no_execution_effect",
            "grant_match_is_projection_only",
            "fresh_reobservation_required_for_convergence",
            "native_owners_remain_authoritative",
            "packet_local_trust_claims_are_ignored",
            "trusted_rows_are_bound_to_canonical_exact_content",
            "trusted_receipt_and_readback_bindings_are_out_of_band",
            "identical_packet_and_trusted_context_produce_identical_output",
        ],
        "boundary": dict(_BOUNDARY),
    }


def validation_error_payload(error: ReconciliationValidationError) -> dict[str, Any]:
    return {
        "ok": False,
        "schema": RESULT_SCHEMA,
        "state": "blocked",
        "findings": error.findings,
        "boundary": dict(_BOUNDARY),
    }


def _observation_candidates(
    packet: dict[str, Any],
    goal: dict[str, Any],
    now: datetime,
    trusted_observation_bindings: frozenset[str],
) -> list[tuple[datetime, dict[str, Any]]]:
    matches: list[tuple[datetime, dict[str, Any]]] = []
    allowed_sources = set(goal["observation_sources"])
    max_age = float(goal["max_observation_age_seconds"])
    for observation in packet["observations"]:
        if not _row_is_trusted("Observation", observation, trusted_observation_bindings):
            continue
        if observation["subject_ref"] != goal["subject_ref"] or observation["predicate"] != goal["predicate"]:
            continue
        if observation["source_ref"] not in allowed_sources:
            continue
        observed_at = datetime.fromisoformat(observation["observed_at"].replace("Z", "+00:00"))
        age = (now - observed_at).total_seconds()
        if 0 <= age <= max_age:
            matches.append((observed_at, observation))
    matches.sort(key=lambda item: (item[0], item[1]["observation_id"]), reverse=True)
    return matches


def _is_converged(goal: dict[str, Any], observed: Any) -> bool:
    desired = goal["desired_value"]
    tolerance = float(goal["tolerance"] or 0)
    operator = goal["operator"]
    if operator == "eq":
        if isinstance(desired, (int, float)) and not isinstance(desired, bool) and isinstance(observed, (int, float)) and not isinstance(observed, bool):
            return abs(float(observed) - float(desired)) <= tolerance
        return observed == desired
    if operator == "ne":
        return observed != desired
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        return False
    if operator == "gte":
        return float(observed) >= float(desired) - tolerance
    return float(observed) <= float(desired) + tolerance


def _gap(goal: dict[str, Any], observation: dict[str, Any] | None) -> dict[str, Any]:
    if observation is None:
        gap_type = "missing_observation"
        observed = None
        severity = 1.0
        evidence_refs: list[str] = []
        why_now = "No fresh allowed observation exists; obtain read-only evidence before planning a mutation."
    else:
        observed = observation["value"]
        desired = goal["desired_value"]
        if goal["operator"] == "gte":
            gap_type = "below_minimum"
            severity = min(1.0, max(0.0, (float(desired) - float(observed)) / max(abs(float(desired)), 1.0)))
        elif goal["operator"] == "lte":
            gap_type = "above_maximum"
            severity = min(1.0, max(0.0, (float(observed) - float(desired)) / max(abs(float(desired)), 1.0)))
        else:
            gap_type = "mismatch"
            severity = 1.0
        evidence_refs = [observation["provenance_ref"]]
        why_now = f"Fresh observation {observation['observation_id']} deviates from goal {goal['goal_id']} revision {goal['revision']}."
    return {
        "gap_id": _stable_id("gap", goal["goal_id"], goal["revision"], goal["predicate"], observed, goal["desired_value"]),
        "goal_ref": goal["goal_id"],
        "expected": goal["desired_value"],
        "observed": observed,
        "gap_type": gap_type,
        "severity": round(severity, 6),
        "confidence": 0.0 if observation is None else 1.0,
        "evidence_refs": evidence_refs,
        "why_now": why_now,
    }


def _assertion_is_current(assertion: dict[str, Any], now: datetime) -> bool:
    if assertion["valid_from"] is not None:
        valid_from = datetime.fromisoformat(assertion["valid_from"].replace("Z", "+00:00"))
        if valid_from > now:
            return False
    if assertion["valid_until"] is not None:
        valid_until = datetime.fromisoformat(assertion["valid_until"].replace("Z", "+00:00"))
        if valid_until < now:
            return False
    return True


def _trusted_preference_source(assertion: dict[str, Any]) -> bool:
    owner = assertion["source_owner"]
    source_ref = assertion["source_ref"]
    trusted_prefixes = {
        "user_profile": ("memory:user:", "user_profile:"),
        "project_profile": ("project_profile:", "profile:project:"),
        "skill_workflow": ("skill:", "workflow:"),
    }
    prefixes = trusted_prefixes.get(owner)
    if prefixes is None or not source_ref.startswith(prefixes):
        return False
    if assertion["epistemic_status"] == "explicit" and assertion["asserted_by"] == "user":
        return owner == "user_profile"
    return assertion["epistemic_status"] == "derived" and assertion["asserted_by"] == "deterministic_rule"


def _preference_bonus(
    packet: dict[str, Any],
    goal: dict[str, Any],
    capability_ref: str,
    now: datetime,
    trusted_preference_assertion_bindings: frozenset[str],
) -> float:
    for assertion in packet["assertions"]:
        if not _row_is_trusted("SemanticAssertion", assertion, trusted_preference_assertion_bindings):
            continue
        if not _trusted_preference_source(assertion):
            continue
        if assertion["predicate"] != "decision.prefer_capability" or assertion["object"] != capability_ref:
            continue
        if not _scope_is_within(goal["scope"], assertion["scope"]):
            continue
        if not _assertion_is_current(assertion, now):
            continue
        return 0.1
    return 0.0


def _selection_context_digest(candidates: list[dict[str, Any]]) -> str:
    return _stable_id("selection-context", [candidate["candidate_id"] for candidate in candidates])


def _intention_id(candidate_id: str, intent_id: str, selection_context_digest: str) -> str:
    return _stable_id("intention", intent_id, candidate_id, selection_context_digest)


def _exact_intention_receipts(
    packet: dict[str, Any],
    intention_id: str,
    action_ref: str,
    trusted_receipt_bindings: frozenset[str],
) -> list[dict[str, Any]]:
    return [
        receipt
        for receipt in packet["receipts"]
        if _row_is_trusted("ExecutionReceipt", receipt, trusted_receipt_bindings)
        and receipt["intention_ref"] == intention_id
        and receipt["action_ref"] == action_ref
    ]


def _authority_status(
    action: dict[str, Any],
    goal: dict[str, Any],
    grants: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    intention_id: str,
    now: datetime,
    trusted_grant_bindings: frozenset[str],
    trusted_receipt_bindings: frozenset[str],
) -> str:
    exact_receipts = _exact_intention_receipts(
        {"receipts": receipts}, intention_id, action["action_id"], trusted_receipt_bindings
    )
    used_executions = len(exact_receipts)
    used_cost = used_executions * float(action["estimated_cost_units"])
    next_cost = used_cost + float(action["estimated_cost_units"])
    if int(goal["budget"]["executions"]) < used_executions + 1 or float(goal["budget"]["cost_units"]) < next_cost:
        return "goal_budget_exhausted"
    if exact_receipts:
        latest_finished = max(
            datetime.fromisoformat(receipt["finished_at"].replace("Z", "+00:00"))
            for receipt in exact_receipts
        )
        if (now - latest_finished).total_seconds() < int(goal["cooldown_seconds"]):
            return "cooldown"
    if action["required_authority"] == "none" and _SIDE_EFFECTS[action["side_effect_level"]] <= 1:
        return "not_required_read_only"
    related = [grant for grant in grants if grant["capability_ref"] == action["capability_ref"]]
    if not related:
        return "missing"
    statuses: list[str] = []
    for grant in sorted(related, key=lambda item: item["grant_id"]):
        if grant["status"] == "revoked":
            statuses.append("revoked")
            continue
        expires = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
        if expires <= now:
            statuses.append("expired")
            continue
        if not _row_is_trusted("AuthorityGrant", grant, trusted_grant_bindings):
            statuses.append("untrusted_projection")
            continue
        if not grant["provenance_ref"].startswith("authority_ledger:"):
            statuses.append("untrusted_provenance")
            continue
        if (
            grant["target_ref"] != action["target_ref"]
            or grant["side_effect_level"] != action["side_effect_level"]
            or grant["scope"] != goal["scope"]
        ):
            statuses.append("non_exact")
            continue
        if (
            int(grant["budget"]["executions"]) < used_executions + 1
            or float(grant["budget"]["cost_units"]) < next_cost
        ):
            statuses.append("under_budget")
            continue
        return "matched_trusted_projection"
    precedence = [
        "untrusted_projection",
        "untrusted_provenance",
        "under_budget",
        "non_exact",
        "expired",
        "revoked",
    ]
    return next((status for status in precedence if status in statuses), "non_exact")


def _candidate_id(goal: dict[str, Any], action: dict[str, Any]) -> str:
    return _stable_id(
        "candidate",
        goal["goal_id"],
        goal["revision"],
        goal["predicate"],
        action,
    )


def _build_candidates(
    packet: dict[str, Any],
    goal: dict[str, Any],
    gap: dict[str, Any],
    now: datetime,
    trusted_preference_assertion_bindings: frozenset[str],
    trusted_grant_bindings: frozenset[str],
    trusted_receipt_bindings: frozenset[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    actions_by_id = {action["action_id"]: action for action in packet["action_contracts"]}
    cost_budget = max(float(goal["budget"]["cost_units"]), 1.0)
    for action in packet["action_contracts"]:
        if goal["predicate"] not in action["serves_predicates"]:
            continue
        candidate_id = _candidate_id(goal, action)
        action_contract_digest = _stable_id("action-contract", action)
        score = (
            float(gap["severity"]) * _PRIORITIES[goal["priority"]] * float(gap["confidence"])
            - _RISK_PENALTIES[action["risk_class"]]
            - min(1.0, float(action["estimated_cost_units"]) / cost_budget)
            + _preference_bonus(
                packet,
                goal,
                action["capability_ref"],
                now,
                trusted_preference_assertion_bindings,
            )
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "action_contract_digest": action_contract_digest,
                "goal_ref": goal["goal_id"],
                "gap_ref": gap["gap_id"],
                "action_ref": action["action_id"],
                "capability_ref": action["capability_ref"],
                "target_ref": action["target_ref"],
                "score": round(score, 6),
                "risk_class": action["risk_class"],
                "side_effect_level": action["side_effect_level"],
                "required_authority": action["required_authority"],
                "evidence_refs": list(gap["evidence_refs"]),
                "rollback_method": action["rollback_method"],
                "readback_method": action["readback_method"],
                "authority_status": "pending_projection",
                "may_execute": False,
            }
        )
    candidates.sort(key=lambda item: (-item["score"], item["action_ref"]))
    selection_context_digest = _selection_context_digest(candidates)
    for candidate in candidates:
        action = actions_by_id[candidate["action_ref"]]
        intention_id = _intention_id(
            candidate["candidate_id"], packet["intent_id"], selection_context_digest
        )
        candidate["authority_status"] = _authority_status(
            action,
            goal,
            packet["authority_grants"],
            packet["receipts"],
            intention_id,
            now,
            trusted_grant_bindings,
            trusted_receipt_bindings,
        )
    return candidates


def _promote_intention(
    candidate: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    packet: dict[str, Any],
    evaluation_time: datetime,
) -> dict[str, Any] | None:
    if candidate is None:
        return None
    selection_context_digest = _selection_context_digest(candidates)
    return {
        "intention_id": _intention_id(
            candidate["candidate_id"], packet["intent_id"], selection_context_digest
        ),
        "candidate_ref": candidate["candidate_id"],
        "selection_context_digest": selection_context_digest,
        "goal_ref": candidate["goal_ref"],
        "action_ref": candidate["action_ref"],
        "action_contract_digest": candidate["action_contract_digest"],
        "selected_at": evaluation_time.isoformat(),
        "authority_status": candidate["authority_status"],
        "may_execute": False,
    }


def _selected_intention_action(intention: dict[str, Any] | None) -> dict[str, str]:
    """Return only the promoted Intention, never every candidate."""
    if intention is None:
        return {}
    return {intention["intention_id"]: intention["action_ref"]}


def _receipt_evaluation(
    packet: dict[str, Any],
    candidate_actions: dict[str, str],
    observation_by_id: dict[str, dict[str, Any]],
    now: datetime,
    goal: dict[str, Any],
    trusted_receipt_bindings: frozenset[str],
    trusted_post_action_observation_bindings: frozenset[str],
) -> str:
    relevant = [
        receipt
        for receipt in packet["receipts"]
        if _row_is_trusted("ExecutionReceipt", receipt, trusted_receipt_bindings)
        and candidate_actions.get(receipt["intention_ref"]) == receipt["action_ref"]
    ]
    if not relevant:
        return "none"
    relevant.sort(
        key=lambda item: (
            datetime.fromisoformat(item["finished_at"].replace("Z", "+00:00")),
            item["receipt_id"],
        ),
        reverse=True,
    )
    receipt = relevant[0]
    if receipt["status"] in {"failed", "blocked", "uncertain"}:
        return receipt["status"]
    valid_observations: list[dict[str, Any]] = []
    finished_at = datetime.fromisoformat(receipt["finished_at"].replace("Z", "+00:00"))
    for ref in receipt["post_action_observation_refs"]:
        observation = observation_by_id.get(ref)
        if observation is None or not _row_is_trusted(
            "Observation", observation, trusted_post_action_observation_bindings
        ):
            continue
        if observation["subject_ref"] != goal["subject_ref"] or observation["predicate"] != goal["predicate"]:
            continue
        if observation["source_ref"] not in goal["observation_sources"]:
            continue
        observed_at = datetime.fromisoformat(observation["observed_at"].replace("Z", "+00:00"))
        if observed_at <= finished_at:
            continue
        if 0 <= (now - observed_at).total_seconds() <= float(goal["max_observation_age_seconds"]):
            valid_observations.append(observation)
    if any(_is_converged(goal, observation["value"]) for observation in valid_observations):
        return "reobserved_converged"
    if valid_observations:
        return "reobserved_gap"
    return "uncertain_without_readback"


def _result_base(packet: dict[str, Any], goal: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": RESULT_SCHEMA,
        "intent_id": packet["intent_id"],
        "goal_ref": goal["goal_id"],
    }


def reconcile_packet(
    packet: Any,
    *,
    evaluation_time: datetime | None = None,
    trusted_observation_bindings: frozenset[str] = frozenset(),
    trusted_preference_assertion_bindings: frozenset[str] = frozenset(),
    trusted_grant_bindings: frozenset[str] = frozenset(),
    trusted_receipt_bindings: frozenset[str] = frozenset(),
    trusted_post_action_observation_bindings: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Validate and deterministically reconcile one explicit packet.

    Evaluation time and native-owner trust identifiers are out-of-band inputs;
    packet-local claims cannot establish freshness, Observation, preference, Grant,
    Receipt, or post-action readback trust. The returned object is a projection. No
    filesystem, runtime, authority, or capability side effect is performed.
    """
    value, now = _validate_packet(packet, evaluation_time)
    goal = value["goal"]
    base = _result_base(value, goal)
    matches = _observation_candidates(value, goal, now, trusted_observation_bindings)
    selected = matches[0][1] if matches else None

    if selected is None:
        gap = _gap(goal, None)
        return {
            **base,
            "state": "needs_observation",
            "selected_observation_ref": None,
            "gap": gap,
            "candidates": [],
            "intention": None,
            "receipt_evaluation": "none",
            "why_now": gap["why_now"],
            "boundary": dict(_BOUNDARY),
        }

    if _is_converged(goal, selected["value"]):
        receipt_candidates = _build_candidates(
            value,
            goal,
            {"gap_id": "receipt-evaluation", "severity": 0.0, "confidence": 1.0, "evidence_refs": []},
            now,
            trusted_preference_assertion_bindings,
            trusted_grant_bindings,
            trusted_receipt_bindings,
        )
        prior_intention = _promote_intention(
            receipt_candidates[0] if receipt_candidates else None,
            receipt_candidates,
            value,
            now,
        )
        candidate_actions = _selected_intention_action(prior_intention)
        observation_by_id = {item["observation_id"]: item for item in value["observations"]}
        receipt_evaluation = _receipt_evaluation(
            value,
            candidate_actions,
            observation_by_id,
            now,
            goal,
            trusted_receipt_bindings,
            trusted_post_action_observation_bindings,
        )
        if receipt_evaluation in {"uncertain_without_readback", "uncertain", "reobserved_gap"}:
            converged_state = "uncertain"
            if receipt_evaluation == "reobserved_gap":
                converged_why = "The exact Receipt's trusted post-action readback still shows a goal gap; an unrelated converged observation cannot close that execution outcome."
            else:
                converged_why = "An exact execution receipt lacks a fresh post-action readback; pre-execution convergence evidence cannot close it."
        elif receipt_evaluation in {"failed", "blocked"}:
            converged_state = "blocked"
            converged_why = f"The latest exact execution receipt is {receipt_evaluation}; pre-execution convergence evidence cannot override it."
        else:
            converged_state = "converged"
            converged_why = f"Fresh observation {selected['observation_id']} satisfies goal {goal['goal_id']} revision {goal['revision']}."
        return {
            **base,
            "state": converged_state,
            "selected_observation_ref": selected["observation_id"],
            "gap": None,
            "candidates": [],
            "intention": None,
            "receipt_evaluation": receipt_evaluation,
            "why_now": converged_why,
            "boundary": dict(_BOUNDARY),
        }

    gap = _gap(goal, selected)
    candidates = _build_candidates(
        value,
        goal,
        gap,
        now,
        trusted_preference_assertion_bindings,
        trusted_grant_bindings,
        trusted_receipt_bindings,
    )
    selected_candidate = candidates[0] if candidates else None
    intention = _promote_intention(selected_candidate, candidates, value, now)
    candidate_actions = _selected_intention_action(intention)
    observation_by_id = {item["observation_id"]: item for item in value["observations"]}
    receipt_evaluation = _receipt_evaluation(
        value,
        candidate_actions,
        observation_by_id,
        now,
        goal,
        trusted_receipt_bindings,
        trusted_post_action_observation_bindings,
    )

    if receipt_evaluation in {"uncertain_without_readback", "uncertain"}:
        state = "uncertain"
        why_now = "Execution outcome lacks a fresh, exact post-action observation and remains uncertain."
    elif receipt_evaluation in {"failed", "blocked"}:
        state = "blocked"
        why_now = f"The latest exact execution receipt is {receipt_evaluation}; no further action is selected on this projection surface."
    elif intention is None:
        state = "blocked"
        why_now = "A fresh goal gap exists but no valid action contract serves the predicate."
    elif intention["authority_status"] in {"goal_budget_exhausted", "cooldown"}:
        state = "blocked"
        why_now = f"Intention {intention['intention_id']} is blocked by lifecycle status {intention['authority_status']}."
    elif intention["authority_status"] == "not_required_read_only":
        state = "candidate"
        why_now = f"Intention {intention['intention_id']} is read-only but remains non-executable on this projection surface."
    elif intention["authority_status"] == "matched_trusted_projection":
        state = "eligible_for_executor"
        why_now = f"Intention {intention['intention_id']} has an exact projected grant match and may be handed to a separately governed executor for revalidation."
    else:
        state = "waiting_authority"
        why_now = f"Intention {intention['intention_id']} is blocked by authority status {intention['authority_status']}."

    return {
        **base,
        "state": state,
        "selected_observation_ref": selected["observation_id"],
        "gap": gap,
        "candidates": candidates,
        "intention": intention,
        "receipt_evaluation": receipt_evaluation,
        "why_now": why_now,
        "boundary": dict(_BOUNDARY),
    }
