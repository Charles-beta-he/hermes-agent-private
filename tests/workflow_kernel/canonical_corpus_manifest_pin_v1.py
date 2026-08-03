"""Independent hardcoded pin for canonical corpus manifest raw SHA (4A.5B.12).

Does NOT import the generator validator or generator constants. Used as a second
authority alongside the governing SSOT append-only invariant.
"""
from __future__ import annotations

# Full SHA-256 of tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-canonical-4a5b11.manifest.json
CANONICAL_CORPUS_MANIFEST_RAW_SHA256_V1 = "c0f935c09ae6bbf16ec8eb066bdad1a5b1426cc8be6a6be5da8eed6f658e40fd"
BASELINE_MANIFEST_RAW_SHA256_V1 = "fa4566baeb9fe32a8e8146a9201f1142fdc8040d0a5ed450b47814f2a9f0b960"
CANONICAL_CORPUS_MANIFEST_RELPATH_V1 = (
    "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-canonical-4a5b11.manifest.json"
)
BASELINE_MANIFEST_RELPATH_V1 = (
    "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json"
)
