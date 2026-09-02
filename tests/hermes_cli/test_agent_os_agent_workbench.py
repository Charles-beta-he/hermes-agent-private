from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.agent_workbench_projection import (
    ReviewPacketTemplateError,
    TaskStateProjectionError,
    build_review_packet_template,
    build_task_state_projection,
)
from hermes_cli.subcommands.agent_os import build_agent_os_parser


def test_task_state_projection_summarizes_tasks_without_opening_runtime_gates() -> None:
    payload = build_task_state_projection(
        [
            {
                "task_id": "P1.task_state_projection_impl",
                "title": "Task state projection implementation",
                "status": "in_progress",
                "worktree": "/Users/charles/.hermes/worktrees/hermes-agent-superset-workbench-p1",
                "branch": "agent/superset-workbench-p1-20260902",
                "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                "evidence_refs": ["authority:11-p1-local-source-authority-packet.json"],
                "allowed_effects": ["local_source_write", "local_test"],
            },
            {
                "task_id": "P1.review_packet_template_impl",
                "title": "Review packet template implementation",
                "status": "blocked_by_authority",
                "worktree": "/Users/charles/.hermes/worktrees/hermes-agent-superset-workbench-p1",
                "branch": "agent/superset-workbench-p1-20260902",
                "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                "evidence_refs": ["authority:11-p1-local-source-authority-packet.json"],
                "allowed_effects": ["local_source_write", "local_test"],
            },
        ],
        generated_at="2026-09-02T00:00:00+00:00",
    )

    assert payload["schema"] == "AgentWorkbenchTaskStateProjection.v0"
    assert payload["mode"] == "local_projection_only"
    assert payload["runtime_activation"] is False
    assert payload["would_execute"] is False
    assert payload["external_side_effects_executed"] is False
    assert payload["summary"] == {
        "task_count": 2,
        "by_status": {"blocked_by_authority": 1, "in_progress": 1},
        "all_tasks_bound_to_worktree": True,
        "all_tasks_have_baseline": True,
        "all_tasks_have_evidence": True,
    }
    assert [task["task_id"] for task in payload["tasks"]] == [
        "P1.review_packet_template_impl",
        "P1.task_state_projection_impl",
    ]


def test_task_state_projection_rejects_high_side_effect_allowed_effects() -> None:
    try:
        build_task_state_projection(
            [
                {
                    "task_id": "P1.bad",
                    "title": "Bad task",
                    "status": "pending",
                    "worktree": "/tmp/wt",
                    "branch": "branch",
                    "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                    "evidence_refs": ["authority:packet"],
                    "allowed_effects": ["local_source_write", "external_dispatch"],
                }
            ]
        )
    except TaskStateProjectionError as exc:
        assert exc.code == "high_side_effect_not_allowed"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected high-side-effect task rejection")


def test_review_packet_template_binds_candidate_identity_and_negative_boundaries() -> None:
    task_projection = build_task_state_projection(
        [
            {
                "task_id": "P1.task_state_projection_impl",
                "title": "Task state projection implementation",
                "status": "ready_for_review",
                "worktree": "/Users/charles/.hermes/worktrees/hermes-agent-superset-workbench-p1",
                "branch": "agent/superset-workbench-p1-20260902",
                "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                "evidence_refs": ["pytest:pass"],
                "allowed_effects": ["local_source_write", "local_test"],
            }
        ],
        generated_at="2026-09-02T00:00:00+00:00",
    )

    packet = build_review_packet_template(
        task_projection,
        candidate_digest="sha256:" + "a" * 64,
        changed_files=[
            "hermes_cli/agent_workbench_projection.py",
            "tests/hermes_cli/test_agent_os_agent_workbench.py",
        ],
        required_verification=["python -m pytest tests/hermes_cli/test_agent_os*.py -q"],
        generated_at="2026-09-02T00:01:00+00:00",
    )

    assert packet["schema"] == "AgentWorkbenchReviewPacketTemplate.v0"
    assert packet["status"] == "READY_FOR_LOCAL_REVIEW"
    assert packet["candidate_digest"] == "sha256:" + "a" * 64
    assert packet["task_projection_digest"].startswith("sha256:")
    assert packet["negative_boundaries"] == {
        "runtime_activation": False,
        "would_execute": False,
        "gateway_scheduler_taskpool_lifecycle_mutation": False,
        "external_dispatch": False,
        "sudo": False,
        "push_deploy_release": False,
    }
    assert packet["review_questions"][0]["id"] == "candidate_identity_bound"
    assert packet["required_verification"] == ["python -m pytest tests/hermes_cli/test_agent_os*.py -q"]
    assert packet["candidate_digest_recipe"] == {
        "algorithm": "sha256",
        "member_order": "changed file paths sorted lexicographically by repository-relative path",
        "framing": "mode + NUL + path + NUL + decimal byte length + NUL + bytes",
        "mode_encoding": "python stat octal literal spelling: regular files are 0o644 or 0o755; symlinks are 0o120000; deletes use D",
        "delete_member": "D + NUL + path + NUL + 0 + NUL, emitted after live members in sorted path order",
    }


