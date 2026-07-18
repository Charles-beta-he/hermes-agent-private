from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "taskpool_follow_through_wake_gate.py"


def _run_gate(tmp_path: Path, payload: dict) -> dict:
    fake = tmp_path / "orchestrator.py"
    fake.write_text(
        "import json\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HERMES_TASKPOOL_ORCHESTRATOR": str(fake)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_gate_stays_silent_when_taskpool_has_no_dispatchable_work(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, {"next": None, "reconcile": {"changed": 0}})
    assert result == {"status": "no_dispatchable_work", "wakeAgent": False}


def test_gate_wakes_agent_and_injects_only_selected_task(tmp_path: Path) -> None:
    task = {"task_id": "tsk-1", "priority": "P1"}
    result = _run_gate(tmp_path, {"next": task, "reconcile": {"changed": 1}})
    assert result == {
        "next": task,
        "reconcile": {"changed": 1},
        "status": "dispatchable_work",
        "wakeAgent": True,
    }
