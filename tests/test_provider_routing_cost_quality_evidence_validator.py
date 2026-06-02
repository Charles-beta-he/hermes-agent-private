import json
from pathlib import Path

from scripts.provider_routing_cost_quality_evidence_validator import validate_bundle


_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_accepts_policy_applied_bundle_with_observable_route():
    result = validate_bundle(_load("provider_routing_cost_quality_policy_applied.json"))

    assert result.status == "policy_applied"
    assert result.ok is True
    assert result.policy_present is True
    assert result.route_observable is True
    assert result.missing_policy_fields == []
    assert result.missing_outcome_fields == []
    assert result.reasons == ["policy_applied_with_observable_routing_evidence"]


def test_policy_missing_fails_closed_and_is_not_misreported_as_governed():
    result = validate_bundle(_load("provider_routing_cost_quality_policy_missing.json"))

    assert result.status == "policy_missing"
    assert result.ok is False
    assert result.policy_present is False
    assert result.route_observable is False
    assert result.missing_policy_fields == ["cost_quality_policy"]
    assert "missing_cost_quality_policy" in result.reasons
    assert "fail_closed_status:policy_missing" in result.reasons


def test_unknown_route_with_policy_fails_closed_until_observable_route_exists():
    result = validate_bundle(
        {
            "provider": "openrouter",
            "cost_quality_policy": {
                "mode": "balanced",
                "budget": {"max_usd": 1.25, "scope": "task"},
                "refuse_providers": ["Together"],
                "refuse_models": [],
                "require_zdr": True,
                "dlp_tier": "strict",
                "data_retention": "deny",
            },
            "routing_preferences_snapshot": {
                "sort": "price",
                "ignore": ["Together"],
                "data_collection": "deny",
                "require_parameters": True,
            },
            "routing_outcome": {
                "status": "unknown",
                "selected_provider": None,
                "selected_model": None,
                "selection_reason": "Dry-run only had config and budget inputs; no observable routed provider/model headers were available.",
                "observed_via": "config_only",
                "unsupported_fields": [],
                "missing_fields": ["selected_provider", "selected_model"],
            },
        }
    )

    assert result.status == "unknown"
    assert result.ok is False
    assert result.policy_present is True
    assert result.route_observable is False
    assert result.missing_policy_fields == []
    assert result.missing_outcome_fields == []
    assert result.reasons == ["fail_closed_status:unknown"]
