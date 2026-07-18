#!/usr/bin/env python3
"""Wake the taskpool follow-through agent only when work is dispatchable."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_ORCHESTRATOR = Path.home() / ".hermes" / "scripts" / "brain_task_orchestrator.py"


def main() -> int:
    orchestrator = Path(
        os.environ.get("HERMES_TASKPOOL_ORCHESTRATOR", str(DEFAULT_ORCHESTRATOR))
    ).expanduser()
    command = [sys.executable, str(orchestrator), "next", "--reconcile", "--json"]
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except Exception as exc:
        print(json.dumps({
            "wakeAgent": True,
            "status": "precheck_failed",
            "error_type": type(exc).__name__,
        }, ensure_ascii=False, sort_keys=True))
        return 0

    if completed.returncode != 0:
        print(json.dumps({
            "wakeAgent": True,
            "status": "precheck_failed",
            "returncode": completed.returncode,
        }, ensure_ascii=False, sort_keys=True))
        return 0

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        print(json.dumps({
            "wakeAgent": True,
            "status": "precheck_malformed",
            "error_type": type(exc).__name__,
        }, ensure_ascii=False, sort_keys=True))
        return 0

    task = payload.get("next")
    if task is None:
        print(json.dumps({
            "wakeAgent": False,
            "status": "no_dispatchable_work",
        }, ensure_ascii=False, sort_keys=True))
        return 0

    print(json.dumps({
        "wakeAgent": True,
        "status": "dispatchable_work",
        "next": task,
        "reconcile": payload.get("reconcile"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
