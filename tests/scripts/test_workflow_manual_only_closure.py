from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_manual_only_closure.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_manual_only_closure", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(root: Path, rel: str, payload: dict):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))


def seed(root: Path):
    write(root, "docs/private/workflow-kernel-unattended-decision-record/workflow_kernel_unattended_decision_record_20260613_phase63.json", {"decision": "keep_manual_only", "selected_option": "A_KEEP_MANUAL_ONLY", "record_only": True, "record_digest": "rd"})
    write(root, "docs/private/workflow-kernel-unattended-dispatch-enablement-packet/workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json", {"unattended_dispatch_enabled": False})
    write(root, "docs/private/workflow-kernel-post-send-correlation-watch/workflow_kernel_post_send_correlation_watch_20260613_phase61.json", {"compensation_watch": {"new_message_sent": False}})
    write(root, "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json", {"status": "ONE_SHOT_GUARDED_SEND_VERIFIED"})
    write(root, "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json", {"would_send": False})


def test_manual_only_closure_ready(tmp_path):
    mod = load()
    seed(tmp_path)
    closure = mod.build_closure(tmp_path)
    assert closure["status"] == "MANUAL_ONLY_CLOSURE_READY"
    assert closure["final_mode"] == "manual_only_with_no_send_dry_run_cron"
    assert closure["unattended_dispatch_enabled"] is False
    assert closure["external_side_effects_executed"] is False
    assert "unattended external dispatch" in closure["closed_paths_without_new_authorization"]


def test_closure_blocks_if_decision_is_not_manual_only(tmp_path):
    mod = load()
    seed(tmp_path)
    write(tmp_path, "docs/private/workflow-kernel-unattended-decision-record/workflow_kernel_unattended_decision_record_20260613_phase63.json", {"decision": "approve_dry_run_only", "selected_option": "B_APPROVE_DRY_RUN_ONLY_AUTOMATION", "record_only": True})
    closure = mod.build_closure(tmp_path)
    assert closure["status"] == "MANUAL_ONLY_CLOSURE_BLOCKED"
    assert "phase63_keep_manual_only" in closure["blockers"]
