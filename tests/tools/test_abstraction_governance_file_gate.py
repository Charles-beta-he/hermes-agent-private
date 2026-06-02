import json

import hermes_constants
from tools.file_tools import patch_tool, write_file_tool


def test_write_file_blocks_task_progress_in_project_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    profile_path = tmp_path / "project-profiles" / "demo.json"

    raw = write_file_tool(str(profile_path), '{"note":"PR #123 was merged and this task is complete"}')
    result = json.loads(raw)

    assert "error" in result
    assert result["abstraction_governance"]["reason"] == "task_progress_not_project_profile"
    assert not profile_path.exists()


def test_write_file_does_not_gate_ordinary_project_files(monkeypatch, tmp_path):
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path / "hermes-home")
    target = tmp_path / "ordinary-note.md"

    raw = write_file_tool(str(target), "PR #123 is mentioned in an ordinary project note.\n")
    result = json.loads(raw)

    assert result.get("error") in (None, "")
    assert target.read_text() == "PR #123 is mentioned in an ordinary project note.\n"


def test_patch_blocks_context_coupled_adr_without_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    adr_path = tmp_path / "docs" / "adr" / "branching.md"
    adr_path.parent.mkdir(parents=True)
    adr_path.write_text("# Branching\n")

    raw = patch_tool(
        mode="replace",
        path=str(adr_path),
        old_string="# Branching\n",
        new_string="# Branching\nAlways use feat/uvisa-2.0 as integration branch.\n",
    )
    result = json.loads(raw)

    assert "error" in result
    assert result["abstraction_governance"]["reason"] == "context_coupled_adr"
    assert adr_path.read_text() == "# Branching\n"


def test_patch_allows_scoped_adr_with_project_entities(monkeypatch, tmp_path):
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    adr_path = tmp_path / "docs" / "adr" / "branching.md"
    adr_path.parent.mkdir(parents=True)
    adr_path.write_text("# Branching\n")

    raw = patch_tool(
        mode="replace",
        path=str(adr_path),
        old_string="# Branching\n",
        new_string=(
            "# Branching\n"
            "Scope: project uvisa-app-2.0 only.\n"
            "Project fact: feat/uvisa-2.0 is the integration branch.\n"
            "Anti-conditions: do not apply to other repositories.\n"
        ),
    )
    result = json.loads(raw)

    assert result.get("error") in (None, "")
    assert "Project fact" in adr_path.read_text()
