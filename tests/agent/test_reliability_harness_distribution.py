import subprocess
import sys
import tomllib
from pathlib import Path

from agent.reliability_harness import bundled_cases_dir


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_reliability_harness_trunk_skill_is_bundled_in_repo():
    skill = REPO_ROOT / "skills" / "software-development" / "agent-eval-dynamic-workflow-harness" / "SKILL.md"

    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    assert "Agent Eval + Dynamic Workflow Harness" in text
    assert "Hermes Agent self-architecture only" in text


def test_evals_are_packaged_as_distribution_data():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    setup = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = pyproject["tool"]["setuptools"]["data-files"]

    assert "graft evals" in manifest
    assert '_data_file_tree("evals")' in setup
    assert "evals/cases/*.json" in data_files["evals/cases"]
    assert "skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md" in data_files[
        "skills/software-development/agent-eval-dynamic-workflow-harness"
    ]


def test_hermes_eval_console_script_is_declared():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    scripts = pyproject["project"]["scripts"]
    assert scripts["hermes-eval"] == "agent.reliability_harness:main"


def test_bundled_cases_dir_finds_seed_cases_from_checkout():
    cases_dir = bundled_cases_dir()

    assert cases_dir.exists()
    assert (cases_dir / "direct-basic-answer.json").exists()
    assert (cases_dir / "research-evidence-summary.json").exists()
    assert (cases_dir / "objective-gated-change.json").exists()


def test_cli_list_works_without_explicit_cases_dir():
    proc = subprocess.run(
        [sys.executable, "-m", "agent.reliability_harness", "list", "--json"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "direct-basic-answer" in proc.stdout
    assert "objective-gated-change" in proc.stdout
