#!/usr/bin/env python3
"""Ledger-aware background task worker.

The worker lives in the tracked package so its probe and dry-run lifecycle
contracts are testable on clean installations. Real model execution remains
intentionally unwired.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict

from agent.conductor_runtime import ACTIVE_STATUSES, TaskLedger


def _now() -> float:
    return time.time()


class BackgroundTaskWorker:
    """Execute a background task with ledger-aware interrupt support."""

    def __init__(self, task_id: str, ledger_path: str | Path = Path.home() / ".hermes" / "task_ledger.db"):
        self.task_id = task_id
        self.ledger = TaskLedger(ledger_path)
        self._started_at = _now()

    def probe(self) -> Dict[str, Any]:
        """Return current task state without executing anything."""
        task = self.ledger.get_task(self.task_id)
        if not task:
            return {"error": f"task not found: {self.task_id}"}
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "title": task["title"],
            "current_goal": task["current_goal"],
            "latest_user_directive": task["latest_user_directive"],
            "constraints": task["constraints"],
            "interrupt_policy": task["interrupt_policy"],
            "multimodel_mode": task["multimodel_mode"],
            "last_summary": task["last_summary"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "worker_session_id": self._worker_session_id(),
        }

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """Run the task lifecycle without external execution in dry-run mode."""
        task = self.ledger.get_task(self.task_id)
        if not task:
            return {"error": f"task not found: {self.task_id}"}

        if task["status"] in {"queued", "paused"}:
            self.ledger._set_status(
                self.task_id,
                "running",
                action="worker_start",
                reason="background worker started",
            )
        elif task["status"] not in ACTIVE_STATUSES:
            return {"error": f"task {self.task_id} not in active state: {task['status']}"}

        task = self.ledger.get_task(self.task_id) or task
        result: Dict[str, Any] = {
            "task_id": self.task_id,
            "worker_session_id": self._worker_session_id(),
            "started_at": self._started_at,
            "initial_goal": task["current_goal"],
            "initial_constraints": task["constraints"],
            "interrupt_policy": task["interrupt_policy"],
            "multimodel_mode": task["multimodel_mode"],
            "status": "running",
            "checkpoints": [],
            "directives_received": [],
        }

        if dry_run:
            result["checkpoints"].append({"ts": _now(), "summary": "dry-run checkpoint"})
            result["directives_received"].append(
                {
                    "ts": _now(),
                    "directive": task.get("latest_user_directive", ""),
                    "action": "read",
                }
            )
            self.ledger._append_history(
                self.task_id,
                action="checkpoint",
                directive="dry-run checkpoint",
                status="done",
                update_latest_directive=False,
            )
            result["status"] = "done"
            result["completed_at"] = _now()
            return result

        result["status"] = "scaffold_no_execution"
        result["note"] = "Real execution not yet wired; use dry_run=True for integration tests."
        self.ledger._append_history(
            self.task_id,
            action="worker_scaffold",
            directive="real execution pending",
            update_latest_directive=False,
        )
        return result

    def _worker_session_id(self) -> str:
        return f"worker_{self.task_id}_{int(self._started_at)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--task-id", required=True)
    run.add_argument("--ledger", default=str(Path.home() / ".hermes" / "task_ledger.db"))
    run.add_argument("--dry-run", action="store_true", default=False)

    probe = sub.add_parser("probe")
    probe.add_argument("--task-id", required=True)
    probe.add_argument("--ledger", default=str(Path.home() / ".hermes" / "task_ledger.db"))

    args = parser.parse_args()
    worker = BackgroundTaskWorker(args.task_id, args.ledger)
    payload = worker.probe() if args.command == "probe" else worker.run(dry_run=args.dry_run)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
