import importlib.util
import json
from pathlib import Path


def load_dispatch_tick_module():
    script = Path.home() / ".hermes" / "scripts" / "brain_task_dispatch_tick.py"
    if not script.exists():
        script = Path("/Users/charles/.hermes/scripts/brain_task_dispatch_tick.py")
    spec = importlib.util.spec_from_file_location("brain_task_dispatch_tick_under_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_taskpool_runtime_config_reads_parallel_knobs(tmp_path, monkeypatch):
    module = load_dispatch_tick_module()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "taskpool:\n"
        "  dispatch_mode: parallel\n"
        "  max_parallel_agents: 5\n"
        "  handoff_archive_threshold: 12\n"
        "  handoff_archive_keep: 4\n"
        "  continuation_backlog_threshold: 9\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONFIG", str(cfg))

    runtime = module.load_taskpool_runtime_config()

    assert runtime == {
        "dispatch_mode": "parallel",
        "max_parallel_agents": 5,
        "handoff_archive_threshold": 12,
        "handoff_archive_keep": 4,
        "continuation_backlog_threshold": 9,
    }


def test_continuation_status_is_exposed_to_taskpool_tick_summary(tmp_path, monkeypatch):
    module = load_dispatch_tick_module()
    active = tmp_path / ".hermes" / "state" / "continuations" / "active"
    active.mkdir(parents=True)
    (active / "packet.json").write_text(
        json.dumps({"schema": "hermes_continuation_packet_v1", "scope": "conversation"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "HOME", tmp_path)

    status = module.continuation_backlog_status(backlog_warning_threshold=0)

    assert status["schema"] == "hermes_continuation_status_v1"
    assert status["active_count"] == 1
    assert status["by_scope"]["conversation"] == 1
    assert status["backlog_warning"] == "continuation_backlog_high"


def test_dispatch_tick_run_envelope_points_at_projection_files(tmp_path, monkeypatch):
    module = load_dispatch_tick_module()
    monkeypatch.setenv("HERMES_RUN_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setattr(module, "LOG_FILE", tmp_path / "brain_task_dispatch_tick.jsonl")
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "brain_task_dispatch_tick_state.json")

    event = {
        "status": "OK",
        "generated_at": "2026-05-20T12:00:00+08:00",
        "next_task_id": "task-123",
        "dispatchable": True,
        "dispatch": {"next": {"id": "task-123"}},
    }

    result = module.append_dispatch_tick_run_envelope(event)
    ledger_event = json.loads((tmp_path / "ledger" / "run_events.jsonl").read_text(encoding="utf-8").splitlines()[-1])

    assert result["run_id"] == ledger_event["run_id"]
    assert ledger_event["surface"] == "dispatch_tick"
    assert ledger_event["lifecycle_state"] == "tick_ok"
    assert ledger_event["proof_refs"] == [
        f"jsonl:{tmp_path / 'brain_task_dispatch_tick.jsonl'}",
        "dispatch_hint:task-123",
    ]
    assert {"projection": "brain_task_dispatch_tick_state", "ref": str(tmp_path / "brain_task_dispatch_tick_state.json")} in ledger_event["projection_refs"]


def test_handoff_backlog_status_warns_and_archives_old_files(tmp_path, monkeypatch):
    module = load_dispatch_tick_module()
    monkeypatch.setattr(module, "HOME", tmp_path)
    root = tmp_path / ".hermes" / "state" / "auto_run_handoffs"
    root.mkdir(parents=True)
    for idx in range(5):
        p = root / f"handoff-{idx}.md"
        p.write_text(f"handoff {idx}\n", encoding="utf-8")

    status = module.handoff_backlog_status(archive=True, threshold=3, keep=2)

    assert status["schema"] == "taskpool_handoff_backlog_v1"
    assert status["warning"] == ""
    assert status["dirs"]["autorun"]["over_threshold_before"] is True
    assert status["dirs"]["autorun"]["over_threshold"] is False
    assert status["dirs"]["autorun"]["archived"] == 3
    assert status["dirs"]["autorun"]["count_after"] == 2
    assert status["archived_count"] == 3
    assert len(list(root.glob("*.md"))) == 2
