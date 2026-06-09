from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RETIRED_NORM_FILES = [
    "agent/norm_write_guard.py",
    "agent/norm_governance.py",
    "agent/norm_taskpool_gate.py",
    "hermes_cli/norms.py",
]
RUNTIME_PY_DIRS = ["agent", "tools", "hermes_cli", "gateway", "cron"]
FORBIDDEN_RUNTIME_REFERENCES = [
    "agent.norm_write_guard",
    "agent.norm_governance",
    "agent.norm_taskpool_gate",
    "hermes_cli.norms",
]


def test_retired_norm_governance_files_are_not_present() -> None:
    missing = [path for path in RETIRED_NORM_FILES if (REPO_ROOT / path).exists()]

    assert missing == []


def test_runtime_code_does_not_keep_inert_norm_governance_imports() -> None:
    offenders: list[str] = []

    for directory in RUNTIME_PY_DIRS:
        root = REPO_ROOT / directory
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in FORBIDDEN_RUNTIME_REFERENCES:
                if marker in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {marker}")

    assert offenders == []
