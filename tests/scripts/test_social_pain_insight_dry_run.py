from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "social_pain_insight_dry_run.py"
    spec = importlib.util.spec_from_file_location("_social_pain_insight_dry_run", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_social_pain_collect_uses_public_hn_boundary_and_dedupes(monkeypatch):
    module = _load_module()

    calls: list[str] = []

    def fake_hn_search(query: str, tags: str, hits_per_page: int):
        calls.append(query)
        return [
            {
                "objectID": "123",
                "title": "Meeting transcription accuracy problem",
                "comment_text": "Speaker diarization and transcript accuracy are hard.",
                "created_at": "2026-01-01T00:00:00Z",
                "points": 10,
                "num_comments": 5,
            },
            {
                "objectID": "123",
                "title": "Duplicate should be removed",
                "comment_text": "Duplicate",
            },
        ]

    monkeypatch.setattr(module, "hn_search", fake_hn_search)

    records = module.collect("AI meeting notes", 8)

    assert len(records) == 1
    assert records[0]["url"] == "https://news.ycombinator.com/item?id=123"
    assert records[0]["cluster_id"] == "transcription_accuracy"
    assert calls


def test_social_pain_writes_markdown_and_csv_with_source_boundary(tmp_path, monkeypatch):
    module = _load_module()

    def fake_collect(category: str, limit: int):
        return [
            {
                "cluster_id": "privacy_and_consent",
                "platform": "Hacker News",
                "query": "meeting notes privacy recording",
                "url": "https://news.ycombinator.com/item?id=456",
                "title": "Privacy for meeting recorders",
                "excerpt": "Consent and retention are the core worries.",
                "date": "2026-01-02T00:00:00Z",
                "engagement": "points=5; comments=2",
            }
        ]

    monkeypatch.setattr(module, "collect", fake_collect)

    rc = module.main(["--category", "AI meeting notes", "--limit", "4", "--out-dir", str(tmp_path)])

    assert rc == 0
    md_files = list(tmp_path.glob("*-dry-run.md"))
    csv_files = list(tmp_path.glob("*-evidence.csv"))
    assert len(md_files) == 1
    assert len(csv_files) == 1
    text = md_files[0].read_text(encoding="utf-8")
    assert "public Hacker News Algolia API only" in text
    csv_text = csv_files[0].read_text(encoding="utf-8")
    assert "\r" not in csv_text
