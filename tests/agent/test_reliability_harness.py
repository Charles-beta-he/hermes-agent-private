import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.reliability_harness import (
    CaseRegistry,
    EvalHarness,
    FeedbackLedger,
    LifecycleContractRegistry,
    RegressionDB,
    SandboxPolicy,
    ShadowWorkflowRouter,
    ToolCodeGrader,
)


def _write_case(root: Path, case: dict) -> Path:
    path = root / f"{case['id']}.json"
    path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    return path


def test_case_registry_loads_schema_and_computes_stable_hash(tmp_path):
    case_path = _write_case(
        tmp_path,
        {
            "id": "media-summary-pass",
            "input": "分析这个视频价值",
            "workflow": "research",
            "risk": "low",
            "allowed_tools": ["read_file"],
            "forbidden_side_effects": ["send_message", "memory_write"],
            "expected_artifacts": ["summary.md"],
            "acceptance": {"contains": ["价值", "证据"]},
            "graders": ["tool_code"],
        },
    )

    registry = CaseRegistry(tmp_path)
    loaded = registry.load("media-summary-pass")

    assert loaded.id == "media-summary-pass"
    assert loaded.workflow == "research"
    assert loaded.case_hash == registry.load_file(case_path).case_hash
    assert loaded.forbidden_side_effects == ["send_message", "memory_write"]


def test_sandbox_policy_blocks_real_side_effect_tools_by_default():
    policy = SandboxPolicy.default()

    assert policy.is_allowed_tool("read_file") is True
    assert policy.is_allowed_tool("terminal") is True
    assert policy.is_allowed_tool("send_message") is False
    assert policy.is_allowed_tool("memory") is False
    assert policy.is_allowed_tool("cronjob") is False


def test_lifecycle_contract_registry_models_skill_packs_and_commands():
    registry = LifecycleContractRegistry.default()
    manifest = registry.as_dict()

    assert manifest["schema"] == "hermes_agent_lifecycle_contract_v1"
    assert [pack["id"] for pack in manifest["skill_packs"]] == [
        "workflow",
        "implementation",
        "evaluation",
        "delivery",
        "edge_runtime",
        "observability",
        "governance",
        "experience_ratchet",
    ]
    assert [command["id"] for command in manifest["commands"]] == [
        "intake",
        "scaffold",
        "implement",
        "eval-generate",
        "eval-grade",
        "edge-profile",
        "edge-benchmark",
        "edge-verify",
        "edge-activation-packet",
        "edge-kill-switch-dry-run",
        "edge-status-readback",
        "edge-open-boundary-checklist",
        "edge-authority-discover",
        "edge-activation-preflight",
        "deploy-proposal",
        "observe",
        "ratchet",
    ]
    assert [profile["id"] for profile in manifest["edge_runtime_profiles"]] == ["local_edge_worker"]
    edge_profile = registry.edge_profile("local_edge_worker")
    assert edge_profile.side_effect_level == "local_write"
    assert "external dispatch" in edge_profile.forbidden_by_default
    assert "memory_rss_mb" in edge_profile.verifies
    edge_command = registry.command("edge-profile")
    assert "edge_runtime" in edge_command.requires
    assert "no device control is enabled" in edge_command.verifies
    deploy = registry.command("deploy-proposal")
    assert deploy.side_effect_level == "local_write"
    assert "external deploy/publish remains human-gated" in deploy.authority_boundary
    assert "evaluation" in deploy.requires


def test_shadow_router_classifies_without_enforcement():
    router = ShadowWorkflowRouter(mode="shadow")

    direct = router.route("现在几点")
    research = router.route("请查证最近稳定币监管新闻并给出证据")
    objective = router.route("全盘推进生产级改造，直到验证和commit")

    assert direct.workflow == "direct"
    assert research.workflow == "research"
    assert objective.workflow == "objective_gated"
    assert objective.enforced is False
    assert 0 <= objective.confidence <= 1


def test_limited_enforce_only_marks_low_risk_high_confidence_routes():
    router = ShadowWorkflowRouter(mode="limited-enforce")

    research = router.route("请查证最近稳定币监管新闻并给出证据")
    objective = router.route("全盘推进生产级改造，直到验证和commit")

    assert research.workflow == "research"
    assert research.enforced is True
    assert objective.workflow == "objective_gated"
    assert objective.enforced is False


