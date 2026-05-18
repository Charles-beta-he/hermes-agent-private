import importlib.util
from pathlib import Path


def load_post_update_check():
    script = Path.home() / ".hermes" / "scripts" / "post-update-check.py"
    spec = importlib.util.spec_from_file_location("post_update_check_under_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_continuation_fragmentation_guard_flags_ad_hoc_resumable_writer(tmp_path):
    module = load_post_update_check()
    repo = tmp_path / "repo"
    scripts = tmp_path / "scripts"
    bad = repo / "agent" / "bad_resume.py"
    bad.parent.mkdir(parents=True)
    scripts.mkdir(parents=True)
    bad.write_text(
        "from pathlib import Path\n"
        "def save(summary):\n"
        "    p = Path.home() / '.hermes' / 'state' / 'custom_handoffs' / 'x.json'\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_text(summary)\n",
        encoding="utf-8",
    )

    issues = module.find_continuation_fragmentation_issues(repo, scripts)

    assert len(issues) == 1
    assert issues[0]["path"].endswith("agent/bad_resume.py")
    assert "custom_handoffs" in issues[0]["evidence"]


def test_continuation_fragmentation_guard_allows_canonical_continuation_writer(tmp_path):
    module = load_post_update_check()
    repo = tmp_path / "repo"
    scripts = tmp_path / "scripts"
    good = repo / "agent" / "good_resume.py"
    good.parent.mkdir(parents=True)
    scripts.mkdir(parents=True)
    good.write_text(
        "from agent.continuation import build_continuation_packet, write_continuation_packet\n"
        "def save(summary):\n"
        "    pkt = build_continuation_packet(scope='delegate', reason='timeout', summary=summary)\n"
        "    return write_continuation_packet(pkt)\n",
        encoding="utf-8",
    )

    issues = module.find_continuation_fragmentation_issues(repo, scripts)

    assert issues == []


def test_continuation_fragmentation_guard_ignores_tests_and_read_only_mentions(tmp_path):
    module = load_post_update_check()
    repo = tmp_path / "repo"
    scripts = tmp_path / "scripts"
    test_file = repo / "tests" / "test_bad_resume.py"
    read_only = repo / "agent" / "inspect_resume.py"
    test_file.parent.mkdir(parents=True)
    read_only.parent.mkdir(parents=True)
    scripts.mkdir(parents=True)
    test_file.write_text("Path('~/.hermes/state/custom_handoffs').write_text('fixture')\n", encoding="utf-8")
    read_only.write_text(
        "def inspect(text):\n"
        "    return 'handoff' in text and 'retry' in text\n",
        encoding="utf-8",
    )

    issues = module.find_continuation_fragmentation_issues(repo, scripts)

    assert issues == []
