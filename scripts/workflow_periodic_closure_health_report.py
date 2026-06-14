#!/usr/bin/env python3
"""Phase 69: periodic closure health report.

Read-only periodic report over the archived manual-only Workflow Kernel ladder.
No publication, no external send, no cron mutation, no reopen apply.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-periodic-closure-health-report')
REPORT_JSON=REPORT_DIR/'workflow_kernel_periodic_closure_health_report_20260613_phase69.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_periodic_closure_health_report_20260613_phase69.md'
INPUTS={
 'archive':'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json',
 'monitoring':'docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json',
 'choice':'docs/private/workflow-kernel-single-point-reopen-choice/workflow_kernel_single_point_reopen_choice_20260613_phase67.json',
 'sequence':'docs/private/workflow-kernel-sequence-map/workflow_kernel_sequence_map_20260613_phase38.json',
}
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_report(root:Path)->dict[str,Any]:
    docs={k:read(root/v) for k,v in INPUTS.items()}
    refs=[{'name':k,'path':v,'sha256':sha(root/v),'status':docs[k].get('status')} for k,v in INPUTS.items()]
    checks={
      'archive_ready': docs['archive'].get('status')=='MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY',
      'archive_read_only': docs['archive'].get('read_only') is True,
      'monitoring_clear': docs['monitoring'].get('status')=='MANUAL_ONLY_MONITORING_CLEAR',
      'monitoring_no_drift': docs['monitoring'].get('drift',[])==[],
      'choice_keep_closed': docs['choice'].get('choice')=='A_NO_REOPEN_KEEP_MANUAL_ONLY',
      'sequence_phase68_or_later': int(docs['sequence'].get('phase_count') or 0) >= 52,
    }
    blockers=[k for k,v in checks.items() if not v]
    report={'schema':'workflow_kernel_periodic_closure_health_report_v1','phase':69,'status':'PERIODIC_CLOSURE_HEALTH_REPORT_CLEAR' if not blockers else 'PERIODIC_CLOSURE_HEALTH_REPORT_DEGRADED','created_at':now(),'input_refs':refs,'checks':checks,'blockers':blockers,'health':'clear' if not blockers else 'degraded','stage_report_only':True,'read_only':True,'reopen_apply_enabled':False,'unattended_dispatch_enabled':False,'external_side_effects_executed':False,'cron_state_mutated':False,'message_sent':False,'next_boundary':'Phase70 archive publication packet remains packet-only'}
    report['health_digest']=digest({k:report[k] for k in report if k!='health_digest'})
    return report
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    rows=[f"- {x['name']}: `{x.get('status')}` `{x.get('sha256')}`" for x in r['input_refs']]
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Periodic Closure Health Report — Phase 69','',f"Status: **{r['status']}**",f"Health: `{r['health']}`",f"Digest: `{r['health_digest']}`",'',*rows])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); args=ap.parse_args(argv)
    r=build_report(Path.cwd())
    if args.write_report: write_report(r)
    if args.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
