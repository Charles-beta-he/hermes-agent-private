import json
from pathlib import Path

from agent.openclaw_control_plane import (
    activation_hints_for_text,
    benchmark,
    memory_contract,
    memory_write_gate,
    openclaw_activation_hints_enabled,
    render_scoped_policy_context,
    scoped_policy_for_path,
    validate_record,
)
from agent.task_router import TaskRouter


def test_openclaw_activation_hints_from_generated_plan():
    hints = activation_hints_for_text("需要 provider tool memory gateway 能力")
    families = {item["family"] for item in hints}
    assert {"provider", "tool", "memory", "gateway"} & families


def test_openclaw_schema_validator_rejects_unredacted_secret():
    record = {
        "schema_version": "openclaw.trace.v1",
        "trace_id": "t1",
        "session_id": "s1",
        "timestamp": "2026-05-14T00:00:00Z",
        "event_type": "tool_error",
        "status": "failed",
        "evidence": {"authorization": "Bearer abcdef123456"},
    }
    result = validate_record("trace", record)
    assert not result.ok
    assert any("secret" in err or "token" in err for err in result.errors)
    clean = dict(record)
    clean["evidence"] = {"authorization": "[REDACTED]"}
    clean["extra"] = "not allowed"
    strict = validate_record("trace", clean)
    assert not strict.ok
    assert any("unknown field" in err for err in strict.errors)


def test_openclaw_scoped_policy_for_repo():
    repo = Path.home() / ".hermes" / "research" / "openclaw"
    policy = scoped_policy_for_path(repo, repo / "src" / "plugins")
    assert policy["count"] >= 1
    assert any(scope["file"].endswith("AGENTS.md") for scope in policy["scopes"])
    context = render_scoped_policy_context(repo, repo / "src" / "plugins")
    assert "Scoped Repository Policy" in context
    assert "Plugins Boundary" in context


def test_openclaw_config_flag_default_off_and_opt_in():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.yaml"
        cfg.write_text("openclaw_control_plane:\n  activation_hints: false\n")
        assert openclaw_activation_hints_enabled(config_path=cfg) is False
        cfg.write_text("openclaw_control_plane:\n  activation_hints: true\n")
        assert openclaw_activation_hints_enabled(config_path=cfg) is True


def test_openclaw_memory_write_gate_contract():
    assert memory_write_gate("procedural_skills", "repeatable verified workflow", evidence="tests passed")["ok"]
    rejected = memory_write_gate("semantic_memory", "temporary TODO WIP", durable_days=1)
    assert not rejected["ok"]


def test_openclaw_memory_contract_sources():
    contract = memory_contract()
    assert set(contract["sources"]) == {"semantic_memory", "procedural_skills", "case_memory", "session_recall"}
    assert "write_with_gate" in contract["operations"]


def test_task_router_activation_hints_opt_in():
    router = TaskRouter({"openclaw_activation_hints": True})
    result = router.route("需要 provider tool memory gateway 能力")
    assert "activation_hints" in result


def test_openclaw_control_plane_benchmark_passes():
    result = benchmark()
    assert result["verdict"] == "pass", json.dumps(result, ensure_ascii=False, indent=2)
