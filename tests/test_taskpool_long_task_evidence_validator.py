from scripts.taskpool_long_task_evidence_validator import validate_candidate


def test_rejects_vague_high_tool_call_claim_without_evidence_slots():
    result = validate_candidate(
        {
            "title": "Adopt Qwen long-task capability",
            "capability_delta": "Qwen can handle 1000+ tool calls, so Hermes should trust it for long tasks.",
            "acceptance_evidence": "benchmark article says it works",
        }
    )

    assert result.long_task_claim is True
    assert result.ok is False
    assert result.missing_slots == [
        "checkpoints",
        "tool_logs",
        "resume_state",
        "error_classes",
        "final_verification",
    ]
    assert result.reasons == [
        "missing_long_task_evidence_slots:checkpoints,tool_logs,resume_state,error_classes,final_verification"
    ]


def test_accepts_claim_with_checkpoint_tool_log_resume_error_and_verification():
    result = validate_candidate(
        {
            "title": "Harden Hermes long-task execution evidence",
            "capability_delta": "Hermes records checkpointed long-task evidence before closing high tool-call claims.",
            "acceptance_evidence": (
                "checkpoint:/tmp/run.state; "
                "tool-log:/tmp/tools.jsonl count=1200; "
                "resume-state:continuation-packet; "
                "error-class:quota_timeout_retryable; "
                "final-verification:pytest tests/test_long_task_evidence.py"
            ),
        }
    )

    assert result.long_task_claim is True
    assert result.ok is True
    assert result.present_slots == [
        "checkpoints",
        "tool_logs",
        "resume_state",
        "error_classes",
        "final_verification",
    ]
    assert result.missing_slots == []
    assert result.reasons == ["long_task_evidence_slots_present"]


def test_weak_only_generic_proof_does_not_close_long_task_claim():
    result = validate_candidate(
        {
            "title": "Long task proof cleanup",
            "acceptance_evidence": "py_compile PASS; selfcheck PASS; proof-check PASS; sha256 proof hash",
        }
    )

    assert result.long_task_claim is True
    assert result.ok is False
    assert result.weak_only_evidence is True
    assert "weak_only_evidence_cannot_satisfy_long_task_claim" in result.reasons
