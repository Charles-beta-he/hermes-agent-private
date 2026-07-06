from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENT_OS_PAGE = ROOT / "web" / "src" / "pages" / "AgentOsPage.tsx"


def test_agent_os_page_has_local_receipt_hash_verification_surface():
    source = AGENT_OS_PAGE.read_text(encoding="utf-8")

    assert "Verify handoff hash" in source
    assert "verificationInput" in source
    assert "expectedHashInput" in source
    assert "verification_status" in source
    assert "match" in source
    assert "mismatch" in source


def test_agent_os_page_receipt_verification_keeps_export_and_execution_boundaries_closed():
    source = AGENT_OS_PAGE.read_text(encoding="utf-8")

    assert "write clipboard" in source
    assert "export files" in source
    assert "dispatch messages" in source
    assert "unlock execution" in source
    assert "navigator.clipboard" not in source
    assert "download" not in source
    assert "window.open" not in source
