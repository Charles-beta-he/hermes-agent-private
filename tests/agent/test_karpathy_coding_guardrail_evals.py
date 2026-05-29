"""Behavioral evals for Karpathy-style coding guardrail routing and negative space."""

from pathlib import Path

from agent.prompt_builder import _parse_skill_file, build_skills_system_prompt


REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_build_skills_system_prompt_includes_negative_space_guardrails(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    skill_dir = tmp_path / "skills" / "coding" / "python-debug"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: python-debug\ndescription: Debug Python scripts\n---\n",
        encoding="utf-8",
    )

    prompt = build_skills_system_prompt()
    lower = prompt.lower()

    assert "trivial typo/one-line fixes" in lower
    assert "non-coding tasks should not be forced through coding workflow skills" in lower
    assert "explicitly requests a broad architecture change or refactor" in lower


def test_repo_skill_descriptions_cover_coding_refactor_debug_review_routes():
    route_expectations = {
        "skills/software-development/writing-plans/SKILL.md": "implementation plans",
        "skills/software-development/systematic-debugging/SKILL.md": "root cause debugging",
        "skills/github/github-code-review/SKILL.md": "review prs",
    }

    for relative_path, expected_fragment in route_expectations.items():
        is_compatible, _, description = _parse_skill_file(REPO_ROOT / relative_path)
        assert is_compatible is True
        assert expected_fragment in description.lower()

    tdd_skill = _repo_text("skills/software-development/test-driven-development/SKILL.md")
    assert "- New features" in tdd_skill
    assert "- Bug fixes" in tdd_skill
    assert "- Refactoring" in tdd_skill

    writing_plans = _repo_text("skills/software-development/writing-plans/SKILL.md")
    assert "thin verified slices" in writing_plans
    assert "speculative architecture" in writing_plans


def test_repo_guardrail_artifacts_preserve_full_negative_space_examples():
    gap_analysis = _repo_text("docs/private/andrej-karpathy-skills-gap-analysis.md")
    landing_review = _repo_text(
        "docs/private/experience-ratchet-proposal-skill-landing-review-20260523.md"
    )

    assert "trivial typo/one-line edits should not require heavy ceremony" in gap_analysis
    assert "non-coding tasks should not be forced through coding workflow skills" in gap_analysis
    assert "user-explicit broad refactors may intentionally touch larger surfaces" in gap_analysis
    assert "broad architecture only when explicitly required" in landing_review
