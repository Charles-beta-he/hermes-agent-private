"""Offline Agent Reliability Harness for Hermes.

This module intentionally stays off the live gateway/taskpool hot path.  It
provides the executable scaffolding for Agent Eval + Dynamic Workflow:
case registry, sandbox policy, shadow routing, deterministic grader, feedback
ledger, regression database, and a small CLI for dry/smoke runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sqlite3
import sys
import sysconfig
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_BLOCKED_TOOLS = {
    "send_message",
    "memory",
    "memory_write",
    "cronjob",
    "skill_manage",
    "mcp_filesystem_write_file",
    "mcp_filesystem_edit_file",
}

_MODULE_START = time.perf_counter()


@dataclass(frozen=True)
class SkillPackContract:
    """Agent-consumable skill-pack capability, not a loaded runtime skill."""

    id: str
    responsibility: str
    teaches: list[str]
    verifies: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "responsibility": self.responsibility,
            "teaches": self.teaches,
            "verifies": self.verifies,
        }


@dataclass(frozen=True)
class LifecycleCommandContract:
    """CLI-style command contract that makes a workflow stage auditable."""

    id: str
    responsibility: str
    requires: list[str]
    produces: list[str]
    verifies: list[str]
    side_effect_level: str = "none"
    authority_boundary: str = "local/offline only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "responsibility": self.responsibility,
            "requires": self.requires,
            "produces": self.produces,
            "verifies": self.verifies,
            "side_effect_level": self.side_effect_level,
            "authority_boundary": self.authority_boundary,
        }


@dataclass(frozen=True)
class EdgeRuntimeProfileContract:
    """Default-off local edge-worker profile inspired by low-footprint agents."""

    id: str
    responsibility: str
    device_classes: list[str]
    resource_budget: dict[str, str]
    allowed_capabilities: list[str]
    required_gates: list[str]
    forbidden_by_default: list[str]
    verifies: list[str]
    side_effect_level: str = "local_write"
    authority_boundary: str = "profile/readback only; no device control or external dispatch"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "responsibility": self.responsibility,
            "device_classes": self.device_classes,
            "resource_budget": self.resource_budget,
            "allowed_capabilities": self.allowed_capabilities,
            "required_gates": self.required_gates,
            "forbidden_by_default": self.forbidden_by_default,
            "verifies": self.verifies,
            "side_effect_level": self.side_effect_level,
            "authority_boundary": self.authority_boundary,
        }


class LifecycleContractRegistry:
    """Google agents-cli-inspired skill-pack + command lifecycle manifest.

    This is a local/default-off contract surface.  It gives Hermes evals and
    future agents a stable vocabulary for lifecycle stages without wiring any
    cloud deploy, taskpool mutation, memory write, or external dispatch.
    """

    def __init__(
        self,
        skill_packs: list[SkillPackContract],
        commands: list[LifecycleCommandContract],
        edge_runtime_profiles: list[EdgeRuntimeProfileContract] | None = None,
    ):
        self.skill_packs = skill_packs
        self.commands = commands
        self.edge_runtime_profiles = edge_runtime_profiles or []

    @classmethod
    def default(cls) -> "LifecycleContractRegistry":
        return cls(
            skill_packs=[
                SkillPackContract(
                    "workflow",
                    "Route the request, preserve objective/path gates, and select the smallest safe stage.",
                    ["objective gate", "dynamic workflow route", "boundary classification"],
                    ["route decision", "human-gate packet when blocked"],
                ),
                SkillPackContract(
                    "implementation",
                    "Turn an accepted stage into scoped code/docs/config changes without widening the core waist.",
                    ["incremental implementation", "narrow-waist extension", "scoped diff discipline"],
                    ["focused tests", "diff/readback"],
                ),
                SkillPackContract(
                    "evaluation",
                    "Generate and grade replayable cases before claiming reliability improvement.",
                    ["case registry", "trajectory capture", "tool/code graders", "rubric contracts"],
                    ["eval run report", "regression database"],
                ),
                SkillPackContract(
                    "delivery",
                    "Package local artifacts and deployment proposals while keeping real publish/deploy gated.",
                    ["artifact manifest", "rollback plan", "deploy proposal"],
                    ["local receipt", "authority boundary readback"],
                ),
                SkillPackContract(
                    "edge_runtime",
                    "Model tiny/local agent execution as a constrained worker profile without enabling device actions.",
                    ["device profile", "resource budget", "local executor contract", "kill switch"],
                    ["profile readback", "benchmark receipt", "closed side-effect gates"],
                ),
                SkillPackContract(
                    "observability",
                    "Record trajectory, dashboard, and ledger signals for post-update regression.",
                    ["dashboard", "feedback ledger", "failure taxonomy"],
                    ["dashboard.md", "feedback.jsonl"],
                ),
                SkillPackContract(
                    "governance",
                    "Keep taskpool, memory, cron, gateway, and external effects behind exact authority gates.",
                    ["side-effect policy", "sandbox policy", "receipt/readback"],
                    ["blocked side-effect proof", "policy finding"],
                ),
                SkillPackContract(
                    "experience_ratchet",
                    "Promote repeated verified lessons into skills/docs/issues without saving task progress as memory.",
                    ["landing-zone classifier", "skill patch candidate", "runtime issue candidate"],
                    ["deduped feedback item", "scoped proposal"],
                ),
            ],
            commands=[
                LifecycleCommandContract(
                    "intake",
                    "Capture source, claims, constraints, red lines, and SSOT path.",
                    [],
                    ["intake note", "source evidence refs"],
                    ["canonical source recorded", "scope and anti-claims stated"],
                ),
                LifecycleCommandContract(
                    "scaffold",
                    "Create or select the minimal local artifact skeleton for the workflow stage.",
                    ["workflow"],
                    ["case/spec/contract skeleton"],
                    ["schema/readback"],
                    side_effect_level="local_write",
                ),
                LifecycleCommandContract(
                    "implement",
                    "Apply the smallest scoped implementation slice against the selected contract.",
                    ["workflow", "implementation"],
                    ["patch", "updated artifact"],
                    ["focused test", "diff review"],
                    side_effect_level="local_write",
                ),
                LifecycleCommandContract(
                    "eval-generate",
                    "Create replayable eval cases or trajectory fixtures for the implemented slice.",
                    ["evaluation"],
                    ["eval case", "trajectory fixture"],
                    ["case hash", "forbidden side effects listed"],
                    side_effect_level="local_write",
                ),
                LifecycleCommandContract(
                    "eval-grade",
                    "Run deterministic graders and optional rubric contracts against a case.",
                    ["evaluation"],
                    ["report.json", "regression.sqlite"],
                    ["pass/fail score", "failure type when failed"],
                    side_effect_level="local_write",
                ),
                LifecycleCommandContract(
                    "edge-profile",
                    "Emit a local edge-worker capability profile and keep execution/device gates closed.",
                    ["edge_runtime", "governance"],
                    ["edge profile manifest"],
                    ["profile id", "resource budget", "no device control is enabled"],
                    side_effect_level="local_write",
                    authority_boundary="profile/readback only; execution requires a later exact target gate",
                ),
                LifecycleCommandContract(
                    "edge-benchmark",
                    "Run or record local-only footprint/boot/readback measurements for an edge-worker candidate.",
                    ["edge_runtime", "evaluation"],
                    ["benchmark receipt", "profile evidence"],
                    ["memory_rss_mb", "boot_ms", "closed external/device gates"],
                    side_effect_level="local_write",
                    authority_boundary="local benchmark only; no hardware/device control without exact authorization",
                ),
                LifecycleCommandContract(
                    "edge-verify",
                    "Verify an edge benchmark receipt and classify promotion readiness without opening runtime gates.",
                    ["edge_runtime", "evaluation", "governance"],
                    ["verification report"],
                    ["receipt schema", "resource budget", "closed side-effect gates", "next authority packet required"],
                    side_effect_level="local_read",
                    authority_boundary="read-only verification; runtime activation requires a later exact target gate",
                ),
                LifecycleCommandContract(
                    "edge-activation-packet",
                    "Write a non-executing runtime activation authority packet template from a passing verifier.",
                    ["edge_runtime", "governance"],
                    ["activation packet template"],
                    ["execute=false", "rollback and readback required", "side-effect gates remain closed"],
                    side_effect_level="local_write",
                    authority_boundary="packet/template only; no runtime activation is performed",
                ),
                LifecycleCommandContract(
                    "edge-kill-switch-dry-run",
                    "Write a local disable/rollback dry-run receipt proving a closed activation path has a kill switch.",
                    ["edge_runtime", "governance", "evaluation"],
                    ["kill-switch dry-run receipt"],
                    ["execute=false", "disabled readback simulated", "side-effect gates remain closed"],
                    side_effect_level="local_write",
                    authority_boundary="dry-run receipt only; no runtime is disabled or contacted",
                ),
                LifecycleCommandContract(
                    "edge-status-readback",
                    "Read the closed edge ladder artifacts and summarize readiness without opening runtime gates.",
                    ["edge_runtime", "governance", "observability"],
                    ["status readback"],
                    ["read-only", "execute=false", "promotion_ready=false"],
                    side_effect_level="local",
                    authority_boundary="readback only; no artifact mutation or runtime activation",
                ),
                LifecycleCommandContract(
                    "edge-open-boundary-checklist",
                    "Write a non-executing human authority checklist for the remaining exact runtime activation boundary.",
                    ["edge_runtime", "governance"],
                    ["open-boundary checklist"],
                    ["execute=false", "human authority required", "closed gates preserved"],
                    side_effect_level="local_write",
                    authority_boundary="checklist only; does not authorize or perform activation",
                ),
                LifecycleCommandContract(
                    "edge-authority-discover",
                    "Scan existing local artifacts for edge authority candidates and blockers before asking the user.",
                    ["edge_runtime", "governance", "observability"],
                    ["authority discovery report"],
                    ["read-only", "no runtime activation", "reports missing execute=true packet"],
                    side_effect_level="local",
                    authority_boundary="discovery only; does not create authority or execute activation",
                ),
                LifecycleCommandContract(
                    "deploy-proposal",
                    "Produce a deployment/publish proposal and rollback plan without executing external deploy.",
                    ["delivery", "evaluation"],
                    ["proposal", "rollback plan", "authority packet"],
                    ["external deploy/publish remains human-gated"],
                    side_effect_level="local_write",
                    authority_boundary="external deploy/publish remains human-gated",
                ),
                LifecycleCommandContract(
                    "observe",
                    "Write local observability/readback artifacts for regression and operator review.",
                    ["observability"],
                    ["dashboard", "feedback ledger"],
                    ["no live gateway/taskpool mutation"],
                    side_effect_level="local_write",
                ),
                LifecycleCommandContract(
                    "ratchet",
                    "Convert repeated verified failures into scoped skill/docs/runtime-issue proposals.",
                    ["experience_ratchet", "governance"],
                    ["ratchet proposal"],
                    ["no direct memory/config/runtime mutation"],
                    side_effect_level="local_write",
                ),
            ],
            edge_runtime_profiles=[
                EdgeRuntimeProfileContract(
                    id="local_edge_worker",
                    responsibility=(
                        "Represent PicoClaw-style tiny local automation as an owned Hermes worker contract "
                        "that can be benchmarked before any real executor/device integration."
                    ),
                    device_classes=["local_mac", "linux_sbc", "android_termux", "kvm_or_camera_gateway"],
                    resource_budget={
                        "memory_rss_mb": "record_observed; target <= 64MB before edge promotion",
                        "boot_ms": "record_observed; target <= 1000ms before edge promotion",
                        "network": "offline/local by default",
                    },
                    allowed_capabilities=[
                        "read-only profile readback",
                        "local benchmark receipt",
                        "fixture-based command simulation",
                        "health/kill-switch dry run",
                    ],
                    required_gates=[
                        "exact target device profile",
                        "sandbox policy",
                        "receipt/readback",
                        "rollback or kill switch",
                    ],
                    forbidden_by_default=[
                        "external dispatch",
                        "credential entry",
                        "device control",
                        "MCP write authority",
                        "publish/deploy/push",
                        "destructive operation",
                    ],
                    verifies=["memory_rss_mb", "boot_ms", "profile_id", "closed_side_effect_gates"],
                )
            ],
        )

    def command(self, command_id: str) -> LifecycleCommandContract:
        for command in self.commands:
            if command.id == command_id:
                return command
        raise KeyError(command_id)

    def edge_profile(self, profile_id: str) -> EdgeRuntimeProfileContract:
        for profile in self.edge_runtime_profiles:
            if profile.id == profile_id:
                return profile
        raise KeyError(profile_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "hermes_agent_lifecycle_contract_v1",
            "source": "google-agents-cli pattern adapted for Hermes local/offline reliability harness",
            "runtime_effect": "none; contract manifest only",
            "skill_packs": [pack.as_dict() for pack in self.skill_packs],
            "commands": [command.as_dict() for command in self.commands],
            "edge_runtime_profiles": [profile.as_dict() for profile in self.edge_runtime_profiles],
        }


@dataclass(frozen=True)
class EvalCase:
    id: str
    input: str
    workflow: str = "direct"
    risk: str = "low"
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_side_effects: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    acceptance: dict[str, Any] = field(default_factory=dict)
    graders: list[str] = field(default_factory=lambda: ["tool_code"])
    case_hash: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "EvalCase":
        required = ["id", "input"]
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError(f"eval case missing required fields: {', '.join(missing)}")
        normalized = {
            "id": str(data["id"]),
            "input": str(data["input"]),
            "workflow": str(data.get("workflow") or "direct").lower(),
            "risk": str(data.get("risk") or "low").lower(),
            "allowed_tools": list(data.get("allowed_tools") or []),
            "forbidden_side_effects": list(data.get("forbidden_side_effects") or []),
            "expected_artifacts": list(data.get("expected_artifacts") or []),
            "acceptance": dict(data.get("acceptance") or {}),
            "graders": list(data.get("graders") or ["tool_code"]),
        }
        digest = _stable_hash(normalized)
        return cls(**normalized, case_hash=digest)


class CaseRegistry:
    """Loads versioned eval cases from JSON files."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EvalCase:
        return EvalCase.from_mapping(data)

    def load(self, case_id: str) -> EvalCase:
        path = self.root / f"{case_id}.json"
        return self.load_file(path)

    def load_file(self, path: str | Path) -> EvalCase:
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        return EvalCase.from_mapping(data)

    def list_cases(self) -> list[EvalCase]:
        cases: list[EvalCase] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                cases.append(self.load_file(path))
            except ValueError:
                # evals/cases may also carry adjacent benchmark fixtures with
                # their own schema.  The reliability harness lists only the
                # simple EvalCase schema instead of failing the whole CLI.
                continue
        return cases


