#!/usr/bin/env python3
"""Read-only terminal index for unattended dispatch and publication gates."""
from __future__ import annotations

import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/private/workflow-kernel-remaining-gates-terminal-index'
PATHS={
 'manual_only_closure': ROOT/'docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json',
 'manual_only_monitoring': ROOT/'docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json',
 'future_unattended_reopen_packet': ROOT/'docs/private/workflow-kernel-future-unattended-reopen-packet/workflow_kernel_future_unattended_reopen_packet_20260613_phase66.json',
 'publication_terminal_summary': ROOT/'docs/private/workflow-kernel-publication-governance-terminal-summary/workflow_kernel_publication_governance_terminal_summary_20260613_phase92.json',
 'broader_adapter_contract': ROOT/'docs/private/workflow-kernel-broader-adapter-operation-contract/workflow_kernel_broader_adapter_operation_contract_20260615.json',
}

def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def load(p:Path): return json.loads(p.read_text()) if p.exists() else None
def digest(o:Any)->str: return hashlib.sha256(json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()

def build_report()->dict[str,Any]:
    data={k:load(p) for k,p in PATHS.items()}
    missing=[k for k,v in data.items() if v is None]
    checks={
      'manual_only_closed': (data['manual_only_closure'] or {}).get('unattended_dispatch_enabled') is False,
      'manual_monitoring_clear': (data['manual_only_monitoring'] or {}).get('status') == 'MANUAL_ONLY_MONITORING_CLEAR',
      'future_unattended_reopen_packet_only': (data['future_unattended_reopen_packet'] or {}).get('packet_only') is True and (data['future_unattended_reopen_packet'] or {}).get('reopen_apply_enabled') is False,
      'publication_terminal_closed': (data['publication_terminal_summary'] or {}).get('publication_apply_enabled') is False and (data['publication_terminal_summary'] or {}).get('terminal_state') == 'closed_no_apply_human_gated_reopen_only',
      'broader_adapter_fail_closed': ((data['broader_adapter_contract'] or {}).get('global_invariants') or {}).get('no_generic_adapter_write_through') is True,
    }
    status='REMAINING_GATES_TERMINAL_INDEX_READY' if not missing and all(checks.values()) else 'REMAINING_GATES_TERMINAL_INDEX_DEGRADED'
    report={
      'schema':'workflow_kernel_remaining_gates_terminal_index_v1',
      'created_at':now(),
      'status':status,
      'index_policy':'read_only_terminal_index_no_apply_no_unattended_no_generic_adapter_write',
      'missing_artifacts':missing,
      'checks':checks,
      'terminal_gates':{
        'unattended_dispatch':{'state':'manual_only', 'enabled':False, 'reopen_requires':'fresh human decision + exact target/payload/idempotency/rollback'},
        'publication_apply':{'state':'closed_no_apply_human_gated_reopen_only', 'enabled':False, 'reopen_requires':'human exact target + payload digest + rollback + blast-radius choice'},
        'broader_adapter_operations':{'state':'fail_closed_contract_only', 'enabled':False, 'reopen_requires':'adapter-specific contract + exact operation target'},
      },
      'read_only':True,
      'external_side_effects_executed':False,
      'unattended_dispatch_enabled':False,
      'publication_apply_enabled':False,
    }
    report['index_digest']=digest({k:v for k,v in report.items() if k!='index_digest'})
    return report

def write_report(report:dict[str,Any], out:Path=OUT)->dict[str,str]:
    out.mkdir(parents=True, exist_ok=True)
    jp=out/'workflow_kernel_remaining_gates_terminal_index_20260615.json'
    mp=out/'workflow_kernel_remaining_gates_terminal_index_20260615.md'
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
    lines=['# Remaining Gates Terminal Index','',f"Status: `{report['status']}`",'',f"Digest: `{report['index_digest']}`",'', '```json', json.dumps(report['checks'], ensure_ascii=False, indent=2, sort_keys=True), '```']
    mp.write_text('\n'.join(lines)+'\n')
    return {'json':str(jp),'markdown':str(mp)}

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); args=ap.parse_args()
    r=build_report()
    if args.write_report: r['artifacts']=write_report(r)
    print(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True) if args.json else r['status'])
    return 0
if __name__=='__main__': raise SystemExit(main())
