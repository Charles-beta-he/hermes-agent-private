from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_kernel_task_closeout_dry_run.py"
    spec = importlib.util.spec_from_file_location("_workflow_kernel_task_closeout_dry_run", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _task_note(tmp_path: Path) -> Path:
    path = tmp_path / "task.md"
    path.write_text(
        """---
status: review
---
# Task
- [ ] 映射说明覆盖 Application Surface、Runtime Host、Prompt & Capability Stack、Agent Runtime、Tool & Artifact Loop，并明确对应 Hermes 当前/目标组件。
- [ ] Checklist 至少包含：Surface 不做特权操作、Host 不伪装模型、Agent 不拥有最终权限、Skill 不覆盖系统规则、Artifact 不只是临时附件。
- [ ] Dry-run harness/example 有可读输出和 evidence ref，能展示一次任务从入口到 durable artifact 的治理链路；不打开外部写副作用。
- [ ] Evidence bundle 回链 intake-note、contact sheet、transcript，并标注 ASR/OCR 不确定项。
""",
        encoding="utf-8",
    )
    return path


def _receipt(tmp_path: Path, **overrides) -> Path:
    payload = {
        "dry_run_only": True,
        "local_reversible_only": True,
        "runtime_enabled": False,
        "write_through_enabled": False,
        "external_side_effects_enabled": False,
        "task_lifecycle_mutation_enabled": False,
        "model_call_enabled": False,
        "verdict": "implementation_ready_not_runtime_enabled",
        "failures": [],
    }
    payload.update(overrides)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_closeout_dry_run_proposes_human_gated_update_without_mutation(tmp_path):
    module = _load_module()

    result = module.build_closeout_dry_run(_task_note(tmp_path), _receipt(tmp_path))

    assert result["dry_run_only"] is True
    assert result["local_reversible_only"] is True
    assert result["task_note_mutated"] is False
    assert result["task_lifecycle_mutation_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["runtime_enabled"] is False
    assert result["human_intervention_required_before_write"] is True
    assert result["verdict"] == "ready_for_human_gated_lifecycle_update"
    assert result["missing_task_snippets"] == []
    assert result["receipt_failures"] == []


def test_closeout_dry_run_blocks_missing_task_evidence(tmp_path):
    module = _load_module()
    task = tmp_path / "task.md"
    task.write_text("---\nstatus: review\n---\n# Task\n- [ ] incomplete\n", encoding="utf-8")

    result = module.build_closeout_dry_run(task, _receipt(tmp_path))

    assert result["verdict"] == "blocked_by_missing_closeout_evidence"
    assert result["missing_task_snippets"]
    assert result["task_note_mutated"] is False


def test_closeout_dry_run_blocks_receipt_runtime_enablement(tmp_path):
    module = _load_module()

    result = module.build_closeout_dry_run(_task_note(tmp_path), _receipt(tmp_path, runtime_enabled=True))

    assert result["verdict"] == "blocked_by_missing_closeout_evidence"
    assert "forbidden_receipt_flag_not_false:runtime_enabled" in result["receipt_failures"]


def test_closeout_dry_run_writes_local_receipt_only(tmp_path):
    module = _load_module()
    result = module.build_closeout_dry_run(_task_note(tmp_path), _receipt(tmp_path))

    path = module.write_result(tmp_path / "out", result)

    assert path.exists()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["result_id"] == result["result_id"]
    assert written["task_lifecycle_mutation_enabled"] is False