def test_tool_code_grader_prefers_deterministic_evidence(tmp_path):
    artifact = tmp_path / "summary.md"
    artifact.write_text("价值: 高\n证据: tool output\n", encoding="utf-8")
    case = CaseRegistry.from_dict(
        {
            "id": "artifact-pass",
            "input": "write artifact",
            "workflow": "direct",
            "risk": "low",
            "expected_artifacts": ["summary.md"],
            "acceptance": {"contains": ["价值", "证据"]},
            "graders": ["tool_code"],
        }
    )

    verdict = ToolCodeGrader().grade(case, tmp_path, response_text="done", tool_events=[{"tool": "write_file"}])

    assert verdict.passed is True
    assert verdict.grader == "tool_code"
    assert any("summary.md" in ref for ref in verdict.evidence_refs)


def test_eval_harness_writes_run_artifacts_regression_db_and_feedback(tmp_path):
    cases_dir = tmp_path / "cases"
    runs_dir = tmp_path / "runs"
    cases_dir.mkdir()
    _write_case(
        cases_dir,
        {
            "id": "broken-missing-artifact",
            "input": "produce summary artifact",
            "workflow": "plan",
            "risk": "medium",
            "expected_artifacts": ["summary.md"],
            "acceptance": {"contains": ["done"]},
            "graders": ["tool_code"],
        },
    )

    harness = EvalHarness(cases_dir=cases_dir, runs_dir=runs_dir, db_path=tmp_path / "regression.sqlite")
    result = harness.run_case("broken-missing-artifact", response_text="done", produced_artifacts={})

    assert result.passed is False
    assert result.route.workflow == "plan"
    assert result.trajectory_path.exists()
    assert result.report_path.exists()
    assert (runs_dir / "dashboard.md").exists()
    assert "missing_artifact" in result.feedback_items[0]["failure_type"]

    db = RegressionDB(tmp_path / "regression.sqlite")
    history = db.history("broken-missing-artifact")
    assert len(history) == 1
    assert history[0]["passed"] is False

    ledger = FeedbackLedger(runs_dir / "feedback.jsonl")
    items = ledger.read_all()
    assert items[0]["landing_zone"] == "regression_case_or_fix"


