from agent.session_risk_audit import classify_records, classify_session_risk


def test_classifies_config_and_write_with_evidence_bundle():
    audit = classify_session_risk(
        {
            "run_id": "cron-20260608-a",
            "source": "cron",
            "summary": "patched ~/.hermes/config.yaml and ran dashboard --write",
            "tool_calls": ["patch", "brain_task_orchestrator.py dashboard --write"],
            "evidence_bundle": [
                "artifact:/Users/charles/.hermes/logs/cron/cron-20260608-a.jsonl",
                "command:git diff -- ~/.hermes/config.yaml",
            ],
        }
    )

    assert audit["schema"] == "session_risk_audit_v1"
    assert audit["run_id"] == "cron-20260608-a"
    assert audit["max_severity"] == "high"
    risks = {finding["risk"] for finding in audit["findings"]}
    assert {"config_change", "filesystem_write"} <= risks
    assert not audit["insufficient_evidence"]
    assert all(finding["evidence_bundle"] for finding in audit["findings"])


def test_classifies_credential_and_provider_signals_without_secret_values():
    audit = classify_session_risk(
        {
            "id": "session-credential-check",
            "kind": "session",
            "messages": ["provider returned 429 quota warning", "checked oauth auth.json presence only"],
            "evidence_refs": ["artifact:/Users/charles/.hermes/logs/session-credential-check.jsonl"],
        }
    )

    risks = {finding["risk"] for finding in audit["findings"]}
    assert "credential_touch" in risks
    assert "provider_cost_signal" in risks
    assert audit["max_severity"] == "high"
    assert "secret_value" not in str(audit).lower()


def test_missing_evidence_fails_closed_for_detected_tool_use():
    audit = classify_session_risk(
        {
            "run_id": "tool-run-without-log",
            "source": "tool",
            "tool": "delegate_task",
            "summary": "used MCP sidecar and browser against https://example.invalid",
        }
    )

    assert audit["insufficient_evidence"] is True
    assert audit["max_severity"] == "high"
    assert {finding["risk"] for finding in audit["findings"]} >= {"external_network", "mcp_or_skill_use"}
    assert all(finding["insufficient_evidence"] for finding in audit["findings"])


def test_no_signal_and_no_evidence_reports_insufficient_evidence():
    audit = classify_session_risk({"run_id": "empty-summary", "summary": "completed"})

    assert audit["max_severity"] == "high"
    assert audit["findings"] == [
        {
            "risk": "insufficient_evidence",
            "severity": "high",
            "evidence_bundle": [],
            "insufficient_evidence": True,
            "reason": "no explicit evidence_bundle/evidence_refs/log_path/artifact/path present",
        }
    ]


def test_classify_records_preserves_three_read_only_samples():
    records = [
        {"run_id": "a", "summary": "write_file used", "artifact": "artifact:/tmp/a.jsonl"},
        {"run_id": "b", "summary": "provider quota 429", "log_path": "/tmp/b.jsonl"},
        {"run_id": "c", "summary": "browser used without persisted evidence"},
    ]

    audits = classify_records(records)

    assert [audit["run_id"] for audit in audits] == ["a", "b", "c"]
    assert audits[2]["insufficient_evidence"] is True
