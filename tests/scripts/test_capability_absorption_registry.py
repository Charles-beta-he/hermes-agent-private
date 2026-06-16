from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "capability_absorption_registry.py"


def load():
    spec = importlib.util.spec_from_file_location("capability_absorption_registry", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_task(path: Path, body: str) -> Path:
    path.write_text(body.strip() + "\n", encoding="utf-8")
    return path


def test_backfill_extracts_absorption_records_and_classifies_layers(tmp_path):
    mod = load()
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    write_task(
        tasks / "2026-06-01-evaluate-headroom.md",
        """
---
status: done
priority: P2
project: hermes-agent
architecture_target: local-brain
capability_delta: 为 Hermes 建立可度量、可逆、content-type-aware 的 context compression 控制平面。
evidence_refs: ["url:https://github.com/chopratejas/headroom", "artifact:/tmp/headroom.md"]
acceptance_criteria: ["gap doc", "fixture prototype"]
---
# Task - 评估 Headroom 对 Hermes token/context 控制面的可吸收模式
""",
    )
    write_task(
        tasks / "2026-06-02-normal-work.md",
        """
---
status: pending
priority: P3
project: misc
architecture_target: taskpool
capability_delta: 普通维护任务
---
# Task - normal maintenance
""",
    )
    write_task(
        tasks / "2026-06-03-browser-use.md",
        """
---
status: pending
priority: P2
project: hermes-agent
architecture_target: taskpool
capability_delta: repo_capability_absorption:browser-sandbox-eval->taskpool:evaluate
evidence_refs: ["url:https://github.com/browser-use/browser-use"]
---
# Task - 评估 browser-use 沙箱基准设施对 Hermes 浏览器自动化主链的接入门
""",
    )

    registry = mod.build_registry(tasks_dir=tasks)

    assert registry["schema"] == "capability_absorption_registry_v1"
    assert registry["summary"]["record_count"] == 2
    assert registry["summary"]["by_status"] == {"done": 1, "pending": 1}
    assert registry["summary"]["by_absorption_layer"]["module"] == 2
    ids = {record["task_id"] for record in registry["records"]}
    assert "2026-06-01-evaluate-headroom" in ids
    browser = next(r for r in registry["records"] if "browser-use" in r["task_id"])
    assert browser["source_refs"] == ["url:https://github.com/browser-use/browser-use"]
    assert browser["target_surface"] == "taskpool"
    assert browser["integration_level"] == "evaluate"
    assert browser["absorption_layer"] == "module"


def test_registry_dashboard_lists_open_and_recent_done(tmp_path):
    mod = load()
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    write_task(
        tasks / "done.md",
        """
---
status: done
priority: P2
project: hermes
architecture_target: skill-graph
updated: 2026-06-15T10:00:00+08:00
capability_delta: 吸收 TradingAgents 决策委员会模式到 Hermes goal-calibration。
evidence_refs: ["url:https://github.com/TauricResearch/TradingAgents"]
---
# Task - TradingAgents absorption
""",
    )
    write_task(
        tasks / "open.md",
        """
---
status: pending
priority: P1
project: hermes
architecture_target: taskpool
updated: 2026-06-16T10:00:00+08:00
capability_delta: 吸收 OpenClaw agent role topology 到 workflow_plan_v1。
evidence_refs: ["url:https://github.com/niuma/openclaw"]
---
# Task - OpenClaw absorption
""",
    )
    registry = mod.build_registry(tasks_dir=tasks)
    artifacts = mod.write_artifacts(registry, tmp_path / "out")

    md = Path(artifacts["dashboard_md"]).read_text(encoding="utf-8")
    assert "Absorption progress" in md
    assert "pending" in md
    assert "OpenClaw" in md
    assert "TradingAgents" in md
    assert Path(artifacts["registry_json"]).exists()
    loaded = json.loads(Path(artifacts["registry_json"]).read_text(encoding="utf-8"))
    assert loaded["summary"]["open_count"] == 1