def test_module_cli_dry_run_outputs_json(tmp_path):
    cases_dir = tmp_path / "cases"
    runs_dir = tmp_path / "runs"
    cases_dir.mkdir()
    _write_case(
        cases_dir,
        {
            "id": "cli-pass",
            "input": "simple answer",
            "workflow": "direct",
            "risk": "low",
            "expected_artifacts": [],
            "acceptance": {"contains": ["OK"]},
            "graders": ["tool_code"],
        },
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "run",
            "--cases-dir",
            str(cases_dir),
            "--runs-dir",
            str(runs_dir),
            "--case",
            "cli-pass",
            "--response-text",
            "OK",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["case_id"] == "cli-pass"
    assert payload["passed"] is True
    assert Path(payload["report_path"]).exists()


def test_eval_harness_trajectory_includes_edge_runtime_profile_ids(tmp_path):
    cases_dir = tmp_path / "cases"
    runs_dir = tmp_path / "runs"
    cases_dir.mkdir()
    _write_case(
        cases_dir,
        {
            "id": "edge-trajectory-pass",
            "input": "summarize local edge worker readiness",
            "workflow": "direct",
            "risk": "low",
            "expected_artifacts": [],
            "acceptance": {"contains": ["OK"]},
            "graders": ["tool_code"],
        },
    )

    harness = EvalHarness(cases_dir=cases_dir, runs_dir=runs_dir, db_path=tmp_path / "regression.sqlite")
    result = harness.run_case("edge-trajectory-pass", response_text="OK")

    trajectory = json.loads(result.trajectory_path.read_text(encoding="utf-8"))
    assert trajectory["lifecycle_contract"]["edge_runtime_profile_ids"] == ["local_edge_worker"]


def test_module_cli_capabilities_outputs_lifecycle_manifest():
    proc = subprocess.run(
        [sys.executable, "-m", "agent.reliability_harness", "capabilities", "--json"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hermes_agent_lifecycle_contract_v1"
    assert any(pack["id"] == "evaluation" for pack in payload["skill_packs"])
    assert any(command["id"] == "eval-grade" for command in payload["commands"])


def test_module_cli_edge_benchmark_writes_local_receipt(tmp_path):
    runs_dir = tmp_path / "edge-runs"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-benchmark",
            "--runs-dir",
            str(runs_dir),
            "--profile",
            "local_edge_worker",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    receipt_path = Path(payload["receipt_path"])
    assert receipt_path.exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "hermes_edge_benchmark_receipt_v1"
    assert receipt["profile_id"] == "local_edge_worker"
    assert receipt["runtime_effect"] == "local_write_receipt_only"
    assert receipt["measurements"]["memory_rss_mb"] >= 0
    assert receipt["measurements"]["boot_ms"] >= 0
    assert receipt["gates"]["device_control"] == "closed"
    assert receipt["gates"]["external_dispatch"] == "closed"
    assert receipt["gates"]["mcp_write_authority"] == "closed"
    assert receipt["forbidden_by_default"]


def test_module_cli_edge_verify_accepts_closed_gate_receipt(tmp_path):
    runs_dir = tmp_path / "edge-runs"
    benchmark = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-benchmark",
            "--runs-dir",
            str(runs_dir),
            "--profile",
            "local_edge_worker",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert benchmark.returncode == 0, benchmark.stderr
    receipt_path = json.loads(benchmark.stdout)["receipt_path"]

    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-verify",
            "--receipt",
            receipt_path,
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert verify.returncode == 0, verify.stderr
    payload = json.loads(verify.stdout)
    assert payload["schema"] == "hermes_edge_benchmark_verification_v1"
    assert payload["status"] == "pass"
    assert payload["profile_id"] == "local_edge_worker"
    assert payload["runtime_effect"] == "read_only_verification"
    assert payload["promotion_ready"] is False
    assert payload["next_authority_required"] == "exact_runtime_activation_packet"
    assert all(check["status"] == "pass" for check in payload["checks"])


def test_module_cli_edge_verify_blocks_open_side_effect_gate(tmp_path):
    receipt_path = tmp_path / "bad-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_benchmark_receipt_v1",
                "profile_id": "local_edge_worker",
                "runtime_effect": "local_write_receipt_only",
                "measurements": {"memory_rss_mb": 10, "boot_ms": 20},
                "gates": {
                    "device_control": "closed",
                    "external_dispatch": "open",
                    "mcp_write_authority": "closed",
                    "publish_deploy_push": "closed",
                    "destructive_operation": "closed",
                    "credential_entry": "closed",
                },
                "forbidden_by_default": ["external_dispatch"],
            }
        ),
        encoding="utf-8",
    )

    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-verify",
            "--receipt",
            str(receipt_path),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert verify.returncode == 1
    payload = json.loads(verify.stdout)
    assert payload["status"] == "blocked"
    assert payload["promotion_ready"] is False
    assert any(check["name"] == "gate_external_dispatch_closed" and check["status"] == "fail" for check in payload["checks"])


def test_module_cli_edge_activation_packet_writes_non_executing_authority_packet(tmp_path):
    runs_dir = tmp_path / "edge-runs"
    benchmark = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-benchmark",
            "--runs-dir",
            str(runs_dir),
            "--profile",
            "local_edge_worker",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert benchmark.returncode == 0, benchmark.stderr
    receipt_path = json.loads(benchmark.stdout)["receipt_path"]
    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-verify",
            "--receipt",
            receipt_path,
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    verification_path = tmp_path / "verification.json"
    verification_path.write_text(verify.stdout, encoding="utf-8")

    packet = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-activation-packet",
            "--verification",
            str(verification_path),
            "--target-id",
            "local-mac-fixture",
            "--runs-dir",
            str(runs_dir),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert packet.returncode == 0, packet.stderr
    payload = json.loads(packet.stdout)
    packet_path = Path(payload["packet_path"])
    assert packet_path.exists()
    activation = json.loads(packet_path.read_text(encoding="utf-8"))
    assert activation["schema"] == "hermes_edge_activation_packet_v1"
    assert activation["execute"] is False
    assert activation["runtime_effect"] == "local_write_packet_only"
    assert activation["target"]["id"] == "local-mac-fixture"
    assert activation["profile_id"] == "local_edge_worker"
    assert activation["preconditions"]["verification_status"] == "pass"
    assert activation["rollback"]["kill_switch_required"] is True
    assert activation["readback"]["required"] is True
    assert activation["gates_remain_closed"]["device_control"] == "closed"
    assert activation["gates_remain_closed"]["external_dispatch"] == "closed"


