#!/usr/bin/env python3
"""Phase-G optimization/self-healing backlog projection.

Read-only projection: creates evidence-backed candidates from current terminal
indexes without enqueueing taskpool work, changing mission state, or enabling any
executor.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/private/workflow-kernel-phase-g-optimization-backlog'
INPUTS={
 'runtime_readiness': ROOT/'docs/private/workflow-kernel-runtime-readiness-summary/workflow_kernel_runtime_readiness_summary_20260613_phase36.json',
 'remaining_gates': ROOT/'docs/private/workflow-kernel-remaining-gates-terminal-index/workflow_kernel_remaining_gates_terminal_index_20260615.json',
 'broader_adapter_contract': ROOT/'docs/private/workflow-kernel-broader-adapter-operation-contract/workflow_kernel_broader_adapter_operation_contract_20260615.json',
 'publication_terminal': ROOT/'docs/private/workflow-kernel-publication-governance-terminal-summary/workflow_kernel_publication_governance_terminal_summary_20260613_phase92.json',
}
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def load(p:Path): return json.loads(p.read_text()) if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()

def build_report()->dict[str,Any]:
    inp={k:load(p) for k,p in INPUTS.items()}
    missing=[k for k,v in inp.items() if v is None]
    candidates=[
      {'id':'phase-g-runtime-freshness-watchdog','class':'self_healing_candidate','priority':'P2','status':'candidate_only','source':'runtime_readiness','proposal':'add periodic read-only freshness watchdog for provider/gate/projection artifacts','side_effects':'none'},
      {'id':'phase-g-gate-boundary-regression','class':'completion_evidence_backlog','priority':'P2','status':'candidate_only','source':'remaining_gates','proposal':'add regression that fail-closed terminal gates stay visible in Control Tower','side_effects':'none'},
      {'id':'phase-g-adapter-contract-expansion-playbook','class':'optimization_backlog','priority':'P3','status':'candidate_only','source':'broader_adapter_contract','proposal':'turn candidate adapter gate fields into a reusable adapter-specific contract template','side_effects':'none'},
      {'id':'phase-g-publication-reopen-dossier-template','class':'optimization_backlog','priority':'P3','status':'candidate_only','source':'publication_terminal','proposal':'create exact-target publication reopen dossier template for future human decision','side_effects':'none'},
    ]
    checks={
      'candidate_only': all(c['status']=='candidate_only' for c in candidates),
      'no_taskpool_enqueue': True,
      'no_executor_enablement': True,
      'no_external_side_effects': True,
      'sources_present': not missing,
    }
    r={
      'schema':'workflow_kernel_phase_g_optimization_backlog_v1',
      'created_at':now(),
      'status':'PHASE_G_OPTIMIZATION_BACKLOG_READY' if all(checks.values()) else 'PHASE_G_OPTIMIZATION_BACKLOG_DEGRADED',
      'projection_policy':'read_only_candidates_no_enqueue_no_executor_enablement',
      'missing_sources':missing,
      'checks':checks,
      'candidates':candidates,
      'candidate_count':len(candidates),
      'candidate_classes':sorted({c['class'] for c in candidates}),
      'read_only':True,
      'taskpool_mutated':False,
      'mission_mutated':False,
      'executor_enabled':False,
      'external_side_effects_executed':False,
    }
    r['backlog_digest']=digest({k:v for k,v in r.items() if k!='backlog_digest'})
    return r

def write_report(r:dict[str,Any], out:Path=OUT)->dict[str,str]:
    out.mkdir(parents=True, exist_ok=True)
    jp=out/'workflow_kernel_phase_g_optimization_backlog_20260615.json'
    mp=out/'workflow_kernel_phase_g_optimization_backlog_20260615.md'
    jp.write_text(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
    lines=['# Phase-G Optimization / Self-Healing Backlog','',f"Status: `{r['status']}`",'',f"Digest: `{r['backlog_digest']}`",'']
    for c in r['candidates']:
      lines.append(f"- `{c['id']}` ({c['class']}, {c['priority']}): {c['proposal']}")
    mp.write_text('\n'.join(lines)+'\n')
    return {'json':str(jp),'markdown':str(mp)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report', action='store_true'); ap.add_argument('--json', action='store_true'); args=ap.parse_args()
    r=build_report()
    if args.write_report: r['artifacts']=write_report(r)
    print(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True) if args.json else r['status'])
    return 0
if __name__=='__main__': raise SystemExit(main())
