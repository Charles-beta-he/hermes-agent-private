import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from agent.post_chat_reflection import (
    PostChatCandidateLedger,
    backfill_historical_reflection,
    build_post_chat_candidates,
    build_promotion_review,
    evaluate_candidate_promotion,
    install_nightly_cron,
    install_nightly_script,
    record_post_chat_reflection,
    run_promotion_review,
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


def test_backfill_historical_reflection_scans_sessiondb_suggest_only_and_dedupes(tmp_path):
    class FakeSessionDB:
        def __init__(self):
            self.list_kwargs = None

        def list_sessions_rich(self, **kwargs):
            self.list_kwargs = kwargs
            return [
                {"id": "s1", "title": "first"},
                {"id": "s2", "title": "second"},
            ]

        def get_messages(self, session_id):
            if session_id == "s1":
                return [_msg("user", "以后多模型分析必须真实外调，不要单模型模拟。")]
            return [_msg("user", "本轮任务已完成，PR #123 合了。")]

    ledger_path = tmp_path / "candidates.jsonl"
    status_path = tmp_path / "backfill.json"
    fake_db = FakeSessionDB()

    first = backfill_historical_reflection(
        max_sessions=2,
        ledger_path=str(ledger_path),
        status_path=str(status_path),
        session_db=fake_db,
    )
    second = backfill_historical_reflection(
        max_sessions=2,
        ledger_path=str(ledger_path),
        status_path=str(status_path),
        session_db=fake_db,
    )

    rows = PostChatCandidateLedger(ledger_path).read_all()
    serialized = json.dumps(rows, ensure_ascii=False)
    assert first["suggest_only"] is True
    assert first["dreaming_style"] is True
    assert first["promotion_gate"] == "nightly_synthesis_then_manual_review"
    assert first["sessions_scanned"] == 2
    assert first["candidates_written"] == 2
    assert second["candidates_written"] == 0
    assert len(rows) == 2
    assert all(row["source"] == "historical_backfill" for row in rows)
    assert "PR #123" not in serialized or '"status": "rejected"' in serialized
    assert json.loads(status_path.read_text(encoding="utf-8"))["suggest_only"] is True
    assert fake_db.list_kwargs is not None
    assert fake_db.list_kwargs["order_by_last_active"] is True


def test_backfill_historical_reflection_fail_closed_on_sessiondb_error(tmp_path):
    class BrokenSessionDB:
        def list_sessions_rich(self, **kwargs):
            raise RuntimeError("db unavailable")

    status = backfill_historical_reflection(
        ledger_path=str(tmp_path / "candidates.jsonl"),
        status_path=str(tmp_path / "backfill.json"),
        session_db=BrokenSessionDB(),
    )

    assert status["state"] == "failed"
    assert status["suggest_only"] is True
    assert status["candidates_written"] == 0
    assert not (tmp_path / "candidates.jsonl").exists()
    assert json.loads((tmp_path / "backfill.json").read_text(encoding="utf-8"))["state"] == "failed"


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


def test_promotion_review_is_manual_only_and_writes_artifacts(tmp_path):
    ledger = PostChatCandidateLedger(tmp_path / "candidates.jsonl")
    ledger.append_many([
        {
            "id": "a",
            "type": "correction",
            "content": "改码前先 fetch/pull 防覆盖",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "evidence_refs": ["session:1:turn:0"],
            "sensitivity_level": "none",
            "explicitness": "explicit",
            "proposed_destination": "memory",
        }
    ])

    packet = run_promotion_review(
        ledger_path=str(ledger.path),
        json_path=str(tmp_path / "review.json"),
        markdown_path=str(tmp_path / "review.md"),
    )

    assert packet["manual_only"] is True
    assert packet["actions"]
    assert packet["actions"][0]["manual_only"] is True
    assert packet["actions"][0]["target"] == "memory"
    assert (tmp_path / "review.json").exists()
    assert "Manual-only" in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_build_promotion_review_filters_non_actionable_clusters():
    report = {
        "generated_at": "now",
        "clusters": [
            {"cluster_id": "a", "content": "x", "decision": {"decision": "needs_more_evidence", "target": "staging"}},
            {"cluster_id": "b", "content": "y", "decision": {"decision": "review_required", "target": "skill"}},
        ],
    }

    packet = build_promotion_review(report)

    assert [a["cluster_id"] for a in packet["actions"]] == ["b"]
    assert packet["metrics"]["skill"] == 1


def test_install_nightly_script_writes_safe_no_agent_script(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    script = install_nightly_script()
    content = script.read_text(encoding="utf-8")

    assert script == tmp_path / "scripts" / "post_chat_reflection_nightly.py"
    assert "run_nightly_synthesis" in content
    assert "memory" not in content.lower()
    assert "skill_manage" not in content


def test_install_nightly_cron_creates_expected_no_agent_job(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    created = {}

    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "load_jobs", lambda: [])

    def fake_create_job(**kwargs):
        created.update(kwargs)
        return {"id": "job1", "name": kwargs["name"], "script": kwargs["script"], "no_agent": kwargs["no_agent"], "prompt": kwargs["prompt"] or ""}

    monkeypatch.setattr(cron_jobs, "create_job", fake_create_job)

    result = install_nightly_cron(schedule="0 3 * * *", deliver="local")

    assert result["state"] == "created"
    assert created["no_agent"] is True
    assert created["script"] == "post_chat_reflection_nightly.py"
    assert created["prompt"] is None
    assert (tmp_path / "scripts" / "post_chat_reflection_nightly.py").exists()


def test_install_nightly_cron_rejects_unsafe_same_name_job_and_recreates_script(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    unsafe = {
        "id": "bad",
        "name": "Hermes post-chat reflection nightly synthesis",
        "no_agent": False,
        "script": "other.py",
        "prompt": "wake an agent",
    }

    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "load_jobs", lambda: [unsafe])

    try:
        install_nightly_cron()
    except ValueError as exc:
        assert "Refusing" in str(exc)
    else:
        raise AssertionError("unsafe same-name cron job should be rejected")

    assert not (tmp_path / "scripts" / "post_chat_reflection_nightly.py").exists()


def test_install_nightly_cron_rejects_same_name_job_with_profile_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    profiled = {
        "id": "profiled",
        "name": "Hermes post-chat reflection nightly synthesis",
        "no_agent": True,
        "script": "post_chat_reflection_nightly.py",
        "prompt": "",
        "profile": "other",
        "enabled": True,
        "deliver": "local",
        "schedule_display": "0 3 * * *",
    }

    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "load_jobs", lambda: [profiled])

    try:
        install_nightly_cron()
    except ValueError as exc:
        assert "Refusing" in str(exc)
    else:
        raise AssertionError("profile-overridden same-name cron job should be rejected")


def test_install_nightly_cron_rejects_duplicate_expected_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    expected = {
        "id": "ok1",
        "name": "Hermes post-chat reflection nightly synthesis",
        "no_agent": True,
        "script": "post_chat_reflection_nightly.py",
        "prompt": "",
        "profile": None,
        "enabled": True,
        "deliver": "local",
        "schedule_display": "0 3 * * *",
        "state": "scheduled",
    }

    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "load_jobs", lambda: [dict(expected), dict(expected, id="ok2")])

    try:
        install_nightly_cron()
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate expected cron jobs should be rejected")