def test_module_cli_edge_activation_packet_blocks_failed_verification(tmp_path):
    verification_path = tmp_path / "blocked-verification.json"
    verification_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_benchmark_verification_v1",
                "status": "blocked",
                "profile_id": "local_edge_worker",
                "promotion_ready": False,
                "checks": [{"name": "gate_external_dispatch_closed", "status": "fail", "evidence": "open"}],
            }
        ),
        encoding="utf-8",
    )

    packet = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-activation-packet",
            "--verification",
            str(verification_path),
            "--target-id",
            "local-mac-fixture",
            "--runs-dir",
            str(tmp_path),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert packet.returncode == 1
    payload = json.loads(packet.stdout)
    assert payload["status"] == "blocked"
    assert payload["runtime_effect"] == "read_only_preflight"
    assert payload["reason"] == "verification_not_passed"


def test_module_cli_edge_kill_switch_dry_run_writes_disabled_receipt(tmp_path):
    runs_dir = tmp_path / "edge-runs"
    activation_path = tmp_path / "activation.json"
    activation_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_activation_packet_v1",
                "status": "authorization_required",
                "profile_id": "local_edge_worker",
                "execute": False,
                "runtime_effect": "local_write_packet_only",
                "target": {"id": "local-mac-fixture"},
                "rollback": {"kill_switch_required": True, "rollback_receipt_required": True},
                "readback": {"required": True},
                "gates_remain_closed": {
                    "device_control": "closed",
                    "external_dispatch": "closed",
                    "mcp_write_authority": "closed",
                    "publish_deploy_push": "closed",
                    "destructive_operation": "closed",
                    "credential_entry": "closed",
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-kill-switch-dry-run",
            "--activation-packet",
            str(activation_path),
            "--runs-dir",
            str(runs_dir),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    receipt_path = Path(payload["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "hermes_edge_kill_switch_dry_run_receipt_v1"
    assert receipt["runtime_effect"] == "local_write_receipt_only"
    assert receipt["execute"] is False
    assert receipt["target"]["id"] == "local-mac-fixture"
    assert receipt["status_after_dry_run"] == "disabled"
    assert receipt["rollback_verified"] is True
    assert receipt["readback"]["simulated_status"] == "disabled"
    assert receipt["gates_remain_closed"]["device_control"] == "closed"
    assert receipt["gates_remain_closed"]["external_dispatch"] == "closed"


def test_module_cli_edge_kill_switch_dry_run_blocks_execute_true_packet(tmp_path):
    activation_path = tmp_path / "activation-execute-true.json"
    activation_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_activation_packet_v1",
                "status": "authorization_required",
                "profile_id": "local_edge_worker",
                "execute": True,
                "target": {"id": "local-mac-fixture"},
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-kill-switch-dry-run",
            "--activation-packet",
            str(activation_path),
            "--runs-dir",
            str(tmp_path),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["status"] == "blocked"
    assert payload["runtime_effect"] == "read_only_preflight"
    assert payload["reason"] == "execute_true_not_allowed_for_dry_run"


def _write_edge_ladder_artifacts(tmp_path):
    benchmark = tmp_path / "benchmark.json"
    verification = tmp_path / "verification.json"
    activation = tmp_path / "activation.json"
    kill_switch = tmp_path / "kill-switch.json"
    benchmark.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_benchmark_receipt_v1",
                "profile_id": "local_edge_worker",
                "runtime_effect": "local_write_receipt_only",
                "measurements": {"memory_rss_mb": 42.0, "boot_ms": 12.0},
                "gates": {
                    "device_control": "closed",
                    "external_dispatch": "closed",
                    "mcp_write_authority": "closed",
                    "publish_deploy_push": "closed",
                    "destructive_operation": "closed",
                    "credential_entry": "closed",
                },
            }
        ),
        encoding="utf-8",
    )
    verification.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_benchmark_verification_v1",
                "profile_id": "local_edge_worker",
                "status": "pass",
                "promotion_ready": False,
                "runtime_effect": "read_only_verification",
            }
        ),
        encoding="utf-8",
    )
    activation.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_activation_packet_v1",
                "profile_id": "local_edge_worker",
                "status": "authorization_required",
                "execute": False,
                "runtime_effect": "local_write_packet_only",
                "target": {"id": "local-mac-fixture"},
                "gates_remain_closed": {
                    "device_control": "closed",
                    "external_dispatch": "closed",
                    "mcp_write_authority": "closed",
                    "publish_deploy_push": "closed",
                    "destructive_operation": "closed",
                    "credential_entry": "closed",
                },
            }
        ),
        encoding="utf-8",
    )
    kill_switch.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_kill_switch_dry_run_receipt_v1",
                "profile_id": "local_edge_worker",
                "status": "pass",
                "execute": False,
                "runtime_effect": "local_write_receipt_only",
                "status_after_dry_run": "disabled",
                "rollback_verified": True,
                "readback": {"simulated_status": "disabled"},
                "gates_remain_closed": {
                    "device_control": "closed",
                    "external_dispatch": "closed",
                    "mcp_write_authority": "closed",
                    "publish_deploy_push": "closed",
                    "destructive_operation": "closed",
                    "credential_entry": "closed",
                },
            }
        ),
        encoding="utf-8",
    )
    return benchmark, verification, activation, kill_switch


