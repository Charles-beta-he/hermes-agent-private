import json
from datetime import datetime, timedelta, timezone

from agent.post_chat_reflection import (
    PostChatCandidateLedger,
    build_post_chat_candidates,
    evaluate_candidate_promotion,
    record_post_chat_reflection,
    synthesize_nightly_reflection,
)


def _msg(role, content):
    return {"role": role, "content": content}


def test_post_chat_extractor_captures_explicit_correction_without_promoting(tmp_path):
    messages = [
        _msg("user", "以后多模型分析必须真实外调，不要单模型模拟。"),
        _msg("assistant", "收到。"),
    ]

    candidates = build_post_chat_candidates(messages, session_id="s1")

    assert candidates
    candidate = candidates[0]
    assert candidate["type"] == "correction"
    assert candidate["status"] == "pending"
    assert candidate["proposed_destination"] in {"memory", "skill"}
    assert candidate["promotion_policy"] == "needs_gate"
    assert candidate["evidence_refs"] == ["session:s1:turn:0"]


def test_post_chat_extractor_rejects_ephemeral_and_redacts_sample_value_like_content():
    sample_value = "NOT_A_REAL_SAMPLE_VALUE"
    messages = [
        _msg("user", "本轮任务已完成，PR #123 也合了。"),
        _msg("user", f"我的 token 是 {sample_value}，请记住。"),
    ]

    candidates = build_post_chat_candidates(messages, session_id="s2")

    assert candidates
    assert {c["status"] for c in candidates} == {"rejected"}
    assert {c["rejection_reason"] for c in candidates} >= {"ephemeral", "sensitive"}
    sensitive = [c for c in candidates if c["rejection_reason"] == "sensitive"][0]
    assert sample_value not in json.dumps(sensitive, ensure_ascii=False)
    assert sensitive["content"] == "[REDACTED_SENSITIVE]"


def test_promotion_gate_requires_multiple_evidence_for_inferred_habit():
    candidate = {
        "type": "habit_candidate",
        "explicitness": "inferred",
        "evidence_refs": ["session:a:turn:0"],
        "status": "pending",
        "content": "User seems to prefer short answers.",
    }

    decision = evaluate_candidate_promotion(candidate)

    assert decision["decision"] == "needs_more_evidence"
    assert decision["target"] == "staging"


def test_promotion_gate_allows_explicit_red_line_after_scrub():
    candidate = {
        "type": "red_line",
        "explicitness": "explicit",
        "evidence_refs": ["session:a:turn:0"],
        "status": "pending",
        "content": "用户要求严禁把临时任务进度写入长期 memory。",
        "sensitivity_level": "none",
    }

    decision = evaluate_candidate_promotion(candidate)

    assert decision["decision"] == "promote_allowed"
    assert decision["target"] in {"memory", "skill", "brain_doc"}


def test_record_post_chat_reflection_is_suggest_only_and_config_gated(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    status_path = tmp_path / "status.json"

    disabled = record_post_chat_reflection(
        [_msg("user", "以后先验证再总结。")],
        session_id="s-disabled",
        config={"enabled": False, "ledger_path": str(ledger_path), "status_path": str(status_path)},
    )
    assert disabled["state"] == "disabled"
    assert not ledger_path.exists()

    status = record_post_chat_reflection(
        [_msg("user", "以后先验证再总结。")],
        session_id="s-enabled",
        config={"enabled": True, "ledger_path": str(ledger_path), "status_path": str(status_path)},
    )

    assert status["suggest_only"] is True
    assert status["candidates_written"] == 1
    rows = PostChatCandidateLedger(ledger_path).read_all()
    assert rows[0]["status"] == "pending"
    assert rows[0]["promotion_policy"] == "needs_gate"
    assert json.loads(status_path.read_text(encoding="utf-8"))["candidate_ids"] == [rows[0]["id"]]


def test_record_post_chat_reflection_expands_configured_tilde_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    status = record_post_chat_reflection(
        [_msg("user", "以后先验证再总结。")],
        session_id="s-tilde",
        config={"enabled": True, "ledger_path": "~/ledger.jsonl", "status_path": "~/status.json"},
    )

    assert (tmp_path / "ledger.jsonl").exists()
    assert (tmp_path / "status.json").exists()
    assert status["candidate_ledger_path"] == str(tmp_path / "ledger.jsonl")


def test_record_post_chat_reflection_noops_without_candidates(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    status_path = tmp_path / "status.json"

    status = record_post_chat_reflection(
        [_msg("user", "hello there")],
        session_id="s-empty",
        config={"enabled": True, "ledger_path": str(ledger_path), "status_path": str(status_path)},
    )

    assert status["state"] == "no_candidates"
    assert status["candidates_written"] == 0
    assert not ledger_path.exists()
    assert not status_path.exists()


def test_record_post_chat_reflection_does_not_persist_raw_sample_value(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    status_path = tmp_path / "status.json"
    sample_value = "NOT_A_REAL_SAMPLE_VALUE"

    record_post_chat_reflection(
        [_msg("user", f"token 是 {sample_value}")],
        session_id="s-sample_value",
        config={"enabled": True, "ledger_path": str(ledger_path), "status_path": str(status_path)},
    )

    persisted = ledger_path.read_text(encoding="utf-8")
    assert sample_value not in persisted
    assert "[REDACTED_SENSITIVE]" in persisted


def test_sensitive_candidate_id_does_not_depend_on_raw_secret_value():
    first = build_post_chat_candidates([_msg("user", "token 是 FIRST_SAMPLE_VALUE")], session_id="same")
    second = build_post_chat_candidates([_msg("user", "token 是 SECOND_SAMPLE_VALUE")], session_id="same")

    assert first[0]["content"] == "[REDACTED_SENSITIVE]"
    assert second[0]["content"] == "[REDACTED_SENSITIVE]"
    assert first[0]["id"] == second[0]["id"]


def test_structured_user_content_only_extracts_safe_text_parts():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "以后先验证再总结。"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,SHOULD_NOT_PERSIST"}},
                {"type": "input_text", "text": "不要保存图像 payload。"},
            ],
        }
    ]

    candidates = build_post_chat_candidates(messages, session_id="structured")
    serialized = json.dumps(candidates, ensure_ascii=False)

    assert candidates
    assert "SHOULD_NOT_PERSIST" not in serialized
    assert "以后先验证再总结" in serialized
    assert "不要保存图像 payload" in serialized


def test_candidate_ledger_appends_jsonl_and_nightly_synthesizer_dedupes(tmp_path):
    ledger = PostChatCandidateLedger(tmp_path / "candidates.jsonl")
    now = datetime.now(timezone.utc)
    ledger.append_many([
        {
            "id": "a",
            "type": "correction",
            "content": "改码前先 fetch/pull 防覆盖",
            "status": "pending",
            "created_at": now.isoformat(),
            "evidence_refs": ["session:1:turn:0"],
            "sensitivity_level": "none",
        },
        {
            "id": "b",
            "type": "correction",
            "content": "改码前先 fetch/pull 防覆盖",
            "status": "pending",
            "created_at": (now + timedelta(seconds=1)).isoformat(),
            "evidence_refs": ["session:2:turn:0"],
            "sensitivity_level": "none",
        },
    ])

    report = synthesize_nightly_reflection(ledger.read_all())

    assert report["total_candidates"] == 2
    assert report["clusters"][0]["count"] == 2
    assert report["clusters"][0]["decision"]["decision"] == "promote_allowed"
    assert report["clusters"][0]["dissent"] == []
