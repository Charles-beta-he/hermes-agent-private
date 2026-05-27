import pytest

from agent.execution_surface_controls import (
    CONTROL_MAP_VERSION,
    EXECUTION_SURFACE_CONTROLS,
    classify_execution_surface_event,
    versioned_control_map,
)


REQUIRED_SURFACES = {
    "browser",
    "file",
    "terminal_process",
    "provider_calls",
    "plugins_skills",
    "cron",
    "taskpool_ingest",
}


def test_versioned_control_map_names_boundaries_evidence_and_failures():
    control_map = versioned_control_map()

    assert control_map["schema"] == CONTROL_MAP_VERSION
    assert REQUIRED_SURFACES <= set(control_map["surfaces"])
    for surface in REQUIRED_SURFACES:
        entry = control_map["surfaces"][surface]
        assert entry["default_boundary"]
        assert entry["evidence_artifact"]
        assert entry["failure_classification"]
        assert EXECUTION_SURFACE_CONTROLS[surface].surface == surface


def test_taskpool_report_generation_requires_structured_evidence():
    allowed = classify_execution_surface_event(
        {
            "surface": "taskpool_ingest",
            "action": "write_report",
            "target": "docs/private/hermes-execution-surface-isolation-control-map-20260527.md",
            "evidence": "artifact:docs/private/hermes-execution-surface-isolation-control-map-20260527.md",
        }
    )
    rejected = classify_execution_surface_event(
        {
            "surface": "taskpool_ingest",
            "action": "write_report",
            "target": "docs/private/no-structured-evidence.md",
            "evidence": "made a report",
        }
    )

    assert allowed["allowed"] is True
    assert allowed["classification"] == "report_generation_allowed"
    assert rejected["allowed"] is False
    assert rejected["classification"] == "evidence_unstructured"


@pytest.mark.parametrize(
    ("event", "classification"),
    [
        (
            {"surface": "file", "action": "write", "target": "/etc/passwd"},
            "sensitive_path_policy",
        ),
        (
            {
                "surface": "plugins_skills",
                "action": "credential_mount",
                "target": "../../.ssh/id_rsa",
            },
            "credential_path_rejected",
        ),
        (
            {"surface": "browser", "action": "navigate", "target": "file:///tmp/secret.txt"},
            "policy_blocked",
        ),
    ],
)
def test_out_of_scope_file_plugin_and_browser_access_fail_closed(event, classification):
    result = classify_execution_surface_event(event)

    assert result["allowed"] is False
    assert result["classification"] == classification