def test_module_cli_edge_status_readback_summarizes_closed_ladder(tmp_path):
    benchmark, verification, activation, kill_switch = _write_edge_ladder_artifacts(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-status-readback",
            "--benchmark",
            str(benchmark),
            "--verification",
            str(verification),
            "--activation-packet",
            str(activation),
            "--kill-switch-receipt",
            str(kill_switch),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hermes_edge_status_readback_v1"
    assert payload["status"] == "closed_ladder_ready_for_human_authority"
    assert payload["runtime_effect"] == "read_only_status"
    assert payload["execute"] is False
    assert payload["promotion_ready"] is False
    assert payload["next_authority_required"] == "exact_runtime_activation_packet_execute_true"
    assert payload["artifacts"]["benchmark"]["status"] == "present"
    assert payload["artifacts"]["verification"]["status"] == "present"
    assert payload["artifacts"]["activation_packet"]["execute"] is False
    assert payload["artifacts"]["kill_switch_receipt"]["status_after_dry_run"] == "disabled"
    assert payload["closed_gates"]["external_dispatch"] == "closed"


def test_module_cli_edge_open_boundary_checklist_writes_non_executing_checklist(tmp_path):
    benchmark, verification, activation, kill_switch = _write_edge_ladder_artifacts(tmp_path)
    status_out = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-status-readback",
            "--benchmark",
            str(benchmark),
            "--verification",
            str(verification),
            "--activation-packet",
            str(activation),
            "--kill-switch-receipt",
            str(kill_switch),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    status_path = tmp_path / "status.json"
    status_path.write_text(status_out.stdout, encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-open-boundary-checklist",
            "--status-readback",
            str(status_path),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    checklist = json.loads(Path(payload["checklist_path"]).read_text(encoding="utf-8"))
    assert checklist["schema"] == "hermes_edge_open_boundary_checklist_v1"
    assert checklist["status"] == "human_authority_required"
    assert checklist["execute"] is False
    assert checklist["runtime_effect"] == "local_write_checklist_only"
    assert checklist["authority_required"]["exact_target"] is True
    assert checklist["authority_required"]["rollback_restore_path"] is True
    assert checklist["authority_required"]["post_action_readback"] is True
    assert checklist["remaining_blockers"] == ["exact_runtime_activation_packet_execute_true"]
    assert checklist["closed_gates"]["device_control"] == "closed"
    assert checklist["closed_gates"]["external_dispatch"] == "closed"


def test_module_cli_edge_authority_discover_scans_existing_artifacts(tmp_path):
    benchmark, verification, activation, kill_switch = _write_edge_ladder_artifacts(tmp_path)
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_status_readback_v1",
                "status": "closed_ladder_ready_for_human_authority",
                "profile_id": "local_edge_worker",
                "execute": False,
                "promotion_ready": False,
                "next_authority_required": "exact_runtime_activation_packet_execute_true",
                "closed_gates": {"device_control": "closed", "external_dispatch": "closed"},
            }
        ),
        encoding="utf-8",
    )
    checklist_path = tmp_path / "checklist.json"
    checklist_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_open_boundary_checklist_v1",
                "status": "human_authority_required",
                "profile_id": "local_edge_worker",
                "execute": False,
                "remaining_blockers": ["exact_runtime_activation_packet_execute_true"],
                "authority_required": {"exact_target": True, "post_action_readback": True},
                "closed_gates": {"device_control": "closed", "external_dispatch": "closed"},
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-authority-discover",
            "--scan-dir",
            str(tmp_path),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hermes_edge_authority_discovery_v1"
    assert payload["opening_ready"] is False
    assert payload["discovered"]["profiles"] == ["local_edge_worker"]
    assert "local-mac-fixture" in payload["discovered"]["target_candidates"]
    assert payload["discovered"]["execute_true_packets"] == []
    assert "exact_runtime_activation_packet_execute_true" in payload["blockers"]
    assert payload["recommendation"] == "remain_closed_until_execute_true_packet_and_exact_runtime_readback_exist"