def test_review_packet_template_rejects_runtime_activated_projection() -> None:
    projection = build_task_state_projection(
        [
            {
                "task_id": "P1.task",
                "title": "Task",
                "status": "pending",
                "worktree": "/tmp/wt",
                "branch": "branch",
                "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                "evidence_refs": ["authority:packet"],
                "allowed_effects": ["local_test"],
            }
        ]
    )
    projection["runtime_activation"] = True

    try:
        build_review_packet_template(
            projection,
            candidate_digest="sha256:" + "a" * 64,
            changed_files=["hermes_cli/agent_workbench_projection.py"],
            required_verification=["pytest"],
        )
    except ReviewPacketTemplateError as exc:
        assert exc.code == "task_projection_runtime_gate_open"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected runtime gate rejection")


def test_review_packet_template_rejects_runtime_side_effect_projection_flags() -> None:
    projection = build_task_state_projection(
        [
            {
                "task_id": "P1.task",
                "title": "Task",
                "status": "pending",
                "worktree": "/tmp/wt",
                "branch": "branch",
                "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                "evidence_refs": ["authority:packet"],
                "allowed_effects": ["local_test"],
            }
        ]
    )
    projection["external_side_effects_executed"] = True

    try:
        build_review_packet_template(
            projection,
            candidate_digest="sha256:" + "a" * 64,
            changed_files=["hermes_cli/agent_workbench_projection.py"],
            required_verification=["pytest"],
        )
    except ReviewPacketTemplateError as exc:
        assert exc.code == "task_projection_runtime_gate_open"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected external side effect rejection")


def test_review_packet_template_rejects_task_level_runtime_gate_open() -> None:
    projection = build_task_state_projection(
        [
            {
                "task_id": "P1.task",
                "title": "Task",
                "status": "pending",
                "worktree": "/tmp/wt",
                "branch": "branch",
                "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                "evidence_refs": ["authority:packet"],
                "allowed_effects": ["local_test"],
            }
        ]
    )
    projection["tasks"][0]["would_execute"] = True

    try:
        build_review_packet_template(
            projection,
            candidate_digest="sha256:" + "a" * 64,
            changed_files=["hermes_cli/agent_workbench_projection.py"],
            required_verification=["pytest"],
        )
    except ReviewPacketTemplateError as exc:
        assert exc.code == "task_runtime_gate_open"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected task-level runtime gate rejection")


def test_review_packet_template_rejects_failed_projection_payload() -> None:
    failed_projection = {
        "ok": False,
        "schema": "AgentWorkbenchTaskStateProjection.v0",
        "mode": "local_projection_only",
        "runtime_activation": False,
        "would_execute": False,
    }

    try:
        build_review_packet_template(
            failed_projection,
            candidate_digest="sha256:" + "a" * 64,
            changed_files=["hermes_cli/agent_workbench_projection.py"],
            required_verification=["pytest"],
        )
    except ReviewPacketTemplateError as exc:
        assert exc.code == "invalid_task_projection"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected failed projection rejection")


def test_review_packet_template_rejects_malformed_candidate_digest() -> None:
    projection = build_task_state_projection(
        [
            {
                "task_id": "P1.task",
                "title": "Task",
                "status": "pending",
                "worktree": "/tmp/wt",
                "branch": "branch",
                "baseline_commit": "d28769d000146a8b15dfc23bda85ee5f0a175028",
                "evidence_refs": ["authority:packet"],
                "allowed_effects": ["local_test"],
            }
        ]
    )

    try:
        build_review_packet_template(
            projection,
            candidate_digest="sha256:not-hex",
            changed_files=["hermes_cli/agent_workbench_projection.py"],
            required_verification=["pytest"],
        )
    except ReviewPacketTemplateError as exc:
        assert exc.code == "invalid_candidate_digest"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected digest rejection")


def test_agent_os_parser_exposes_workbench_read_only_commands() -> None:
    parser = __import__("argparse").ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_agent_os_parser(subparsers)

    state_args = parser.parse_args(["agent-os", "workbench-state", "--input", "tasks.json", "--json"])
    assert state_args.agent_os_action == "workbench-state"
    assert state_args.json is True

    review_args = parser.parse_args([
        "agent-os",
        "workbench-review-template",
        "--input",
        "projection.json",
        "--candidate-digest",
        "sha256:" + "a" * 64,
        "--changed-file",
        "hermes_cli/agent_workbench_projection.py",
        "--required-verification",
        "pytest",
        "--json",
    ])
    assert review_args.agent_os_action == "workbench-review-template"
    assert review_args.changed_file == ["hermes_cli/agent_workbench_projection.py"]
