#!/usr/bin/env python3
"""Read-only doctor for the Autonomous Runtime Governance documentation takeover.

This script validates that the new canonical packet has taken over architecture
interpretation without deleting or rewriting historical evidence stores.
It performs local file readback only and writes no state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PROFILE = Path('/Users/charles/.hermes/project-profiles/hermes-agent-os-control-plane.json')
PROGRESS_LEDGER = Path('/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/progress-ledger.md')
CANONICAL_INDEX = ROOT / '00-index.md'

REQUIRED_DOCS = [
    '00-index.md',
    '01-vision-and-principles.md',
    '02-current-architecture-map.md',
    '03-target-architecture.md',
    '04-core-domain-model.md',
    '05-control-loop-and-reconciliation.md',
    '06-evidence-ledger-and-closure.md',
    '07-authority-and-side-effect-governance.md',
    '08-runtime-surfaces.md',
    '09-subsystem-mapping.md',
    '10-migration-roadmap.md',
    '11-adr-index.md',
    '12-verification-and-acceptance.md',
    '13-local-brain-absorption.md',
    '14-takeover-disposition-manifest.md',
]

BANNER_SOURCES = [
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/00-model/agent-os-control-plane-ssot.md',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/01-architecture/capability-control-plane-architecture.md',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/02-roadmap/phase-roadmap.md',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/03-governance/default-off-gates.md',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/06-projection/control-surface-projection.md',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/07-shadow/resume-layer-contract.md',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/07-shadow/interruptibility-policy-shadow.md',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/taskpool-concurrency-certification-plan-20260627.md',
    '/Users/charles/.hermes/hermes-agent/docs/private/hermes-control-tower-long-term-stable-plan-20260611.md',
    '/Users/charles/.hermes/hermes-agent/docs/private/workflow-kernel-architecture-migration/workflow_kernel_architecture_migration_20260613_phase25.md',
    '/Users/charles/.hermes/hermes-agent/docs/private/modular-workflow-kernel-phase25-architecture-migration-adr.md',
    '/Users/charles/.hermes/hermes-agent/docs/private/workflow-kernel-architecture-selfcheck/arch_selfcheck_20260613_phase21.md',
    '/Users/charles/.hermes/hermes-agent/docs/private/agent-architecture-coverage-plan-20260608.md',
]

JSON_SOURCES = [
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/01-architecture/agent-os-schemas.json',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/03-governance/remaining-gates-index.json',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/yodaos-hermes-analysis-evidence.json',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/11-high-risk/exact-execution-requirements-20260627.json',
    '/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/11-high-risk/real-execution-authority-and-destructive-smoke-20260627.json',
]

REQUIRED_NEEDLES = {
    '00-index.md': [
        'Hermes Autonomous Runtime Governance Architecture',
        'overlay/canonical-index',
        '14-takeover-disposition-manifest.md',
    ],
    '07-authority-and-side-effect-governance.md': [
        'Default-off and shadow-first classes',
        'Exact high-risk execution requirements',
    ],
    '08-runtime-surfaces.md': [
        'Resume layer contract',
        'Interruptibility policy',
    ],
    '10-migration-roadmap.md': [
        'P0.5 — Local brain overlay, not overwrite',
        'Readiness-before-scale',
    ],
    '12-verification-and-acceptance.md': [
        'Readiness-before-scale acceptance',
        'High-risk smoke acceptance pattern',
    ],
    '13-local-brain-absorption.md': [
        'Replacement strategy: do not overwrite the old local brain',
        'Project profile is scoped local brain',
    ],
    '14-takeover-disposition-manifest.md': [
        'Markdown sources with in-file disposition banners',
        'JSON/profile sources governed by this manifest',
    ],
}


def check() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []

    missing_docs = [name for name in REQUIRED_DOCS if not (ROOT / name).exists()]
    if missing_docs:
        findings.append({'severity': 'error', 'code': 'missing_required_docs', 'items': missing_docs})

    for name, needles in REQUIRED_NEEDLES.items():
        path = ROOT / name
        text = path.read_text(encoding='utf-8') if path.exists() else ''
        missing = [needle for needle in needles if needle not in text]
        if missing:
            findings.append({'severity': 'error', 'code': 'missing_required_needles', 'file': str(path), 'items': missing})

    index_text = CANONICAL_INDEX.read_text(encoding='utf-8') if CANONICAL_INDEX.exists() else ''
    unreferenced = [name for name in REQUIRED_DOCS[1:] if name not in index_text]
    if unreferenced:
        findings.append({'severity': 'error', 'code': 'doc_not_referenced_from_index', 'items': unreferenced})

    banner_missing = []
    for source in BANNER_SOURCES:
        path = Path(source)
        if not path.exists():
            banner_missing.append({'path': source, 'reason': 'missing_file'})
            continue
        head = path.read_text(encoding='utf-8')[:1400]
        if 'Autonomous Runtime Governance disposition' not in head:
            banner_missing.append({'path': source, 'reason': 'missing_banner'})
    if banner_missing:
        findings.append({'severity': 'error', 'code': 'source_disposition_banner_missing', 'items': banner_missing})

    json_missing = [source for source in JSON_SOURCES if not Path(source).exists()]
    if json_missing:
        findings.append({'severity': 'error', 'code': 'json_source_missing', 'items': json_missing})

    if PROJECT_PROFILE.exists():
        profile = json.loads(PROJECT_PROFILE.read_text(encoding='utf-8'))
        docs = profile.get('canonical_docs', {})
        expected_index = str(CANONICAL_INDEX)
        if docs.get('autonomous_runtime_governance_index') != expected_index:
            findings.append({
                'severity': 'error',
                'code': 'project_profile_not_pointing_to_arg_index',
                'expected': expected_index,
                'actual': docs.get('autonomous_runtime_governance_index'),
            })
        if profile.get('current_components', {}).get('erg_runtime') != 'target-architecture/evidence-governed-reconciliation-runtime':
            findings.append({'severity': 'error', 'code': 'project_profile_missing_erg_runtime_component'})
    else:
        findings.append({'severity': 'error', 'code': 'missing_project_profile', 'path': str(PROJECT_PROFILE)})

    if PROGRESS_LEDGER.exists():
        ledger_text = PROGRESS_LEDGER.read_text(encoding='utf-8')
        for needle in ['Autonomous Runtime Governance canonical documentation packet created', 'Local brain source absorption into Autonomous Runtime Governance packet']:
            if needle not in ledger_text:
                findings.append({'severity': 'warn', 'code': 'progress_ledger_missing_marker', 'needle': needle})
    else:
        findings.append({'severity': 'warn', 'code': 'missing_progress_ledger', 'path': str(PROGRESS_LEDGER)})

    status = 'pass' if not any(f['severity'] == 'error' for f in findings) else 'fail'
    return {
        'status': status,
        'root': str(ROOT),
        'canonical_index': str(CANONICAL_INDEX),
        'required_doc_count': len(REQUIRED_DOCS),
        'banner_source_count': len(BANNER_SOURCES),
        'json_source_count': len(JSON_SOURCES),
        'findings': findings,
        'scope': 'read-only documentation takeover doctor; no runtime authority or side effects',
    }


if __name__ == '__main__':
    print(json.dumps(check(), ensure_ascii=False, indent=2))
