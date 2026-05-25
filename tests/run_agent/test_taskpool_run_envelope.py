from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_orchestrator_module():
    script = Path.home() / ".hermes" / "scripts" / "brain_task_orchestrator.py"
    if not script.exists():
        script = Path("/Users/charles/.hermes/scripts/brain_task_orchestrator.py")
    spec = importlib.util.spec_from_file_location("brain_task_orchestrator_under_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_taskpool_lifecycle_transition_writes_unified_run_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/Users/charles/.hermes")
    module = load_orchestrator_module()
    monkeypatch.setenv("HERMES_RUN_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setattr(module, "TASKS_DIR", tmp_path / "brain" / "tasks")

    task_path = module.TASKS_DIR / "2026-05-20-demo.md"
    task_path.parent.mkdir(parents=True)
    task = module.Task(
        path=task_path,
        fm={
            "status": "pending",
            "run_id": "",
            "claimed_by": "",
            "claim_expires_at": "",
            "evidence": [],
        },
        body="# Task - Demo\n",
    )

    module.apply_lifecycle_status(
        task,
        "claimed",
        "tester",
        evidence=["file:/tmp/proof.txt"],
    )

    ledger_event = json.loads((tmp_path / "ledger" / "run_events.jsonl").read_text(encoding="utf-8").splitlines()[-1])

    assert ledger_event["surface"] == "taskpool"
    assert ledger_event["run_id"] == f"taskpool:{task.fm['run_id']}"
    assert ledger_event["lifecycle_state"] == "claimed"
    assert ledger_event["trigger"]["kind"] == "local_brain_taskpool"
    assert ledger_event["trigger"]["task_id"] == task_path.stem
    assert ledger_event["proof_refs"] == ["file:/tmp/proof.txt", f"file:{task_path}"]
    assert {"projection": "brain_markdown", "ref": str(task_path)} in ledger_event["projection_refs"]
