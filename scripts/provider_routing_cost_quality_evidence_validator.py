#!/usr/bin/env python3
"""Validate provider-routing cost/quality policy evidence bundles.

This is a documentation/schema-validation helper. It does not mutate runtime
provider routing or taskpool state. The goal is to fail closed when a task or
report claims governed cost/quality routing without explicit policy fields and
observable routing evidence.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ALLOWED_POLICY_MODES = {"quality-first", "balanced", "cost-first"}
_ALLOWED_OUTCOME_STATUSES = {"policy_applied", "policy_missing", "unknown", "unsupported"}
_ALLOWED_DLP_TIERS = {"strict", "standard", "none", "unknown"}
_ALLOWED_DATA_RETENTION = {"deny", "allow", "unknown"}
_BUDGET_KEYS = {"max_usd", "reference_window", "max_input_tokens", "max_output_tokens"}


@dataclass(frozen=True)
class ValidationResult:
    status: str
    ok: bool
    policy_present: bool
    route_observable: bool
    provider: str | None
    missing_policy_fields: list[str]
    missing_outcome_fields: list[str]
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "provider_routing_cost_quality_evidence_validation_v1",
            "status": self.status,
            "ok": self.ok,
            "policy_present": self.policy_present,
            "route_observable": self.route_observable,
            "provider": self.provider,
            "missing_policy_fields": self.missing_policy_fields,
            "missing_outcome_fields": self.missing_outcome_fields,
            "reasons": self.reasons,
        }


def _load_bundle(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        obj = json.load(fh)
    if not isinstance(obj, dict):
        raise SystemExit(f"bundle must be a JSON object: {path}")
    return obj


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def validate_bundle(bundle: dict[str, Any]) -> ValidationResult:
    provider = str(bundle.get("provider") or "").strip() or None
    policy = bundle.get("cost_quality_policy")
    snapshot = bundle.get("routing_preferences_snapshot")
    outcome = bundle.get("routing_outcome")

    policy_present = isinstance(policy, dict) and bool(policy)
    policy_dict: dict[str, Any] = policy if isinstance(policy, dict) else {}
    status = ""
    if isinstance(outcome, dict) and _non_empty_string(outcome.get("status")):
        status = str(outcome.get("status")).strip()
    elif policy_present:
        status = "unknown"
    else:
        status = "policy_missing"

    missing_policy_fields: list[str] = []
    missing_outcome_fields: list[str] = []
    reasons: list[str] = []

    if status not in _ALLOWED_OUTCOME_STATUSES:
        reasons.append(f"invalid_routing_outcome_status:{status or '<empty>'}")

    if not policy_present:
        missing_policy_fields.append("cost_quality_policy")
        reasons.append("missing_cost_quality_policy")
    else:
        mode = policy_dict.get("mode")
        if mode not in _ALLOWED_POLICY_MODES:
            missing_policy_fields.append("mode")
            reasons.append("invalid_policy_mode")

        budget = policy_dict.get("budget")
        if not isinstance(budget, dict):
            missing_policy_fields.append("budget")
            reasons.append("missing_budget_object")
        elif not any(key in budget for key in _BUDGET_KEYS):
            missing_policy_fields.append("budget_bound")
            reasons.append("missing_budget_bound")

        if not _is_list(policy_dict.get("refuse_providers")):
            missing_policy_fields.append("refuse_providers")
        if not _is_list(policy_dict.get("refuse_models")):
            missing_policy_fields.append("refuse_models")
        if not isinstance(policy_dict.get("require_zdr"), bool):
            missing_policy_fields.append("require_zdr")
        if policy_dict.get("dlp_tier") not in _ALLOWED_DLP_TIERS:
            missing_policy_fields.append("dlp_tier")
        if policy_dict.get("data_retention") not in _ALLOWED_DATA_RETENTION:
            missing_policy_fields.append("data_retention")
        if missing_policy_fields and "cost_quality_policy" not in missing_policy_fields:
            reasons.append("incomplete_cost_quality_policy_fields")

        if not isinstance(snapshot, dict):
            missing_policy_fields.append("routing_preferences_snapshot")
            reasons.append("missing_routing_preferences_snapshot")

    if not isinstance(outcome, dict):
        missing_outcome_fields.extend(["routing_outcome", "selection_reason", "observed_via"])
        reasons.append("missing_routing_outcome")
        outcome = {}
    else:
        if not _non_empty_string(outcome.get("selection_reason")):
            missing_outcome_fields.append("selection_reason")
        if not _non_empty_string(outcome.get("observed_via")):
            missing_outcome_fields.append("observed_via")

    selected_provider = outcome.get("selected_provider")
    selected_model = outcome.get("selected_model")
    route_observable = (
        status == "policy_applied"
        and _non_empty_string(selected_provider)
        and _non_empty_string(selected_model)
        and _non_empty_string(outcome.get("observed_via"))
    )

    if status == "policy_applied":
        if not _non_empty_string(selected_provider):
            missing_outcome_fields.append("selected_provider")
        if not _non_empty_string(selected_model):
            missing_outcome_fields.append("selected_model")
        if not route_observable:
            reasons.append("policy_applied_requires_observable_selected_provider_and_model")
    elif status == "unsupported":
        unsupported_fields = outcome.get("unsupported_fields")
        if not _is_list(unsupported_fields) or not unsupported_fields:
            missing_outcome_fields.append("unsupported_fields")
            reasons.append("unsupported_status_requires_unsupported_fields")
    elif status in {"policy_missing", "unknown"}:
        if _non_empty_string(selected_provider) or _non_empty_string(selected_model):
            reasons.append(f"{status}_must_not_claim_selected_route")

    if status != "policy_applied":
        reasons.append(f"fail_closed_status:{status}")

    if not reasons and not missing_policy_fields and not missing_outcome_fields:
        reasons.append("policy_applied_with_observable_routing_evidence")

    ok = status == "policy_applied" and not missing_policy_fields and not missing_outcome_fields and route_observable
    return ValidationResult(
        status=status,
        ok=ok,
        policy_present=policy_present,
        route_observable=route_observable,
        provider=provider,
        missing_policy_fields=missing_policy_fields,
        missing_outcome_fields=missing_outcome_fields,
        reasons=reasons,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_json", type=Path, help="Provider-routing evidence bundle JSON to validate")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    result = validate_bundle(_load_bundle(args.bundle_json))
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("OK" if result.ok else "FAIL")
        print(f"- status={result.status}")
        for reason in result.reasons:
            print(f"- {reason}")
    return 0 if result.ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
