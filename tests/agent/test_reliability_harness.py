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
        "deploy-proposal",
        "observe",
        "ratchet",
    ]
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
