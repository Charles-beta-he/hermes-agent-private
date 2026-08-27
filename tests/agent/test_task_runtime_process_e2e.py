from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _run(code: str, *, db_path: Path) -> dict:
    env = os.environ.copy()
    env["HERMES_TASK_RUNTIME_DB"] = str(db_path)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_cli_and_telegram_processes_recover_the_same_durable_task(tmp_path):
    db_path = tmp_path / "task-runtime.db"
    created = _run(
        """
import json
from tools.task_runtime_tool import task_runtime_tool
print(task_runtime_tool(
    action='create',
    entrypoint='cli',
    session_id='cli-process-1',
    title='process reconstruction',
    original_sources=[{
        'source_id': 'S-U1',
        'kind': 'user_message',
        'content': 'exact original turn',
        'coordinates': {'entrypoint': 'cli', 'turn': 1},
    }],
))
""",
        db_path=db_path,
    )
    assert created["success"] is True
    task_id = created["task"]["task_id"]

    telegram = _run(
        f"""
import json
from tools.task_runtime_tool import task_runtime_tool
print(task_runtime_tool(
    action='bind',
    task_id={task_id!r},
    entrypoint='telegram',
    target_session_id='telegram:42:7',
))
""",
        db_path=db_path,
    )
    assert telegram["success"] is True
    assert telegram["task"]["task_id"] == task_id

    recovered_cli = _run(
        """
from tools.task_runtime_tool import task_runtime_tool
print(task_runtime_tool(action='status', entrypoint='cli', session_id='cli-process-1'))
""",
        db_path=db_path,
    )
    recovered_telegram = _run(
        """
from tools.task_runtime_tool import task_runtime_tool
print(task_runtime_tool(action='status', entrypoint='telegram', session_id='telegram:42:7'))
""",
        db_path=db_path,
    )
    assert recovered_cli["task"]["task_id"] == task_id
    assert recovered_telegram["task"]["task_id"] == task_id
    assert {item["entrypoint"] for item in recovered_telegram["task"]["bindings"]} == {"cli", "telegram"}

    source_readback = _run(
        f"""
import json
from agent.task_runtime import TaskRuntimeStore
store = TaskRuntimeStore.default_path()
task = TaskRuntimeStore(store).get_task({task_id!r})
print(json.dumps(task['original_sources'][0], ensure_ascii=False))
""",
        db_path=db_path,
    )
    assert source_readback["content"] == "exact original turn"
    assert source_readback["coordinates"] == {"entrypoint": "cli", "turn": 1}
