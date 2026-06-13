from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_preview_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_dag_projection_preview.py"
    spec = importlib.util.spec_from_file_location("_workflow_dag_projection_preview", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture() -> dict:
    return {
        "workflow_id": "fixture_graphical_multiagent_control",
        "version": "draft-v1",
        "nodes": [
            {"id": "intake", "type": "task", "title": "Collect local evidence"},
            {"id": "map", "type": "task", "depends_on": ["intake"], "title": "Map DAG"},
            {
                "id": "approve_external_surface",
                "type": "approval",
                "depends_on": ["map"],
                "question": "Should Hermes enable any external workflow execution surface?",
                "options": ["A: keep local preview only", "B: authorize one scoped adapter dry-run"],
                "recommend": "A: keep local preview only",
            },
            {"id": "report", "type": "task", "depends_on": ["approve_external_surface"]},
        ],
    }


def test_preview_builds_deterministic_graph_hash_and_approval_plan():
    preview_mod = _load_preview_module()

    first = preview_mod.build_preview(_fixture())
    second = preview_mod.build_preview({**_fixture(), "nodes": list(reversed(_fixture()["nodes"]))})

    assert first["status"] == "PASS"
    assert first["projection_only"] is True
    assert first["boundary"] == "no_execution_no_task_claim_no_model_call_no_adapter_write_no_handoff_recorded"
    assert first["topological_order"] == ["intake", "map", "approve_external_surface", "report"]
    assert first["graph_hash"] == second["graph_hash"]
    assert first["approval_packet_count"] == 1
    packet = first["approval_packet_plans"][0]
    assert packet["status"] == "draft_only_not_recorded"
    assert packet["recommend"] == "A: keep local preview only"
    assert "hermes mission handoff" in packet["durable_command_template"]


def test_preview_rejects_cycles():
    preview_mod = _load_preview_module()
    dag = {
        "workflow_id": "cyclic_fixture",
        "nodes": [
            {"id": "a", "depends_on": ["b"]},
            {"id": "b", "depends_on": ["a"]},
        ],
    }

    try:
        preview_mod.build_preview(dag)
    except preview_mod.DagValidationError as exc:
        assert "cycle detected" in str(exc)
    else:  # pragma: no cover - explicit failure branch for readability
        raise AssertionError("cycle was not rejected")


def test_cli_writes_local_preview_artifact(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_dag_projection_preview.py"
    dag_path = tmp_path / "dag.json"
    dag_path.write_text(json.dumps(_fixture()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), str(dag_path), "--write", "--output-dir", str(tmp_path / "out")],
        check=True,
        text=True,
        capture_output=True,
    )

    preview = json.loads(result.stdout)
    written = Path(preview["written"])
    assert written.exists()
    saved = json.loads(written.read_text(encoding="utf-8"))
    assert saved["graph_hash"] == preview["graph_hash"]
    assert saved["approval_packet_count"] == 1
