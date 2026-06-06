"""Tests for the deterministic skills health audit."""

from __future__ import annotations

from pathlib import Path

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli.skills_hub import do_health
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
    assert report["checked_count"] == 0
    assert "fail-closed" in report["policy"]


def test_skills_health_audit_fails_closed_on_broad_profile_roots(tmp_path):
    home = tmp_path / "home"
    other_home = home / "profiles" / "other"
    _skill(other_home / "skills" / "borrowed", "borrowed", "Use when testing profile boundary drift.")

    other_report = audit_skills([other_home], hermes_home=home, active_profile="default")
    profiles_report = audit_skills([home / "profiles"], hermes_home=home, active_profile="default")

    assert other_report["status"] == "FAIL"
    assert other_report["checked_count"] == 0
    assert any(item["path"] == str(other_home.resolve()) for item in other_report["violations"])
    assert profiles_report["status"] == "FAIL"
    assert profiles_report["checked_count"] == 0
    assert any(item["profile"] == "*" for item in profiles_report["violations"])


def test_skills_health_audit_fails_closed_on_resolved_cross_profile_skill_file(tmp_path):
    home = tmp_path / "home"
    active_root = home / "skills"
    other_skill = home / "profiles" / "other" / "skills" / "borrowed" / "SKILL.md"
    _skill(other_skill.parent, "borrowed", "Use when testing symlink profile boundary drift.")
    link_dir = active_root / "linked"
    link_dir.mkdir(parents=True)
    (link_dir / "SKILL.md").symlink_to(other_skill)

    report = audit_skills([active_root], hermes_home=home, active_profile="default")

    assert report["status"] == "FAIL"
    assert report["checked_count"] == 0
    assert any(item["code"] == "cross_profile_skill_root" for item in report["violations"])


def test_skills_health_audit_fails_closed_when_hermes_home_is_active_profile(tmp_path):
    home = tmp_path / "home"
    active_home = home / "profiles" / "active"
    other_root = home / "profiles" / "other" / "skills"
    _skill(active_home / "skills" / "local", "local", "Use when testing local profile skills.")
    _skill(other_root / "borrowed", "borrowed", "Use when testing profile boundary drift.")

    report = audit_skills([other_root], hermes_home=active_home, active_profile="active")

    assert report["status"] == "FAIL"
    violation = next(item for item in report["violations"] if item["code"] == "cross_profile_skill_root")
    assert violation["profile"] == "other"
    assert violation["active_profile"] == "active"


def test_skills_health_audit_infers_active_profile_from_profile_home(tmp_path):
    home = tmp_path / "home"
    active_home = home / "profiles" / "active"
    _skill(active_home / "skills" / "local", "local", "Use when testing local profile skills.")

    report = audit_skills(hermes_home=active_home)

    assert report["status"] == "PASS"
    assert report["active_profile"] == "active"
    assert report["checked_count"] == 1
    assert report["roots"][0]["cross_profile"] is False


def test_skills_health_cli_profile_home_default_root_passes(tmp_path, capsys):
    home = tmp_path / "home"
    active_home = home / "profiles" / "active"
    _skill(active_home / "skills" / "local", "local", "Use when testing local profile skills.")

    token = set_hermes_home_override(active_home)
    try:
        assert do_health() == 0
        output = capsys.readouterr().out
        assert '"active_profile": "active"' in output
        assert '"status": "PASS"' in output
    finally:
        reset_hermes_home_override(token)


def test_skills_health_cli_exit_semantics(tmp_path, capsys):
    home = tmp_path / "home"
    good_root = home / "skills"
    other_root = home / "profiles" / "other" / "skills"
    _skill(good_root / "good", "good", "Use when testing a healthy skill report.")
    _skill(other_root / "borrowed", "borrowed", "Use when testing profile boundary drift.")

    token = set_hermes_home_override(home)
    try:
        assert do_health([good_root]) == 0
        pass_output = capsys.readouterr().out
        assert '"status": "PASS"' in pass_output

        assert do_health([other_root]) == 1
        fail_output = capsys.readouterr().out
        assert '"status": "FAIL"' in fail_output
        assert "cross_profile_skill_root" in fail_output
    finally:
        reset_hermes_home_override(token)
