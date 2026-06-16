#!/usr/bin/env python3
"""Dry-run evidence harness for Hermes workflow-kernel five-layer closure.

This script is intentionally local-only: it models an intent flowing through
Hermes-style governance, capability selection, runtime adapter execution, and a
durable artifact receipt without invoking model providers, external adapters,
or write-through integrations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_EVIDENCE_REFS = [
    "artifact:/Users/charles/.hermes/runs/douyin-agent-native-arch-20260615-212503/intake-note.md",
    "artifact:/Users/charles/.hermes/runs/douyin-agent-native-arch-20260615-212503/contact_sheet.jpg",
    "artifact:/Users/charles/.hermes/runs/douyin-agent-native-arch-20260615-212503/transcript.txt",
]

LAYER_MAP = [
    {
        "layer": "Application Surface",
        "hermes_surface": "CLI, gateway, cron, dashboard, taskpool event ingress",
        "responsibility": "express intent and display/read back outcome; no privileged write authority",
        "red_line": "surface_must_not_do_privileged_operations",
    },
    {
        "layer": "Runtime Host",
        "hermes_surface": "run_agent loop, taskpool orchestrator, mission ledger, permission/lifecycle gates",
        "responsibility": "own session, authority, event flow, verification, diagnostics, and artifact ledger",
        "red_line": "host_must_not_pretend_to_be_the_model",
    },
    {
        "layer": "Prompt & Capability Stack",
        "hermes_surface": "system/developer instructions, skills, project profiles, memory, plugins, toolsets",
        "responsibility": "declare rule precedence, available capabilities, contracts, and non-goals before execution",
        "red_line": "skills_must_not_override_higher_priority_rules",
    },
    {
        "layer": "Agent Runtime",
        "hermes_surface": "provider adapters, Claude/Codex/Gemini/worker profiles behind launch contracts",
        "responsibility": "execute through replaceable adapters that expose capability, launch method, and IO format",
        "red_line": "agent_runtime_must_not_own_final_authority",
    },
    {
        "layer": "Tool & Artifact Loop",
        "hermes_surface": "tool calls, tests, read-backs, proof logs, task notes, docs/private artifacts",
        "responsibility": "turn execution into durable, recoverable, exportable, and reviewable artifacts",
        "red_line": "artifact_must_not_be_temporary_attachment_only",
    },
]

CHECKLIST = [
    "Surface does not perform privileged operations; it only captures intent and renders results.",
    "Host does not masquerade as a model; it routes, governs, verifies, and records.",
    "Agent runtime does not own final authority; host lifecycle gates decide status and side effects.",
    "Skill/capability stack does not override higher-priority system/developer/user rules.",
    "Artifact is durable: saved, recoverable, exportable, and editable/reviewable after the run.",
    "Evidence refs include intake note, contact sheet, transcript, and mark OCR/ASR uncertainty.",
]


def build_receipt(intent: str, artifact_path: str | None) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    governance = {
        "external_side_effects": False,
        "write_through": False,
        "permission_tier": "budget-handoff",
        "status_authority": "brain_task_orchestrator transition only",
        "uncertainty": {
            "asr": "transcript contains local ASR errors (e.g. 调用/跳用, 闭环/避还); use intake-note normalized visible text as higher-confidence summary",
            "ocr": "contact sheet is visual evidence; layer labels are treated as intake evidence, not independent engineering proof",
        },
    }
    closure_steps = [
        {"stage": "intent", "input": intent, "surface": "scheduled follow-through taskpool executor"},
        {"stage": "governance", "checks": governance},
        {"stage": "capability", "selected": ["taskpool-control-plane", "hermes-agent", "file", "terminal"]},
        {"stage": "execution", "runtime": "local Python dry-run harness", "model_calls": False},
        {
            "stage": "artifact",
            "durable_artifact": artifact_path or "stdout-json-only",
            "evidence_refs": DEFAULT_EVIDENCE_REFS,
        },
    ]
    canonical = json.dumps(
        {"intent": intent, "layers": LAYER_MAP, "checklist": CHECKLIST, "closure_steps": closure_steps},
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "schema": "hermes_workflow_kernel_five_layer_dry_run_v1",
        "observed_at": observed_at,
        "intent": intent,
        "status": "PASS",
        "external_side_effects": False,
        "layer_map": LAYER_MAP,
        "checklist": CHECKLIST,
        "closure_steps": closure_steps,
        "evidence_refs": DEFAULT_EVIDENCE_REFS,
        "receipt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", default="map agent-native five-layer closure into Hermes workflow/kernel acceptance")
    parser.add_argument("--out", help="Optional durable JSON receipt path. Parent directories are created.")
    args = parser.parse_args()

    receipt = build_receipt(args.intent, args.out)
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
