#!/usr/bin/env python3
"""Validate a voice-clone consent/provenance manifest.

This CLI is a dry-run gate. It does not synthesize audio, download model
weights, contact APIs, or send media. Exit 0 means the local clone prototype is
selectable; exit 2 means blocked.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.voice_clone_policy import render_template, validate_manifest_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a voice-clone manifest without running TTS")
    parser.add_argument("manifest", nargs="?", help="Path to consent/provenance JSON manifest")
    parser.add_argument("--template", action="store_true", help="Print a sample manifest template")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    if args.template:
        print(json.dumps(render_template(), ensure_ascii=False, indent=2))
        return 0

    if not args.manifest:
        parser.error("manifest is required unless --template is used")

    result = validate_manifest_file(args.manifest)
    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Voice clone gate: {payload['status']}")
        for finding in payload["findings"]:
            print(f"- {finding['level'].upper()} {finding['code']}: {finding['message']}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
