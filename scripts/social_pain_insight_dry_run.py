#!/usr/bin/env python3
"""Public-source-only social pain insight dry-run.

This is intentionally small and deterministic enough for taskpool evidence:
- Uses only public Hacker News Algolia API endpoints (no login, no paid API).
- Preserves raw URLs/excerpts per pain cluster.
- Emits both Markdown and CSV artifacts.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"

CLUSTERS = [
    {
        "id": "transcription_accuracy",
        "title": "Transcription accuracy and speaker understanding",
        "keywords": ["transcription", "transcribe", "speaker", "diarization", "accuracy", "caption", "summarize", "summary"],
        "trend": "stable",
        "angles": ["domain vocabulary correction", "speaker-aware meeting notes", "confidence-visible summaries"],
    },
    {
        "id": "privacy_and_consent",
        "title": "Privacy, consent, and data-retention anxiety",
        "keywords": ["privacy", "consent", "record", "recording", "retention", "data", "gdpr", "security", "confidential"],
        "trend": "rising",
        "angles": ["local-first capture", "explicit consent ledger", "retention-by-default controls"],
    },
    {
        "id": "workflow_integration",
        "title": "Workflow integration and action-item follow-through",
        "keywords": ["calendar", "slack", "notion", "jira", "crm", "action item", "follow up", "workflow", "integrat"],
        "trend": "stable",
        "angles": ["task-system handoff", "source-linked action items", "post-meeting verification"],
    },
    {
        "id": "cost_and_vendor_lockin",
        "title": "Cost, vendor lock-in, and subscription fatigue",
        "keywords": ["price", "pricing", "subscription", "expensive", "cost", "lock", "vendor", "cancel"],
        "trend": "unknown",
        "angles": ["BYO-model/local model mode", "transparent per-meeting cost", "export-first notes"],
    },
]


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def hn_search(query: str, tags: str, hits_per_page: int) -> list[dict[str, Any]]:
    # HN Algolia treats comma-separated tags as an AND-style filter for many
    # combinations; for this dry-run we leave tags unset and keep the raw hit URL
    # plus excerpt so the output remains auditable.
    params = urllib.parse.urlencode({"query": query, "hitsPerPage": hits_per_page})
    req = urllib.request.Request(
        f"{HN_SEARCH_URL}?{params}",
        headers={"User-Agent": "Mozilla/5.0 HermesSocialPainDryRun/1.0", "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310 public API read-only
                payload = json.loads(resp.read().decode("utf-8"))
            return list(payload.get("hits", []))
        except Exception as exc:  # fail-soft per-query; final failure returns no records for that query
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    print(f"WARN: public source query failed: {query!r}: {last_error}", file=sys.stderr)
    return []


def item_url(hit: dict[str, Any]) -> str:
    object_id = str(hit.get("objectID") or hit.get("story_id") or "")
    return f"https://news.ycombinator.com/item?id={object_id}" if object_id else str(hit.get("url") or "")


def normalize_hit(hit: dict[str, Any], query: str) -> dict[str, str]:
    title = strip_html(str(hit.get("title") or hit.get("story_title") or "HN discussion"))
    comment = strip_html(str(hit.get("comment_text") or hit.get("story_text") or ""))
    excerpt = comment or title
    excerpt = textwrap.shorten(excerpt, width=320, placeholder=" …")
    return {
        "platform": "Hacker News",
        "query": query,
        "url": item_url(hit),
        "title": title,
        "excerpt": excerpt,
        "date": str(hit.get("created_at") or ""),
        "engagement": f"points={hit.get('points', '')}; comments={hit.get('num_comments', '')}",
    }


def assign_cluster(record: dict[str, str]) -> str:
    haystack = f"{record['title']} {record['excerpt']}".lower()
    best_id = "workflow_integration"
    best_score = -1
    for cluster in CLUSTERS:
        score = sum(1 for keyword in cluster["keywords"] if keyword in haystack)
        if score > best_score:
            best_id = cluster["id"]
            best_score = score
    return best_id


def opportunity_score(cluster: dict[str, Any], records: list[dict[str, str]]) -> tuple[int, int]:
    frequency = len(records)
    severity_seed = {
        "privacy_and_consent": 5,
        "transcription_accuracy": 4,
        "workflow_integration": 4,
        "cost_and_vendor_lockin": 3,
    }.get(cluster["id"], 3)
    source_diversity = len({r["query"] for r in records})
    score = min(100, severity_seed * 12 + frequency * 6 + source_diversity * 5)
    return severity_seed, score


def collect(category: str, limit: int) -> list[dict[str, str]]:
    queries = [
        f"{category} privacy recording",
        f"{category} transcription accuracy",
        f"{category} action items workflow",
        f"{category} pricing subscription",
    ]
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    per_query = max(2, limit // len(queries))
    for query in queries:
        for hit in hn_search(query, "story,comment", per_query):
            rec = normalize_hit(hit, query)
            if not rec["url"] or rec["url"] in seen:
                continue
            seen.add(rec["url"])
            rec["cluster_id"] = assign_cluster(rec)
            records.append(rec)
    return records[:limit]


def write_csv(records: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["cluster_id", "platform", "query", "url", "title", "excerpt", "date", "engagement"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def write_markdown(category: str, records: list[dict[str, str]], md_path: Path, csv_path: Path) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    grouped = {cluster["id"]: [] for cluster in CLUSTERS}
    for record in records:
        grouped.setdefault(record["cluster_id"], []).append(record)

    lines: list[str] = []
    lines.append(f"# social-pain-insight-loop-v1 public-source dry-run — {category}")
    lines.append("")
    lines.append(f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## Input contract")
    lines.append("")
    lines.append(f"- Category: {category}")
    lines.append("- Sources: public Hacker News Algolia API only; no login/session scraping; no paid API; no stealth/anti-bot bypass.")
    lines.append("- Required output: Markdown report + CSV evidence table; every cluster keeps raw source URL and excerpt.")
    lines.append("- Scoring rubric: severity seed × 12 + evidence frequency × 6 + query diversity × 5, capped at 100.")
    lines.append("")
    lines.append("## Collection plan")
    lines.append("")
    lines.append("1. Generate bounded public-source queries for privacy, transcription accuracy, workflow/action-item handoff, and pricing.")
    lines.append("2. Fetch public HN story/comment search hits through `https://hn.algolia.com/api/v1/search`.")
    lines.append("3. Normalize title/excerpt/date/engagement and retain source URLs.")
    lines.append("4. Rule-cluster by explicit keywords; do not synthesize unsupported facts.")
    lines.append("5. Export CSV for audit/read-back and Markdown for operator review.")
    lines.append("")
    lines.append(f"CSV artifact: `{csv_path}`")
    lines.append("")
    lines.append("## Pain clusters")
    lines.append("")
    for cluster in CLUSTERS:
        cluster_records = grouped.get(cluster["id"], [])[:5]
        if not cluster_records:
            continue
        severity, score = opportunity_score(cluster, cluster_records)
        lines.append(f"### {cluster['id']} — {cluster['title']}")
        lines.append("")
        lines.append(f"- Severity: {severity}/5")
        lines.append(f"- Frequency in dry-run sample: {len(cluster_records)}")
        lines.append(f"- Trend label: {cluster['trend']}")
        lines.append(f"- Opportunity score: {score}/100")
        lines.append(f"- Suggested product angles: {', '.join(cluster['angles'])}")
        lines.append("- Evidence:")
        for rec in cluster_records:
            lines.append(f"  - URL: {rec['url']}")
            lines.append(f"    - Query: `{rec['query']}`")
            lines.append(f"    - Date: {rec['date']}")
            lines.append(f"    - Excerpt: {rec['excerpt']}")
        lines.append("")
    lines.append("## Next validation")
    lines.append("")
    lines.append("- Repeat with a second public source class such as public app-store review pages before any cron/service promotion.")
    lines.append("- Add source-specific robots/ToS notes per adapter before expanding beyond HN public API.")
    lines.append("- Keep recurring execution behind an explicit human gate; this dry-run is one-shot evidence only.")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", default="AI meeting notes and transcription tools")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--out-dir", type=Path, default=Path.home() / ".hermes" / "docs" / "private" / "social-pain-insight-loop")
    args = parser.parse_args(argv)

    records = collect(args.category, args.limit)
    if not records:
        print(json.dumps({"status": "FAIL", "reason": "no_public_records"}, ensure_ascii=False))
        return 2

    slug = re.sub(r"[^a-z0-9]+", "-", args.category.lower()).strip("-")[:80]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    md_path = args.out_dir / f"{stamp}-{slug}-dry-run.md"
    csv_path = args.out_dir / f"{stamp}-{slug}-evidence.csv"
    write_csv(records, csv_path)
    write_markdown(args.category, records, md_path, csv_path)

    summary = {
        "status": "OK",
        "category": args.category,
        "record_count": len(records),
        "clusters": sorted({r["cluster_id"] for r in records}),
        "markdown": str(md_path),
        "csv": str(csv_path),
        "source_boundary": "public_hn_algolia_no_login_no_paid_api_no_stealth",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
