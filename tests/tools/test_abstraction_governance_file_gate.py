import json

import hermes_constants
import tools.file_tools as file_tools
from tools.file_tools import patch_tool, write_file_tool


def _allow_pytest_tmp_under_private_var(monkeypatch):
    """Keep these tests focused on the abstraction gate, not macOS temp paths.

    On macOS pytest's tmp_path lives under /private/var/folders, which the file
    tool's sensitive-path guard intentionally blocks before deeper write gates.
    The abstraction gate itself is path-agnostic, so narrow the sensitive prefix
    list in these focused tests instead of weakening runtime behavior.
    """

    monkeypatch.setattr(
        file_tools,
        "_SENSITIVE_PATH_PREFIXES",
        tuple(prefix for prefix in file_tools._SENSITIVE_PATH_PREFIXES if prefix != "/private/var/"),
    )


def test_write_file_blocks_task_progress_in_project_profile(monkeypatch, tmp_path):
    _allow_pytest_tmp_under_private_var(monkeypatch)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    profile_path = tmp_path / "project-profiles" / "demo.json"

    raw = write_file_tool(str(profile_path), '{"note":"PR #123 was merged and this task is complete"}')
    result = json.loads(raw)

    assert "error" in result
    assert result["abstraction_governance"]["reason"] == "task_progress_not_project_profile"
    assert not profile_path.exists()


def test_write_file_blocks_task_progress_in_skill(monkeypatch, tmp_path):
    _allow_pytest_tmp_under_private_var(monkeypatch)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    skill_path = tmp_path / "skills" / "bad" / "SKILL.md"

    raw = write_file_tool(str(skill_path), "PR #123 was merged and this task is complete.")
    result = json.loads(raw)

    assert "error" in result
    assert result["abstraction_governance"]["reason"] == "task_progress_not_skill"
    assert not skill_path.exists()


def test_write_file_does_not_gate_ordinary_project_files(monkeypatch, tmp_path):
    _allow_pytest_tmp_under_private_var(monkeypatch)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path / "hermes-home")
    target = tmp_path / "ordinary-note.md"

    raw = write_file_tool(str(target), "PR #123 is mentioned in an ordinary project note.\n")
    result = json.loads(raw)

    assert result.get("error") in (None, "")
    assert target.read_text() == "PR #123 is mentioned in an ordinary project note.\n"


def test_file_gate_classification_defers_when_governance_import_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(file_tools, "classify_durable_write_path", None)

    assert file_tools._classify_abstraction_governance_path(str(tmp_path / "note.md")) is None


def test_patch_blocks_context_coupled_adr_without_scope(monkeypatch, tmp_path):
    _allow_pytest_tmp_under_private_var(monkeypatch)
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


def test_patch_blocks_context_coupled_skill_without_scope(monkeypatch, tmp_path):
    _allow_pytest_tmp_under_private_var(monkeypatch)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    skill_path = tmp_path / "skills" / "bad" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Skill\n")

    raw = patch_tool(
        mode="replace",
        path=str(skill_path),
        old_string="# Skill\n",
        new_string="# Skill\nAlways use feat/uvisa-2.0 as integration branch.\n",
    )
    result = json.loads(raw)

    assert "error" in result
    assert result["abstraction_governance"]["reason"] == "context_coupled_skill"
    assert skill_path.read_text() == "# Skill\n"


def test_patch_allows_scoped_adr_with_project_entities(monkeypatch, tmp_path):
    _allow_pytest_tmp_under_private_var(monkeypatch)
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