def test_install_nightly_cron_rejects_schedule_deliver_or_disabled_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    mismatched = {
        "id": "mismatch",
        "name": "Hermes post-chat reflection nightly synthesis",
        "no_agent": True,
        "script": "post_chat_reflection_nightly.py",
        "prompt": "",
        "profile": None,
        "enabled": False,
        "deliver": "origin",
        "schedule_display": "0 4 * * *",
    }

    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "load_jobs", lambda: [mismatched])

    try:
        install_nightly_cron(schedule="0 3 * * *", deliver="local")
    except ValueError as exc:
        assert "matching schedule/deliver" in str(exc)
    else:
        raise AssertionError("mismatched disabled cron job should be rejected")


def test_install_nightly_cron_rejects_missing_enabled_and_paused_same_name_job(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    missing_enabled = {
        "id": "legacy",
        "name": "Hermes post-chat reflection nightly synthesis",
        "no_agent": True,
        "script": "post_chat_reflection_nightly.py",
        "prompt": "",
        "profile": None,
        "deliver": "local",
        "schedule_display": "0 3 * * *",
        "state": "paused",
    }

    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "load_jobs", lambda: [missing_enabled])

    try:
        install_nightly_cron(schedule="0 3 * * *", deliver="local")
    except ValueError as exc:
        assert "enabled job" in str(exc)
    else:
        raise AssertionError("missing-enabled paused cron job should be rejected")

    assert not (tmp_path / "scripts" / "post_chat_reflection_nightly.py").exists()


