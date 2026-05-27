"""Hermes execution-surface isolation control map.

This module is deliberately data-first: it names the default boundary,
required evidence artifact, and failure classification for each high-risk
execution surface that taskpool automation can touch.  Runtime enforcement
continues to live in the existing tool modules; this map gives taskpool and
review flows a stable contract to cite and test instead of relying on prose
scattered across skills or task notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


CONTROL_MAP_VERSION = "hermes_execution_surface_controls_v1"


@dataclass(frozen=True)
class SurfaceControl:
    """Fail-closed contract for one Hermes execution surface."""

    surface: str
    default_boundary: str
    evidence_artifact: str
    failure_classification: str


_EXECUTION_SURFACE_CONTROLS: tuple[SurfaceControl, ...] = (
    SurfaceControl(
        surface="browser",
        default_boundary="http/https navigation only; local file schemes and bare filesystem paths are policy-blocked before browser launch",
        evidence_artifact="browser_replay_bundle_v1 with browser_qa_validation_v1 and browser_proof_log_entry",
        failure_classification="policy_blocked | degraded_evidence | validation_success",
    ),
    SurfaceControl(
        surface="file",
        default_boundary="project/workspace files are allowed; credential, device, cross-profile, and sensitive system paths fail closed or require explicit approval path",
        evidence_artifact="tool result plus write-deny/sensitive-path classification and read-back when modifying project artifacts",
        failure_classification="write_denied | sensitive_path_policy | cross_profile_guard | binary_or_oversize_read_guard",
    ),
    SurfaceControl(
        surface="terminal_process",
        default_boundary="commands run in resolved workdir/backend with approval gates for dangerous operations; background jobs require durable process tracking",
        evidence_artifact="terminal command, exit code, stdout/stderr excerpt, and process registry/session id for background work",
        failure_classification="approval_required | command_failed | timeout | process_not_tracked",
    ),
    SurfaceControl(
        surface="provider_calls",
        default_boundary="provider/model resolution must use configured router metadata and credential availability without leaking secrets into prompts or logs",
        evidence_artifact="provider probe/router status with redacted credentials and model/provider id",
        failure_classification="provider_unavailable | auth_missing | quorum_degraded | truth_gate_unsupported",
    ),
    SurfaceControl(
        surface="plugins_skills",
        default_boundary="skills/plugins may request only profile-scoped, relative credential files under HERMES_HOME; absolute paths and traversal are rejected",
        evidence_artifact="skill/plugin manifest audit plus credential-file mount decision",
        failure_classification="credential_path_rejected | plugin_disabled | manifest_invalid",
    ),
    SurfaceControl(
        surface="cron",
        default_boundary="cron jobs execute with declared delivery/toolsets/workdir; no_agent scripts use filename-only script references and logged outcomes",
        evidence_artifact="cron job row, scheduler log/output artifact, and last_run/last_status",
        failure_classification="missed_schedule | script_path_invalid | delivery_failed | no_agent_guard_failed",
    ),
    SurfaceControl(
        surface="taskpool_ingest",
        default_boundary="task candidates must carry lane/priority/dependency/proof metadata; advisory multimodel fields are normalized before dispatch",
        evidence_artifact="task note frontmatter, add/transition command evidence, selfcheck/proof-check/next read-back",
        failure_classification="mission_envelope_missing | evidence_unstructured | autorun_disabled | closeout_evidence_missing",
    ),
)


EXECUTION_SURFACE_CONTROLS: dict[str, SurfaceControl] = {
    control.surface: control for control in _EXECUTION_SURFACE_CONTROLS
}


_ALLOWED_REPORT_ACTIONS = {"write_report", "add_candidate", "record_evidence", "read_back"}
_SENSITIVE_PREFIXES = ("/etc/", "/private/etc/", "/boot/", "/usr/lib/systemd/")
_SENSITIVE_EXACT = {"/var/run/docker.sock", "/run/docker.sock"}


def versioned_control_map() -> dict[str, Any]:
    """Return a serializable copy of the current execution-surface map."""

    return {
        "schema": CONTROL_MAP_VERSION,
        "surfaces": {
            name: {
                "default_boundary": control.default_boundary,
                "evidence_artifact": control.evidence_artifact,
                "failure_classification": control.failure_classification,
            }
            for name, control in EXECUTION_SURFACE_CONTROLS.items()
        },
    }


def classify_execution_surface_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a smoke-check event against the control map.

    The classifier is intentionally narrow.  It provides deterministic evidence
    fixtures for taskpool review without replacing the deeper runtime guards in
    ``tools.file_tools``, ``tools.credential_files``, browser policy checks, or
    cron execution.
    """

    surface = str(event.get("surface") or "").strip()
    action = str(event.get("action") or "").strip()
    target = str(event.get("target") or "").strip()

    if surface not in EXECUTION_SURFACE_CONTROLS:
        return _result(False, surface, "unknown_surface", "surface is not present in the versioned control map")

    if surface == "taskpool_ingest" and action in _ALLOWED_REPORT_ACTIONS:
        evidence = str(event.get("evidence") or "")
        if evidence.startswith(("artifact:", "proof:", "command:")):
            return _result(True, surface, "report_generation_allowed", "structured evidence is present")
        return _result(False, surface, "evidence_unstructured", "taskpool ingest requires structured evidence prefixes")

    if surface == "file" and action in {"write", "patch"}:
        if target in _SENSITIVE_EXACT or target.startswith(_SENSITIVE_PREFIXES):
            return _result(False, surface, "sensitive_path_policy", "sensitive system path rejected")
        return _result(True, surface, "workspace_write_allowed", "target is not in the sensitive-path fixture set")

    if surface == "plugins_skills" and action == "credential_mount":
        path = PurePosixPath(target)
        if target.startswith("/"):
            return _result(False, surface, "credential_path_rejected", "absolute credential paths are rejected")
        if ".." in path.parts:
            return _result(False, surface, "credential_path_rejected", "credential path traversal is rejected")
        return _result(True, surface, "credential_mount_allowed", "relative HERMES_HOME credential path accepted")

    if surface == "browser" and action == "navigate":
        if target.startswith(("file://", "/")):
            return _result(False, surface, "policy_blocked", "local filesystem browser target rejected")
        return _result(True, surface, "browser_navigation_allowed", "non-local browser target accepted for downstream website policy")

    return _result(False, surface, "unsupported_fixture", "event shape is not covered by this smoke classifier")


def _result(allowed: bool, surface: str, classification: str, reason: str) -> dict[str, Any]:
    return {
        "schema": CONTROL_MAP_VERSION,
        "surface": surface,
        "allowed": allowed,
        "classification": classification,
        "reason": reason,
    }
