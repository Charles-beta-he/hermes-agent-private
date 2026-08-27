"""Model-facing schema for bounded fresh task-runtime worker dispatch."""

from __future__ import annotations

from typing import Any

from tools.registry import registry

TOOL_SCHEMA = {
    "name": "task_runtime_worker",
    "description": (
        "Claim one ready slice from a durable task and execute it in a fresh, bounded "
        "Hermes session using its immutable ContextPacket. The worker can only submit "
        "a structured result for later deterministic integration; it cannot close the task."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Durable task identifier."},
            "lease_seconds": {
                "type": "integer",
                "minimum": 30,
                "maximum": 3600,
                "default": 900,
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    },
}


def task_runtime_worker_tool(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    # The live Agent object is required to inherit provider/tool policy and create
    # a true fresh session. agent.tool_executor intercepts this tool before the
    # generic registry dispatch path.
    return {"success": False, "error": "task_runtime_worker_requires_live_agent"}


registry.register(
    name="task_runtime_worker",
    toolset="delegation",
    schema=TOOL_SCHEMA,
    handler=task_runtime_worker_tool,
    emoji="🧵",
)
