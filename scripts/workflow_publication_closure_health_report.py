#!/usr/bin/env python3
"""Phase 79: publication closure health report.

Read-only health report over publication/no-apply invariants.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-closure-health-report')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_closure_health_report_20260613_phase79.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_closure_health_report_20260613_phase79.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_report(root:Path)->dict[str,Any]:
    refs={
      'phase72_gate': root/'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json',
      'phase74_authorization': root/'docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json',
      'phase76_choice': root/'docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json',
      'phase78_reopen': root/'docs/private/workflow-kernel-publication-apply-reopen-packet/workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json',
    }
    data={k:read(p) for k,p in refs.items()}
    checks={
      'exact_target_gate_ready': data['phase72_gate'].get('status')=='PUBLICATION_EXACT_TARGET_GATE_READY',
      'authorization_packet_ready': data['phase74_authorization'].get('status')=='PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY',
      'choice_keep_private': data['phase76_choice'].get('choice')=='A_KEEP_PRIVATE_NO_APPLY',
      'reopen_packet_ready': data['phase78_reopen'].get('status')=='PUBLICATION_APPLY_REOPEN_PACKET_READY',
      'all_apply_disabled': all(d.get('publication_apply_enabled') is False for d in data.values()),
      'all_external_publication_disabled': all(d.get('external_publication_enabled') in (False, None) for d in data.values()),
    }
    unhealthy=[k for k,v in checks.items() if not v]
    r={'schema':'workflow_kernel_publication_closure_health_report_v1','phase':79,'status':'PUBLICATION_CLOSURE_HEALTH_REPORT_CLEAR' if not unhealthy else 'PUBLICATION_CLOSURE_HEALTH_REPORT_DEGRADED','created_at':now(),'health':'clear' if not unhealthy else 'degraded','checks':checks,'unhealthy_checks':unhealthy,'input_refs':[{'name':k,'path':str(p.relative_to(root)),'sha256':sha(p),'status':data[k].get('status')} for k,p in refs.items()],'read_only':True,'stage_report_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase80 may update governance archive as read-only index'}
    r['health_digest']=digest({k:r[k] for k in r if k!='health_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Publication Closure Health Report — Phase 79','',f"Status: **{r['status']}**",f"Health: `{r['health']}`",f"Unhealthy checks: `{len(r['unhealthy_checks'])}`",f"Digest: `{r['health_digest']}`"])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_report(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