def test_module_cli_edge_activation_preflight_blocks_discovery_without_valid_execute_packet(tmp_path):
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_authority_discovery_v1",
                "opening_ready": False,
                "discovered": {
                    "profiles": ["local_edge_worker"],
                    "target_candidates": ["local-mac-fixture"],
                    "execute_true_packets": [str(tmp_path / "bad-execute-true.json")],
                },
                "blockers": ["exact_runtime_activation_packet_execute_true"],
                "recommendation": "remain_closed_until_execute_true_packet_and_exact_runtime_readback_exist",
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-activation-preflight",
            "--discovery",
            str(discovery_path),
            "--target-id",
            "local-mac-fixture",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hermes_edge_activation_preflight_v1"
    assert payload["status"] == "blocked"
    assert payload["runtime_effect"] == "read_only_preflight"
    assert payload["target_id"] == "local-mac-fixture"
    assert payload["reason"] == "discovery_not_opening_ready"
    assert "exact_runtime_activation_packet_execute_true" in payload["blockers"]


def test_module_cli_edge_activation_preflight_passes_only_fully_bound_discovery(tmp_path):
    packet_path = tmp_path / "execute-true.json"
    packet_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_activation_packet_v1",
                "profile_id": "local_edge_worker",
                "target": {"id": "local-mac-fixture"},
                "execute": True,
                "rollback": {"restore_path_required": True, "kill_switch_required": True},
                "readback": {"required": True, "post_action_status_required": True},
                "receipt_path": str(tmp_path / "receipt.json"),
            }
        ),
        encoding="utf-8",
    )
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(
        json.dumps(
            {
                "schema": "hermes_edge_authority_discovery_v1",
                "opening_ready": True,
                "discovered": {
                    "profiles": ["local_edge_worker"],
                    "target_candidates": ["local-mac-fixture"],
                    "execute_true_packets": [str(packet_path)],
                },
                "blockers": [],
                "recommendation": "ready_for_exact_activation_readback",
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.reliability_harness",
            "edge-activation-preflight",
            "--discovery",
            str(discovery_path),
            "--target-id",
            "local-mac-fixture",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hermes_edge_activation_preflight_v1"
    assert payload["status"] == "pass"
    assert payload["runtime_effect"] == "read_only_preflight"
    assert payload["execute"] is False
    assert payload["target_id"] == "local-mac-fixture"
    assert payload["matched_execute_true_packet"] == str(packet_path)
    assert payload["next_step"] == "operator_may_run_separate_exact_executor_boundary"
