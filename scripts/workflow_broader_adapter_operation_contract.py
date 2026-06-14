#!/usr/bin/env python3
"""Compile a fail-closed broader-adapter operation contract for the long-term Control Tower plan.

This is a read-only contract/index. It does not execute adapters, mutate task
lifecycle, send messages, read credentials, or enable publication/unattended
paths. It makes the current narrow write-through surface explicit and turns
broader adapter work into exact-target future gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/private/workflow-kernel-broader-adapter-operation-contract'

CURRENT_SUPPORTED = [
    {
        'adapter': 'taskpool_note',
        'operation': 'closeout_status_write',
        'status': 'implemented_narrow',
        'authority_required': 'approved closeout authorization + evidence hash + task note path boundary',
        'external_side_effect': False,
        'rollback_required': True,
    },
    {
        'adapter': 'kanban_db',
        'operation': 'closeout_status_write',
        'status': 'implemented_narrow',
        'authority_required': 'approved closeout authorization + evidence hash + sqlite task id boundary',
        'external_side_effect': False,
        'rollback_required': True,
    },
]

CANDIDATE_LOCKED = [
    {'adapter': 'github', 'operation': 'issue_or_pr_mutation', 'required_gate': 'exact repo/issue/pr id + payload digest + rollback/comment plan'},
    {'adapter': 'lark', 'operation': 'task_or_doc_mutation', 'required_gate': 'project profile boundary + exact object id + bot/user-token authority + rollback'},
    {'adapter': 'telegram', 'operation': 'notification_send', 'required_gate': 'exact allowlisted target + payload digest + idempotency + compensation'},
    {'adapter': 'publication_target', 'operation': 'external_publication_apply', 'required_gate': 'exact target + payload digest + rollback + blast-radius choice'},
    {'adapter': 'generic_workflow', 'operation': 'arbitrary_write_through', 'required_gate': 'not allowed without new adapter-specific contract'},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build_report() -> dict[str, Any]:
    body = {
        'schema': 'workflow_kernel_broader_adapter_operation_contract_v1',
        'status': 'BROADER_ADAPTER_OPERATION_CONTRACT_READY',
        'contract_policy': 'fail_closed_exact_target_required_for_new_adapter_operations',
        'current_supported_operations': CURRENT_SUPPORTED,
        'candidate_operations_locked': CANDIDATE_LOCKED,
        'global_invariants': {
            'no_generic_adapter_write_through': True,
            'external_side_effects_enabled': False,
            'unattended_dispatch_enabled': False,
            'publication_apply_enabled': False,
            'credential_read_required': False,
            'task_lifecycle_mutated_by_this_report': False,
        },
        'future_gate_required_fields': [
            'adapter', 'operation', 'exact_target_id', 'payload_digest', 'authority_actor_role',
            'idempotency_key', 'preflight_readback', 'rollback_or_compensation_plan', 'post_apply_readback',
        ],
        'safe_default_next': 'keep_current_narrow_taskpool_kanban_only',
        'read_only': True,
    }
    body['contract_digest'] = digest({k:v for k,v in body.items() if k != 'contract_digest'})
    return {'created_at': utc_now(), **body}


def write_report(report: dict[str, Any], out: Path = OUT) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / 'workflow_kernel_broader_adapter_operation_contract_20260615.json'
    md_path = out / 'workflow_kernel_broader_adapter_operation_contract_20260615.md'
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    rows = ['# Broader Adapter Operation Contract', '', f"Status: `{report['status']}`", '', f"Digest: `{report['contract_digest']}`", '', '## Current supported operations']
    for item in report['current_supported_operations']:
        rows.append(f"- `{item['adapter']}.{item['operation']}` — {item['status']} external_side_effect={item['external_side_effect']}")
    rows.extend(['', '## Candidate operations locked'])
    for item in report['candidate_operations_locked']:
        rows.append(f"- `{item['adapter']}.{item['operation']}` requires: {item['required_gate']}")
    rows.extend(['', '## Invariants', '```json', json.dumps(report['global_invariants'], ensure_ascii=False, indent=2, sort_keys=True), '```'])
    md_path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return {'json': str(json_path), 'markdown': str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-report', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    report = build_report()
    if args.write_report:
        report['artifacts'] = write_report(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(report['status'])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
