from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from workflow_kernel.runtime_gate import (
    LOCAL_DRY_RUN_SCOPE,
    build_human_gate_packet,
    compile_local_dry_run_plan,
    validate_human_gate_packet,
)
from workflow_kernel.static_registry import MODULE_NAMES, registry_snapshot


def test_human_gate_packet_validates_for_local_dry_run_scope() -> None:
    packet = build_human_gate_packet(decision_id="unit-test", operator="tester")

    report = validate_human_gate_packet(packet)

    assert report["status"] == "PASS"
    assert report["scope"] == LOCAL_DRY_RUN_SCOPE
    assert report["errors"] == []


def test_runtime_gate_rejects_digest_tampering() -> None:
    packet = build_human_gate_packet(decision_id="unit-test", operator="tester")
    packet["operator"] = "tampered"

    report = validate_human_gate_packet(packet)

    assert report["status"] == "FAIL"
    assert "human gate digest mismatch" in report["errors"]


def test_local_dry_run_plan_has_no_model_adapter_or_external_side_effects() -> None:
    packet = build_human_gate_packet(decision_id="unit-test", operator="tester")

    plan = compile_local_dry_run_plan(packet)

    assert plan["status"] == "READY_LOCAL_DRY_RUN"
    assert plan["runtime_enabled"] is True
    assert plan["write_through_enabled"] is False
    assert plan["external_side_effects_enabled"] is False
    assert plan["model_calls_enabled"] is False
    assert plan["adapter_writes_enabled"] is False
    assert len(plan["actions"]) == len(MODULE_NAMES)
    assert all(action["executor"] == "local_noop_dry_run" for action in plan["actions"])
    assert all(action["model_call"] is False for action in plan["actions"])
    assert all(action["adapter_write"] is False for action in plan["actions"])
    assert all(action["external_side_effect"] is False for action in plan["actions"])


def test_local_dry_run_plan_blocks_if_forbidden_side_effect_flag_enabled() -> None:
    packet = build_human_gate_packet(decision_id="unit-test", operator="tester")
    registry = registry_snapshot()
    registry["safety_flags"] = copy.deepcopy(registry["safety_flags"])
    registry["safety_flags"]["external_side_effects_enabled"] = True

    plan = compile_local_dry_run_plan(packet, registry)

    assert plan["status"] == "BLOCKED"
    assert "forbidden runtime flag enabled: external_side_effects_enabled" in plan["errors"]


def test_runtime_gate_cli_writes_packet_and_plan(tmp_path) -> None:
    script = Path("scripts/workflow_kernel_local_runtime_gate.py")
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--decision-id",
            "unit-test-cli",
            "--operator",
            "tester",
            "--write-report",
            "--out-dir",
            str(tmp_path),
            "--json",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(proc.stdout)

    assert result["plan"]["status"] == "READY_LOCAL_DRY_RUN"
    assert Path(result["artifacts"]["packet"]["path"]).exists()
    assert Path(result["artifacts"]["plan"]["path"]).exists()
    assert len(result["artifacts"]["plan"]["sha256"]) == 64