@dataclass(frozen=True)
class SandboxPolicy:
    """Default-deny policy for external side-effect tools in eval."""

    blocked_tools: set[str] = field(default_factory=lambda: set(DEFAULT_BLOCKED_TOOLS))
    allowed_tools: set[str] = field(default_factory=set)

    @classmethod
    def default(cls) -> "SandboxPolicy":
        return cls()

    def is_allowed_tool(self, tool_name: str) -> bool:
        if tool_name in self.blocked_tools:
            return False
        if self.allowed_tools:
            return tool_name in self.allowed_tools
        return True

    def violations(self, tool_events: Iterable[dict[str, Any]]) -> list[str]:
        bad: list[str] = []
        for event in tool_events:
            tool = str(event.get("tool") or event.get("name") or "")
            if tool and not self.is_allowed_tool(tool):
                bad.append(tool)
        return bad


@dataclass(frozen=True)
class RouteDecision:
    workflow: str
    confidence: float
    reason: str
    mode: str = "shadow"
    enforced: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "confidence": self.confidence,
            "reason": self.reason,
            "mode": self.mode,
            "enforced": self.enforced,
        }


class ShadowWorkflowRouter:
    """Cheap deterministic workflow router.

    It is advisory by default.  Even in ``suggest`` mode it does not mutate live
    execution; ``limited-enforce`` only marks low-risk direct/research decisions
    as enforceable for callers that explicitly opt in.
    """

    def __init__(self, mode: str = "shadow"):
        self.mode = mode

    def route(self, text: str, risk: str | None = None) -> RouteDecision:
        t = (text or "").lower()
        workflow = "direct"
        confidence = 0.62
        reasons: list[str] = []

        if _matches(t, ["全盘推进", "生产级", "commit", "高风险", "长期", "直至", "落地所有阶段"]):
            workflow = "objective_gated"
            confidence = 0.93
            reasons.append("objective/high-risk/full-stage marker")
        elif _matches(t, ["查", "搜索", "research", "证据", "来源", "新闻", "监管", "分析这个视频"]):
            workflow = "research"
            confidence = 0.82
            reasons.append("external evidence/research marker")
        elif _matches(t, ["修复", "实现", "重构", "计划", "多步", "测试", "验证"]):
            workflow = "plan"
            confidence = 0.78
            reasons.append("multi-step implementation marker")

        if risk and risk.lower() in {"high", "critical"} and workflow == "direct":
            workflow = "objective_gated"
            confidence = max(confidence, 0.86)
            reasons.append("case risk escalated")

        enforceable = self.mode == "limited-enforce" and workflow in {"direct", "research"} and confidence >= 0.8
        return RouteDecision(
            workflow=workflow,
            confidence=round(confidence, 3),
            reason="; ".join(reasons) or "default simple/direct route",
            mode=self.mode,
            enforced=enforceable,
        )