def test_install_nightly_script_rejects_symlink_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    outside = tmp_path / "outside.py"
    outside.write_text("do not overwrite", encoding="utf-8")
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / "post_chat_reflection_nightly.py"
    script.symlink_to(outside)

    try:
        install_nightly_script()
    except ValueError as exc:
        assert "unsafe nightly script path" in str(exc)
    else:
        raise AssertionError("symlink nightly script path should be rejected")

    assert outside.read_text(encoding="utf-8") == "do not overwrite"


def test_install_nightly_script_rejects_symlinked_scripts_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    outside = tmp_path / "outside-scripts"
    outside.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    scripts = home / "scripts"
    scripts.symlink_to(outside, target_is_directory=True)

    try:
        install_nightly_script()
    except ValueError as exc:
        assert "symlinked scripts directory" in str(exc)
    else:
        raise AssertionError("symlinked scripts directory should be rejected")

    assert not (outside / "post_chat_reflection_nightly.py").exists()


def test_reflection_status_json_is_bounded_and_requires_positive_limit(tmp_path):
    ledger = PostChatCandidateLedger(tmp_path / "candidates.jsonl")
    rows = []
    for i in range(60):
        rows.append(
            {
                "id": str(i),
                "type": "correction",
                "content": f"candidate {i}",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "evidence_refs": [f"session:{i}"],
                "sensitivity_level": "none",
                "explicitness": "explicit",
                "proposed_destination": "memory",
            }
        )
    ledger.append_many(rows)

    base_cmd = [sys.executable, "hermes_cli/main.py", "reflection", "status", "--json", "--ledger", str(ledger.path)]
    default = subprocess.run(base_cmd, check=True, text=True, capture_output=True)
    default_payload = json.loads(default.stdout)
    assert default_payload["candidate_count"] == 60
    assert default_payload["returned_count"] == 50
    assert default_payload["limit"] == 50
    assert default_payload["truncated"] is True
    assert len(default_payload["candidates"]) == 50
    assert default_payload["candidates"][0]["id"] == "10"
    assert default_payload["candidates"][-1]["id"] == "59"

    limited = subprocess.run([*base_cmd, "--limit", "7"], check=True, text=True, capture_output=True)
    limited_payload = json.loads(limited.stdout)
    assert limited_payload["candidate_count"] == 60
    assert limited_payload["returned_count"] == 7
    assert limited_payload["limit"] == 7
    assert limited_payload["truncated"] is True
    assert len(limited_payload["candidates"]) == 7
    assert limited_payload["candidates"][0]["id"] == "53"
    assert limited_payload["candidates"][-1]["id"] == "59"

    all_rows = subprocess.run([*base_cmd, "--all"], check=True, text=True, capture_output=True)
    all_payload = json.loads(all_rows.stdout)
    assert all_payload["candidate_count"] == 60
    assert all_payload["returned_count"] == 60
    assert all_payload["limit"] is None
    assert all_payload["truncated"] is False
    assert len(all_payload["candidates"]) == 60

    invalid = subprocess.run([*base_cmd, "--limit", "0"], text=True, capture_output=True)
    assert invalid.returncode != 0
    assert "--limit must be >= 1" in invalid.stderr or "--limit must be >= 1" in invalid.stdout

    negative = subprocess.run([*base_cmd, "--limit", "-1"], text=True, capture_output=True)
    assert negative.returncode != 0
    assert "--limit must be >= 1" in negative.stderr or "--limit must be >= 1" in negative.stdout
