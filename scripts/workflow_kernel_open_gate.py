#!/usr/bin/env python3
"""Compile/apply the workflow-kernel limited-open manifest.

`--open` requires the exact confirmation string.  Writing an opened manifest only
arms a local governed capability; it does not send anything.  External delivery
must still be performed by a separate runner that validates the manifest,
idempotency, rate limits, and kill switch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workflow_kernel.io import write_json_artifact  # noqa: E402
from workflow_kernel.open_gate import OPEN_CONFIRMATION, build_open_manifest, validate_open_manifest  # noqa: E402

DEFAULT_OUT_DIR = REPO_ROOT / "docs/private/workflow-kernel-open-gate"
DEFAULT_MANIFEST = DEFAULT_OUT_DIR / "workflow_kernel_open_manifest_20260614.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true", help="write opened manifest if all readiness checks pass")
    parser.add_argument("--confirm-open", help=f"must equal {OPEN_CONFIRMATION} when --open is used")
    parser.add_argument("--operator", default="he-cheng")
    parser.add_argument("--payload", default=None)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    kwargs = {"opened": args.open, "confirmation": args.confirm_open, "operator": args.operator}
    if args.payload is not None:
        kwargs["payload"] = args.payload
    manifest = build_open_manifest(REPO_ROOT, **kwargs)
    validation = validate_open_manifest(manifest)
    result = {"manifest": manifest, "validation": validation}
    if args.write_report:
        result["artifact"] = write_json_artifact(args.out, manifest)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"workflow_kernel_open_gate: {manifest['status']} validation={validation['status']}")
        if args.write_report:
            print(f"artifact: {result['artifact']['path']}")
            print(f"sha256: {result['artifact']['sha256']}")
        for error in manifest.get("errors", []) + validation.get("errors", []):
            print(f"ERROR: {error}")
    return 0 if manifest["status"] in {"OPEN_READY_NOT_OPENED", "OPENED_LIMITED_UNATTENDED"} and validation["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
