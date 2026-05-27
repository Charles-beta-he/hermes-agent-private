"""Tests for the deterministic skills health audit."""

from __future__ import annotations

from pathlib import Path

from tools.skills_health_audit import audit_skills


def _skill(path: Path, name: str, description: str, body: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n{body}\n",
        encoding="utf-8",
    )


def test_skills_health_audit_passes_clean_skill(tmp_path):
    home = tmp_path / "home"
    root = home / "skills"
    _skill(root / "good", "good", "Use when reviewing a focused Python test change.")

    report = audit_skills([root], hermes_home=home, active_profile="default")

    assert report["schema_version"] == "hermes_skills_health_audit_v1"
    assert report["read_only"] is True
    assert report["status"] == "PASS"
    assert report["checked_count"] == 1
    assert report["violations"] == []


def test_skills_health_audit_flags_token_budget_and_duplicate_overlap(tmp_path):
    home = tmp_path / "home"
    root = home / "skills"
    desc = (
        "Use when operating taskpool control plane dispatch eligibility proof evidence "
        "governance resolver blocked lifecycle follow-through automation."
    )
    _skill(root / "a", "alpha", desc)
    _skill(root / "b", "beta", desc)

    report = audit_skills([root], hermes_home=home, active_profile="default", max_description_chars=40)

    codes = {item["code"] for item in report["violations"] + report["warnings"]}
    assert report["status"] == "FAIL"
    assert "description_token_budget_exceeded" in codes
    assert "duplicate_trigger_overlap" in codes
    assert all(isinstance(item, dict) and "code" in item for item in report["violations"])


def test_skills_health_audit_fails_closed_on_cross_profile_root(tmp_path):
    home = tmp_path / "home"
    other_root = home / "profiles" / "other" / "skills"
    _skill(other_root / "borrowed", "borrowed", "Use when testing profile boundary drift.")

    report = audit_skills([other_root], hermes_home=home, active_profile="default")

    assert report["status"] == "FAIL"
    assert any(item["code"] == "cross_profile_skill_root" for item in report["violations"])
    assert "fail-closed" in report["policy"]
