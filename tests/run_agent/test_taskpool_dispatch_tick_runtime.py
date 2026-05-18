import importlib.util
import json
from pathlib import Path


def load_dispatch_tick_module():
    script = Path.home() / ".hermes" / "scripts" / "brain_task_dispatch_tick.py"
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


def test_handoff_backlog_status_warns_and_archives_old_files(tmp_path, monkeypatch):
    module = load_dispatch_tick_module()
    monkeypatch.setattr(module, "HOME", tmp_path)
    root = tmp_path / ".hermes" / "state" / "autorun" / "auto_run_handoffs"
    root.mkdir(parents=True)
    for idx in range(5):
        p = root / f"handoff-{idx}.md"
        p.write_text(f"handoff {idx}\n", encoding="utf-8")

    status = module.handoff_backlog_status(archive=True, threshold=3, keep=2)

    assert status["schema"] == "taskpool_handoff_backlog_v1"
    assert status["warning"] == "handoff_backlog_high"
    assert status["dirs"]["autorun"]["over_threshold"] is True
    assert status["dirs"]["autorun"]["archived"] == 3
    assert status["dirs"]["autorun"]["count_after"] == 2
    assert status["archived_count"] == 3
    assert len(list(root.glob("*.md"))) == 2
