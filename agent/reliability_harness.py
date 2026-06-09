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
import re
import sqlite3
import sys
import sysconfig
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
        return [self.load_file(path) for path in sorted(self.root.glob("*.json"))]


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
