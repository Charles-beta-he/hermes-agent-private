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


def _write_openclaw_fixture(tmp_path: Path) -> tuple[Path, Path]:
    gap_dir = tmp_path / "openclaw-gap"
    gap_dir.mkdir()
    plan = {
        "families": {
            "provider": [{"id": "provider-fixture"}],
            "tool": [{"id": "tool-fixture"}],
            "memory": [{"id": "memory-fixture"}],
            "gateway": [{"id": "gateway-fixture"}],
        }
    }
    required = [
        "manifest_index.json",
        "activation_plan.json",
        "scoped_rules.json",
        "protocol_inventory.json",
        "memory_contract.json",
        "hermes_gap_matrix.json",
        "hermes_missing_functions.md",
        "dashboard.md",
    ]
    for name in required:
        content = json.dumps(plan) if name == "activation_plan.json" else "fixture\n"
        (gap_dir / name).write_text(content, encoding="utf-8")

    repo = tmp_path / "openclaw"
    plugins = repo / "src" / "plugins"
    plugins.mkdir(parents=True)
    (repo / "AGENTS.md").write_text("# OpenClaw Fixture\n- Preserve repository policy\n", encoding="utf-8")
    (plugins / "AGENTS.md").write_text("# Plugins Boundary\n- Keep plugins isolated\n", encoding="utf-8")
    return gap_dir, repo


def test_openclaw_activation_hints_from_generated_plan(tmp_path):
    gap_dir, _ = _write_openclaw_fixture(tmp_path)
    hints = activation_hints_for_text("需要 provider tool memory gateway 能力", gap_dir=gap_dir)
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


def test_openclaw_scoped_policy_for_repo(tmp_path):
    _, repo = _write_openclaw_fixture(tmp_path)
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


def test_task_router_activation_hints_opt_in(tmp_path, monkeypatch):
    gap_dir, _ = _write_openclaw_fixture(tmp_path)
    monkeypatch.setattr(
        "agent.task_router.activation_hints_for_text",
        lambda text: activation_hints_for_text(text, gap_dir=gap_dir),
    )
    router = TaskRouter({"openclaw_activation_hints": True})
    result = router.route("需要 provider tool memory gateway 能力")
    assert "activation_hints" in result


def test_openclaw_control_plane_benchmark_passes(tmp_path):
    gap_dir, repo = _write_openclaw_fixture(tmp_path)
    result = benchmark(gap_dir=gap_dir, openclaw_repo=repo)
    assert result["verdict"] == "pass", json.dumps(result, ensure_ascii=False, indent=2)