@dataclass(frozen=True)
class GraderVerdict:
    grader: str
    passed: bool
    score: float
    failure_type: str | None = None
    message: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "grader": self.grader,
            "passed": self.passed,
            "score": self.score,
            "failure_type": self.failure_type,
            "message": self.message,
            "evidence_refs": self.evidence_refs,
        }


class ToolCodeGrader:
    """Deterministic grader for artifacts, acceptance text, and sandbox policy."""

    name = "tool_code"

    def grade(
        self,
        case: EvalCase,
        workdir: str | Path,
        *,
        response_text: str = "",
        tool_events: list[dict[str, Any]] | None = None,
        sandbox_policy: SandboxPolicy | None = None,
    ) -> GraderVerdict:
        root = Path(workdir)
        evidence: list[str] = []
        missing: list[str] = []
        text_blobs = [response_text or ""]

        for rel in case.expected_artifacts:
            path = root / rel
            if not path.exists():
                missing.append(rel)
                continue
            evidence.append(str(path))
            if path.is_file():
                try:
                    text_blobs.append(path.read_text(encoding="utf-8"))
                except UnicodeDecodeError:
                    evidence.append(f"binary:{path}")

        if missing:
            return GraderVerdict(
                grader=self.name,
                passed=False,
                score=0.0,
                failure_type="missing_artifact",
                message=f"Missing expected artifact(s): {', '.join(missing)}",
                evidence_refs=evidence,
            )

        combined = "\n".join(text_blobs)
        required = list(case.acceptance.get("contains") or [])
        absent = [needle for needle in required if str(needle) not in combined]
        if absent:
            return GraderVerdict(
                grader=self.name,
                passed=False,
                score=0.25,
                failure_type="acceptance_contains_missing",
                message=f"Missing required text: {', '.join(map(str, absent))}",
                evidence_refs=evidence,
            )

        policy = sandbox_policy or SandboxPolicy.default()
        violations = policy.violations(tool_events or [])
        if violations:
            return GraderVerdict(
                grader=self.name,
                passed=False,
                score=0.0,
                failure_type="sandbox_violation",
                message=f"Blocked side-effect tools used: {', '.join(sorted(set(violations)))}",
                evidence_refs=evidence,
            )

        return GraderVerdict(
            grader=self.name,
            passed=True,
            score=1.0,
            message="Deterministic tool/code checks passed",
            evidence_refs=evidence or ["response_text"],
        )


