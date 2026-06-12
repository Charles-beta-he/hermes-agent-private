#!/usr/bin/env python3
"""Emit Hermes tool intent/risk review dry-run sidecars.

This is a bounded evidence generator for the pre-tool gate rollout. It does not
execute the requested tool and does not mutate Hermes runtime policy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.tool_intent_review import review_tool_intent, write_review_sidecar  # noqa: E402


def _load_json_object(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--args-json must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("--args-json must decode to an object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-name", required=True)
    parser.add_argument("--args-json", default="{}")
    parser.add_argument("--intent", default="")
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--cron-mode", action="store_true")
    parser.add_argument("--out", help="Optional JSON sidecar path")
    ns = parser.parse_args(argv)

    review = review_tool_intent(
        ns.tool_name,
        _load_json_object(ns.args_json),
        intent_summary=ns.intent,
        evidence_refs=list(ns.evidence_ref or []),
        cron_mode=ns.cron_mode,
    )
    payload = review.to_payload()
    if ns.out:
        sidecar = write_review_sidecar(review, ns.out)
        payload["sidecar_path"] = str(sidecar)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