class LLMRubricGrader:
    """Rubric contract placeholder; not model-calling by design.

    The harness records the rubric requirement and returns an advisory pass when
    no external model judgment is provided.  Future model-backed grading can
    write the same verdict schema without changing the DB/report contract.
    """

    name = "llm_rubric"

    def grade(self, case: EvalCase, response_text: str = "") -> GraderVerdict:
        rubric = case.acceptance.get("rubric") or []
        if rubric and not response_text.strip():
            return GraderVerdict(self.name, False, 0.0, "empty_response", "No response for rubric grading")
        return GraderVerdict(self.name, True, 1.0, message="Rubric contract satisfied/advisory")


class HumanReviewGrader:
    name = "human_review"

    def grade(self, case: EvalCase) -> GraderVerdict:
        if case.risk in {"high", "critical"}:
            return GraderVerdict(self.name, False, 0.5, "human_review_required", "High-risk case requires human review")
        return GraderVerdict(self.name, True, 1.0, message="Human review not required for this risk level")


class FeedbackLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_from_verdict(self, case: EvalCase, verdict: GraderVerdict, run_id: str) -> dict[str, Any] | None:
        if verdict.passed:
            return None
        item = {
            "ts": _now(),
            "run_id": run_id,
            "case_id": case.id,
            "failure_type": verdict.failure_type or "unknown_failure",
            "evidence_refs": verdict.evidence_refs,
            "root_cause": verdict.message,
            "landing_zone": "regression_case_or_fix",
            "suggested_fix_layer": _suggest_fix_layer(verdict.failure_type),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class RegressionDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eval_runs (
                    run_id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    case_hash TEXT NOT NULL,
                    workflow TEXT NOT NULL,
                    route_workflow TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    score REAL NOT NULL,
                    report_path TEXT NOT NULL,
                    trajectory_path TEXT NOT NULL
                )
                """
            )

    def record(self, result: "EvalRunResult") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO eval_runs
                (run_id, ts, case_id, case_hash, workflow, route_workflow, passed, score, report_path, trajectory_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.ts,
                    result.case.id,
                    result.case.case_hash,
                    result.case.workflow,
                    result.route.workflow,
                    1 if result.passed else 0,
                    result.score,
                    str(result.report_path),
                    str(result.trajectory_path),
                ),
            )

    def history(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_runs WHERE case_id = ? ORDER BY ts ASC",
                (case_id,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total, SUM(passed) AS passed FROM eval_runs").fetchone()
        total = int(row["total"] or 0)
        passed = int(row["passed"] or 0)
        return {"total": total, "passed": passed, "failed": total - passed}


@dataclass(frozen=True)
class EvalRunResult:
    run_id: str
    ts: str
    case: EvalCase
    route: RouteDecision
    verdicts: list[GraderVerdict]
    passed: bool
    score: float
    trajectory_path: Path
    report_path: Path
    feedback_items: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ts": self.ts,
            "case_id": self.case.id,
            "case_hash": self.case.case_hash,
            "workflow": self.case.workflow,
            "route": self.route.as_dict(),
            "passed": self.passed,
            "score": self.score,
            "verdicts": [v.as_dict() for v in self.verdicts],
            "trajectory_path": str(self.trajectory_path),
            "report_path": str(self.report_path),
            "feedback_items": self.feedback_items,
        }


class EvalHarness:
    def __init__(
        self,
        *,
        cases_dir: str | Path,
        runs_dir: str | Path,
        db_path: str | Path | None = None,
        router_mode: str = "shadow",
        sandbox_policy: SandboxPolicy | None = None,
    ):
        self.registry = CaseRegistry(cases_dir)
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.router = ShadowWorkflowRouter(mode=router_mode)
        self.sandbox_policy = sandbox_policy or SandboxPolicy.default()
        self.db = RegressionDB(db_path or self.runs_dir / "regression.sqlite")
        self.ledger = FeedbackLedger(self.runs_dir / "feedback.jsonl")

    def run_case(
        self,
        case_id: str,
        *,
        response_text: str = "",
        produced_artifacts: dict[str, str] | None = None,
        tool_events: list[dict[str, Any]] | None = None,
    ) -> EvalRunResult:
        case = self.registry.load(case_id)
        ts = _now()
        run_id = f"{case.id}-{hashlib.sha1(ts.encode('utf-8')).hexdigest()[:10]}"
        run_dir = self.runs_dir / run_id
        workdir = run_dir / "workdir"
        workdir.mkdir(parents=True, exist_ok=True)

        for rel, content in (produced_artifacts or {}).items():
            target = workdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")

        route = self.router.route(case.input, risk=case.risk)
        lifecycle_contract = LifecycleContractRegistry.default().as_dict()
        if case.workflow and route.workflow == "direct" and case.workflow != "direct":
            route = RouteDecision(
                workflow=case.workflow,
                confidence=max(route.confidence, 0.75),
                reason=f"case workflow contract: {case.workflow}; {route.reason}",
                mode=route.mode,
                enforced=False,
            )
        verdicts: list[GraderVerdict] = []
        for grader_name in case.graders:
            if grader_name == "tool_code":
                verdicts.append(
                    ToolCodeGrader().grade(
                        case,
                        workdir,
                        response_text=response_text,
                        tool_events=tool_events or [],
                        sandbox_policy=self.sandbox_policy,
                    )
                )
            elif grader_name == "llm_rubric":
                verdicts.append(LLMRubricGrader().grade(case, response_text=response_text))
            elif grader_name == "human_review":
                verdicts.append(HumanReviewGrader().grade(case))
            else:
                verdicts.append(GraderVerdict(grader_name, False, 0.0, "unknown_grader", f"Unknown grader: {grader_name}"))

        passed = all(v.passed for v in verdicts)
        score = round(sum(v.score for v in verdicts) / max(1, len(verdicts)), 3)
        trajectory = {
            "schema": "hermes_agent_reliability_trajectory_v1",
            "run_id": run_id,
            "ts": ts,
            "case": {"id": case.id, "hash": case.case_hash, "workflow": case.workflow, "risk": case.risk},
            "route": route.as_dict(),
            "lifecycle_contract": {
                "schema": lifecycle_contract["schema"],
                "runtime_effect": lifecycle_contract["runtime_effect"],
                "skill_pack_ids": [pack["id"] for pack in lifecycle_contract["skill_packs"]],
                "command_ids": [command["id"] for command in lifecycle_contract["commands"]],
                "edge_runtime_profile_ids": [profile["id"] for profile in lifecycle_contract["edge_runtime_profiles"]],
            },
            "sandbox": {"blocked_tools": sorted(self.sandbox_policy.blocked_tools)},
            "tool_events": tool_events or [],
            "response_text": response_text,
            "expected_artifacts": case.expected_artifacts,
        }
        trajectory_path = run_dir / "trajectory.json"
        trajectory_path.write_text(json.dumps(trajectory, ensure_ascii=False, indent=2), encoding="utf-8")

        feedback_items: list[dict[str, Any]] = []
        for verdict in verdicts:
            item = self.ledger.append_from_verdict(case, verdict, run_id)
            if item:
                feedback_items.append(item)

        result = EvalRunResult(
            run_id=run_id,
            ts=ts,
            case=case,
            route=route,
            verdicts=verdicts,
            passed=passed,
            score=score,
            trajectory_path=trajectory_path,
            report_path=run_dir / "report.json",
            feedback_items=feedback_items,
        )
        result.report_path.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.record(result)
        self._write_dashboard()
        return result

    def _write_dashboard(self) -> None:
        summary = self.db.summary()
        lines = [
            "# Hermes Agent Reliability Harness Dashboard",
            "",
            f"Updated: {_now()}",
            f"Total runs: {summary['total']}",
            f"Passed: {summary['passed']}",
            f"Failed: {summary['failed']}",
            "",
            "This dashboard is generated from offline eval runs only; it does not mutate live gateway/taskpool state.",
        ]
        (self.runs_dir / "dashboard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stable_hash(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _matches(text: str, needles: Iterable[str]) -> bool:
    return any(re.search(re.escape(needle.lower()), text) for needle in needles)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    if "passed" in out:
        out["passed"] = bool(out["passed"])
    return out


def _suggest_fix_layer(failure_type: str | None) -> str:
    if failure_type in {"missing_artifact", "acceptance_contains_missing"}:
        return "case_or_implementation"
    if failure_type == "sandbox_violation":
        return "sandbox_or_tool_policy"
    if failure_type == "human_review_required":
        return "human_review"
    return "regression_case_or_fix"


def _memory_rss_mb() -> float:
    try:
        import resource

        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return round(rss / divisor, 3)
    except Exception:
        return 0.0


def write_edge_benchmark_receipt(runs_dir: str | Path, profile_id: str = "local_edge_worker") -> dict[str, Any]:
    registry = LifecycleContractRegistry.default()
    profile = registry.edge_profile(profile_id)
    root = Path(runs_dir)
    root.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": "hermes_edge_benchmark_receipt_v1",
        "profile_id": profile.id,
        "ts": _now(),
        "runtime_effect": "local_write_receipt_only",
        "measurements": {
            "memory_rss_mb": _memory_rss_mb(),
            "boot_ms": round((time.perf_counter() - _MODULE_START) * 1000, 3),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "gates": {
            "device_control": "closed",
            "external_dispatch": "closed",
            "mcp_write_authority": "closed",
            "publish_deploy_push": "closed",
            "destructive_operation": "closed",
            "credential_entry": "closed",
        },
        "resource_budget": profile.resource_budget,
        "allowed_capabilities": profile.allowed_capabilities,
        "forbidden_by_default": profile.forbidden_by_default,
        "authority_boundary": profile.authority_boundary,
    }
    receipt_path = root / f"edge-benchmark-{profile.id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def _verification_check(name: str, passed: bool, evidence: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if passed else "fail", "evidence": evidence}


def verify_edge_benchmark_receipt(receipt_path: str | Path) -> dict[str, Any]:
    path = Path(receipt_path)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "schema": "hermes_edge_benchmark_verification_v1",
            "status": "blocked",
            "profile_id": None,
            "runtime_effect": "read_only_verification",
            "promotion_ready": False,
            "next_authority_required": "valid_benchmark_receipt",
            "receipt_path": str(path),
            "checks": [_verification_check("receipt_parseable", False, str(exc))],
        }

    registry = LifecycleContractRegistry.default()
    profile_id = receipt.get("profile_id")
    try:
        registry.edge_profile(str(profile_id))
        known_profile = True
    except KeyError:
        known_profile = False

    measurements = receipt.get("measurements") if isinstance(receipt.get("measurements"), dict) else {}
    gates = receipt.get("gates") if isinstance(receipt.get("gates"), dict) else {}
    memory = measurements.get("memory_rss_mb")
    boot = measurements.get("boot_ms")
    checks = [
        _verification_check(
            "schema",
            receipt.get("schema") == "hermes_edge_benchmark_receipt_v1",
            str(receipt.get("schema")),
        ),
        _verification_check("profile_known", known_profile, str(profile_id)),
        _verification_check(
            "runtime_effect_local_write_only",
            receipt.get("runtime_effect") == "local_write_receipt_only",
            str(receipt.get("runtime_effect")),
        ),
        _verification_check(
            "measurement_memory_rss_mb_present",
            isinstance(memory, (int, float)) and memory >= 0,
            str(memory),
        ),
        _verification_check(
            "measurement_boot_ms_present",
            isinstance(boot, (int, float)) and boot >= 0,
            str(boot),
        ),
        _verification_check(
            "measurement_memory_rss_mb_target",
            isinstance(memory, (int, float)) and memory <= 64,
            f"{memory} <= 64",
        ),
        _verification_check(
            "measurement_boot_ms_target",
            isinstance(boot, (int, float)) and boot <= 1000,
            f"{boot} <= 1000",
        ),
    ]
    for gate in [
        "device_control",
        "external_dispatch",
        "mcp_write_authority",
        "publish_deploy_push",
        "destructive_operation",
        "credential_entry",
    ]:
        checks.append(_verification_check(f"gate_{gate}_closed", gates.get(gate) == "closed", str(gates.get(gate))))

    status = "pass" if all(check["status"] == "pass" for check in checks) else "blocked"
    return {
        "schema": "hermes_edge_benchmark_verification_v1",
        "status": status,
        "profile_id": profile_id,
        "runtime_effect": "read_only_verification",
        "promotion_ready": False,
        "next_authority_required": "exact_runtime_activation_packet" if status == "pass" else "fix_benchmark_receipt_or_closed_gates",
        "receipt_path": str(path),
        "checks": checks,
    }


def write_edge_activation_packet(
    verification_path: str | Path,
    runs_dir: str | Path,
    target_id: str,
) -> tuple[dict[str, Any], int]:
    path = Path(verification_path)
    try:
        verification = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return (
            {
                "schema": "hermes_edge_activation_packet_preflight_v1",
                "status": "blocked",
                "runtime_effect": "read_only_preflight",
                "reason": "verification_not_parseable",
                "evidence": str(exc),
            },
            1,
        )
    if verification.get("status") != "pass":
        return (
            {
                "schema": "hermes_edge_activation_packet_preflight_v1",
                "status": "blocked",
                "runtime_effect": "read_only_preflight",
                "reason": "verification_not_passed",
                "verification_path": str(path),
                "verification_status": verification.get("status"),
                "failed_checks": [check for check in verification.get("checks", []) if check.get("status") != "pass"],
            },
            1,
        )

    root = Path(runs_dir)
    root.mkdir(parents=True, exist_ok=True)
    profile_id = verification.get("profile_id")
    packet = {
        "schema": "hermes_edge_activation_packet_v1",
        "status": "authorization_required",
        "ts": _now(),
        "profile_id": profile_id,
        "execute": False,
        "runtime_effect": "local_write_packet_only",
        "target": {
            "id": target_id,
            "type": "exact_target_placeholder",
            "must_be_replaced_before_execution": True,
        },
        "preconditions": {
            "verification_path": str(path),
            "verification_status": verification.get("status"),
            "all_checks_passed": True,
        },
        "rollback": {
            "kill_switch_required": True,
            "rollback_receipt_required": True,
            "restore_path_required": True,
        },
        "readback": {
            "required": True,
            "post_action_status_required": True,
            "must_confirm_execute_true_was_explicitly_authorized": True,
        },
        "gates_remain_closed": {
            "device_control": "closed",
            "external_dispatch": "closed",
            "mcp_write_authority": "closed",
            "publish_deploy_push": "closed",
            "destructive_operation": "closed",
            "credential_entry": "closed",
        },
        "next_authority_required": "exact_runtime_activation_packet_execute_true",
    }
    packet_path = root / f"edge-activation-packet-{profile_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    packet["packet_path"] = str(packet_path)
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    return packet, 0


def write_edge_kill_switch_dry_run(
    activation_packet_path: str | Path,
    runs_dir: str | Path,
) -> tuple[dict[str, Any], int]:
    path = Path(activation_packet_path)
    try:
        activation = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return (
            {
                "schema": "hermes_edge_kill_switch_preflight_v1",
                "status": "blocked",
                "runtime_effect": "read_only_preflight",
                "reason": "activation_packet_not_parseable",
                "evidence": str(exc),
            },
            1,
        )
    if activation.get("schema") != "hermes_edge_activation_packet_v1":
        return (
            {
                "schema": "hermes_edge_kill_switch_preflight_v1",
                "status": "blocked",
                "runtime_effect": "read_only_preflight",
                "reason": "invalid_activation_packet_schema",
                "evidence": str(activation.get("schema")),
            },
            1,
        )
    if activation.get("execute") is not False:
        return (
            {
                "schema": "hermes_edge_kill_switch_preflight_v1",
                "status": "blocked",
                "runtime_effect": "read_only_preflight",
                "reason": "execute_true_not_allowed_for_dry_run",
                "activation_packet_path": str(path),
            },
            1,
        )

    gates = activation.get("gates_remain_closed") if isinstance(activation.get("gates_remain_closed"), dict) else {}
    closed_gates = {
        gate: gates.get(gate, "closed") if gates.get(gate, "closed") == "closed" else gates.get(gate)
        for gate in [
            "device_control",
            "external_dispatch",
            "mcp_write_authority",
            "publish_deploy_push",
            "destructive_operation",
            "credential_entry",
        ]
    }
    root = Path(runs_dir)
    root.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": "hermes_edge_kill_switch_dry_run_receipt_v1",
        "status": "pass",
        "ts": _now(),
        "profile_id": activation.get("profile_id"),
        "activation_packet_path": str(path),
        "execute": False,
        "runtime_effect": "local_write_receipt_only",
        "target": activation.get("target", {}),
        "status_after_dry_run": "disabled",
        "rollback_verified": True,
        "readback": {
            "required": True,
            "simulated_status": "disabled",
            "post_action_status_required": True,
        },
        "gates_remain_closed": closed_gates,
    }
    receipt_path = root / f"edge-kill-switch-dry-run-{activation.get('profile_id')}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt, 0


def _load_json_artifact(path: str | Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    p = Path(path)
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return payload, {"path": str(p), "status": "present", "schema": payload.get("schema")}
    except Exception as exc:
        return None, {"path": str(p), "status": "missing_or_invalid", "error": str(exc)}


def _closed_gate_summary(*payloads: dict[str, Any] | None) -> dict[str, str]:
    gates = {
        "device_control": "closed",
        "external_dispatch": "closed",
        "mcp_write_authority": "closed",
        "publish_deploy_push": "closed",
        "destructive_operation": "closed",
        "credential_entry": "closed",
    }
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        source = payload.get("gates_remain_closed") or payload.get("gates") or payload.get("closed_gates")
        if isinstance(source, dict):
            for gate in gates:
                gates[gate] = source.get(gate, gates[gate])
    return gates


def edge_status_readback(
    benchmark_path: str | Path,
    verification_path: str | Path,
    activation_packet_path: str | Path,
    kill_switch_receipt_path: str | Path,
) -> tuple[dict[str, Any], int]:
    benchmark, benchmark_info = _load_json_artifact(benchmark_path)
    verification, verification_info = _load_json_artifact(verification_path)
    activation, activation_info = _load_json_artifact(activation_packet_path)
    kill_switch, kill_switch_info = _load_json_artifact(kill_switch_receipt_path)

    artifacts = {
        "benchmark": benchmark_info,
        "verification": verification_info,
        "activation_packet": activation_info,
        "kill_switch_receipt": kill_switch_info,
    }
    if isinstance(activation, dict):
        artifacts["activation_packet"].update(
            {
                "execute": activation.get("execute"),
                "status": "present",
                "target": activation.get("target"),
            }
        )
    if isinstance(kill_switch, dict):
        artifacts["kill_switch_receipt"].update(
            {
                "status": kill_switch.get("status"),
                "status_after_dry_run": kill_switch.get("status_after_dry_run"),
            }
        )

    required = [benchmark, verification, activation, kill_switch]
    closed_gates = _closed_gate_summary(benchmark, verification, activation, kill_switch)
    checks = [
        benchmark and benchmark.get("schema") == "hermes_edge_benchmark_receipt_v1",
        verification and verification.get("schema") == "hermes_edge_benchmark_verification_v1" and verification.get("status") == "pass",
        activation and activation.get("schema") == "hermes_edge_activation_packet_v1" and activation.get("execute") is False,
        kill_switch and kill_switch.get("schema") == "hermes_edge_kill_switch_dry_run_receipt_v1" and kill_switch.get("status_after_dry_run") == "disabled",
        all(value == "closed" for value in closed_gates.values()),
    ]
    ok = all(checks)
    status = "closed_ladder_ready_for_human_authority" if ok else "blocked"
    payload = {
        "schema": "hermes_edge_status_readback_v1",
        "status": status,
        "ts": _now(),
        "profile_id": (verification or activation or benchmark or {}).get("profile_id"),
        "runtime_effect": "read_only_status",
        "execute": False,
        "promotion_ready": False,
        "artifacts": artifacts,
        "closed_gates": closed_gates,
        "next_authority_required": "exact_runtime_activation_packet_execute_true" if ok else "fix_closed_ladder_artifacts",
    }
    return payload, 0 if ok else 1


def write_edge_open_boundary_checklist(
    status_readback_path: str | Path,
    runs_dir: str | Path,
) -> tuple[dict[str, Any], int]:
    status_readback, status_info = _load_json_artifact(status_readback_path)
    if not status_readback or status_readback.get("schema") != "hermes_edge_status_readback_v1":
        return (
            {
                "schema": "hermes_edge_open_boundary_checklist_preflight_v1",
                "status": "blocked",
                "runtime_effect": "read_only_preflight",
                "reason": "invalid_status_readback",
                "status_readback": status_info,
            },
            1,
        )
    if status_readback.get("status") != "closed_ladder_ready_for_human_authority":
        return (
            {
                "schema": "hermes_edge_open_boundary_checklist_preflight_v1",
                "status": "blocked",
                "runtime_effect": "read_only_preflight",
                "reason": "status_readback_not_ready",
                "status": status_readback.get("status"),
            },
            1,
        )

    root = Path(runs_dir)
    root.mkdir(parents=True, exist_ok=True)
    checklist = {
        "schema": "hermes_edge_open_boundary_checklist_v1",
        "status": "human_authority_required",
        "ts": _now(),
        "profile_id": status_readback.get("profile_id"),
        "execute": False,
        "runtime_effect": "local_write_checklist_only",
        "status_readback_path": str(Path(status_readback_path)),
        "authority_required": {
            "exact_target": True,
            "execute_true_packet": True,
            "rollback_restore_path": True,
            "post_action_readback": True,
            "human_operator_ack": True,
        },
        "remaining_blockers": ["exact_runtime_activation_packet_execute_true"],
        "closed_gates": status_readback.get("closed_gates", _closed_gate_summary(status_readback)),
    }
    checklist_path = root / f"edge-open-boundary-checklist-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    checklist["checklist_path"] = str(checklist_path)
    checklist_path.write_text(json.dumps(checklist, ensure_ascii=False, indent=2), encoding="utf-8")
    return checklist, 0


def edge_authority_discover(scan_dirs: list[str | Path]) -> dict[str, Any]:
    schemas: dict[str, list[str]] = {}
    profiles: set[str] = set()
    target_candidates: set[str] = set()
    execute_true_packets: list[str] = []
    blockers: set[str] = set()
    for root_value in scan_dirs:
        root = Path(root_value)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".json", ".out"}:
                continue
            payload, _ = _load_json_artifact(path)
            if not isinstance(payload, dict):
                continue
            schema = payload.get("schema")
            if isinstance(schema, str) and schema.startswith("hermes_edge_"):
                schemas.setdefault(schema, []).append(str(path))
            profile_id = payload.get("profile_id")
            if isinstance(profile_id, str):
                profiles.add(profile_id)
            target = payload.get("target")
            if isinstance(target, dict) and isinstance(target.get("id"), str):
                target_candidates.add(target["id"])
            if payload.get("execute") is True:
                execute_true_packets.append(str(path))
            for key in ("remaining_blockers",):
                values = payload.get(key)
                if isinstance(values, list):
                    blockers.update(str(value) for value in values)
            next_auth = payload.get("next_authority_required")
            if isinstance(next_auth, str):
                blockers.add(next_auth)
    opening_ready = bool(execute_true_packets) and not blockers
    if not execute_true_packets:
        blockers.add("exact_runtime_activation_packet_execute_true")
    return {
        "schema": "hermes_edge_authority_discovery_v1",
        "ts": _now(),
        "runtime_effect": "read_only_discovery",
        "scan_dirs": [str(Path(p)) for p in scan_dirs],
        "opening_ready": opening_ready,
        "discovered": {
            "schemas": {k: sorted(v) for k, v in sorted(schemas.items())},
            "profiles": sorted(profiles),
            "target_candidates": sorted(target_candidates),
            "execute_true_packets": sorted(execute_true_packets),
        },
        "blockers": sorted(blockers),
        "recommendation": "ready_for_exact_activation_readback" if opening_ready else "remain_closed_until_execute_true_packet_and_exact_runtime_readback_exist",
    }


def bundled_cases_dir() -> Path:
    """Return the packaged seed eval cases directory.

    Supports both source checkouts and installed distributions that ship evals
    through ``setup.py`` data_files.  The function is intentionally explicit so
    enterprise/private-repo installs can self-check their bundled reliability
    baseline without relying on a user-local profile path.
    """

    candidates = [
        Path(__file__).resolve().parents[1] / "evals" / "cases",  # source checkout
        Path(sys.prefix) / "evals" / "cases",  # setup.py data_files install
        Path(sysconfig.get_path("data") or sys.prefix) / "evals" / "cases",
    ]
    for candidate in candidates:
        if candidate.exists() and any(candidate.glob("*.json")):
            return candidate
    raise FileNotFoundError(
        "Bundled eval cases not found. Pass --cases-dir explicitly or reinstall a distribution that packages evals/cases."
    )


def _resolve_cases_dir(value: str | None) -> Path:
    return Path(value) if value else bundled_cases_dir()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes offline Agent Reliability Harness")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a single eval case offline")
    run.add_argument("--cases-dir", help="Eval cases directory; defaults to bundled evals/cases")
    run.add_argument("--runs-dir", required=True)
    run.add_argument("--case", required=True)
    run.add_argument("--response-text", default="")
    run.add_argument("--router-mode", default="shadow", choices=["off", "shadow", "suggest", "limited-enforce"])
    run.add_argument("--json", action="store_true")

    list_cases = sub.add_parser("list", help="List eval cases")
    list_cases.add_argument("--cases-dir", help="Eval cases directory; defaults to bundled evals/cases")
    list_cases.add_argument("--json", action="store_true")

    capabilities = sub.add_parser("capabilities", help="Print the local skill-pack + lifecycle command contract")
    capabilities.add_argument("--json", action="store_true")

    edge_benchmark = sub.add_parser("edge-benchmark", help="Write a local-only edge runtime benchmark receipt")
    edge_benchmark.add_argument("--runs-dir", required=True)
    edge_benchmark.add_argument("--profile", default="local_edge_worker")
    edge_benchmark.add_argument("--json", action="store_true")

    edge_verify = sub.add_parser("edge-verify", help="Verify an edge benchmark receipt without opening runtime gates")
    edge_verify.add_argument("--receipt", required=True)
    edge_verify.add_argument("--json", action="store_true")

    edge_activation = sub.add_parser(
        "edge-activation-packet",
        help="Write a non-executing edge runtime activation authority packet template",
    )
    edge_activation.add_argument("--verification", required=True)
    edge_activation.add_argument("--target-id", required=True)
    edge_activation.add_argument("--runs-dir", required=True)
    edge_activation.add_argument("--json", action="store_true")

    edge_kill = sub.add_parser(
        "edge-kill-switch-dry-run",
        help="Write a local kill-switch/rollback dry-run receipt without contacting runtime",
    )
    edge_kill.add_argument("--activation-packet", required=True)
    edge_kill.add_argument("--runs-dir", required=True)
    edge_kill.add_argument("--json", action="store_true")

    edge_status = sub.add_parser(
        "edge-status-readback",
        help="Read benchmark/verification/activation/kill-switch artifacts and summarize closed edge readiness",
    )
    edge_status.add_argument("--benchmark", required=True)
    edge_status.add_argument("--verification", required=True)
    edge_status.add_argument("--activation-packet", required=True)
    edge_status.add_argument("--kill-switch-receipt", required=True)
    edge_status.add_argument("--json", action="store_true")

    edge_checklist = sub.add_parser(
        "edge-open-boundary-checklist",
        help="Write a non-executing authority checklist for the remaining edge activation boundary",
    )
    edge_checklist.add_argument("--status-readback", required=True)
    edge_checklist.add_argument("--runs-dir", required=True)
    edge_checklist.add_argument("--json", action="store_true")

    edge_discover = sub.add_parser(
        "edge-authority-discover",
        help="Scan existing local artifacts for edge authority candidates and blockers",
    )
    edge_discover.add_argument("--scan-dir", action="append", required=True)
    edge_discover.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        cases = CaseRegistry(_resolve_cases_dir(args.cases_dir)).list_cases()
        payload = [{"id": c.id, "workflow": c.workflow, "risk": c.risk, "case_hash": c.case_hash} for c in cases]
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for case in payload:
                print(f"{case['id']}\t{case['workflow']}\t{case['risk']}\t{case['case_hash'][:12]}")
        return 0

    if args.command == "capabilities":
        payload = LifecycleContractRegistry.default().as_dict()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("# Hermes Agent Lifecycle Capabilities")
            print("skill_packs:")
            for pack in payload["skill_packs"]:
                print(f"- {pack['id']}: {pack['responsibility']}")
            print("commands:")
            for command in payload["commands"]:
                print(f"- {command['id']}: {command['responsibility']}")
            print("edge_runtime_profiles:")
            for profile in payload["edge_runtime_profiles"]:
                print(f"- {profile['id']}: {profile['responsibility']}")
        return 0

    if args.command == "edge-benchmark":
        try:
            receipt = write_edge_benchmark_receipt(args.runs_dir, args.profile)
        except KeyError as exc:
            parser.error(f"unknown edge runtime profile: {exc.args[0]}")
        if args.json:
            print(json.dumps({"ok": True, "profile_id": receipt["profile_id"], "receipt_path": receipt["receipt_path"]}, ensure_ascii=False))
        else:
            print(f"profile={receipt['profile_id']} receipt={receipt['receipt_path']}")
        return 0

    if args.command == "edge-verify":
        verification = verify_edge_benchmark_receipt(args.receipt)
        if args.json:
            print(json.dumps(verification, ensure_ascii=False))
        else:
            print(f"status={verification['status']} profile={verification.get('profile_id')} promotion_ready={verification['promotion_ready']}")
            for check in verification["checks"]:
                print(f"- {check['status']} {check['name']}: {check['evidence']}")
        return 0 if verification["status"] == "pass" else 1

    if args.command == "edge-activation-packet":
        packet, code = write_edge_activation_packet(args.verification, args.runs_dir, args.target_id)
        if args.json:
            print(json.dumps(packet, ensure_ascii=False))
        else:
            if code == 0:
                print(f"status={packet['status']} execute={packet['execute']} packet={packet['packet_path']}")
            else:
                print(f"status={packet['status']} reason={packet['reason']}")
        return code

    if args.command == "edge-kill-switch-dry-run":
        receipt, code = write_edge_kill_switch_dry_run(args.activation_packet, args.runs_dir)
        if args.json:
            print(json.dumps(receipt, ensure_ascii=False))
        else:
            if code == 0:
                print(f"status={receipt['status']} disabled={receipt['status_after_dry_run']} receipt={receipt['receipt_path']}")
            else:
                print(f"status={receipt['status']} reason={receipt['reason']}")
        return code

    if args.command == "edge-status-readback":
        status, code = edge_status_readback(
            args.benchmark,
            args.verification,
            args.activation_packet,
            args.kill_switch_receipt,
        )
        if args.json:
            print(json.dumps(status, ensure_ascii=False))
        else:
            print(f"status={status['status']} execute={status['execute']} next={status['next_authority_required']}")
        return code

    if args.command == "edge-open-boundary-checklist":
        checklist, code = write_edge_open_boundary_checklist(args.status_readback, args.runs_dir)
        if args.json:
            print(json.dumps(checklist, ensure_ascii=False))
        else:
            if code == 0:
                print(f"status={checklist['status']} execute={checklist['execute']} checklist={checklist['checklist_path']}")
            else:
                print(f"status={checklist['status']} reason={checklist['reason']}")
        return code

    if args.command == "edge-authority-discover":
        report = edge_authority_discover(args.scan_dir)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"opening_ready={report['opening_ready']} recommendation={report['recommendation']}")
            print(f"profiles={','.join(report['discovered']['profiles'])}")
            print(f"targets={','.join(report['discovered']['target_candidates'])}")
            print(f"blockers={','.join(report['blockers'])}")
        return 0

    if args.command == "run":
        harness = EvalHarness(cases_dir=_resolve_cases_dir(args.cases_dir), runs_dir=args.runs_dir, router_mode=args.router_mode)
        result = harness.run_case(args.case, response_text=args.response_text)
        payload = result.as_dict()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"case={result.case.id} passed={result.passed} score={result.score} report={result.report_path}")
        return 0 if result.passed else 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
