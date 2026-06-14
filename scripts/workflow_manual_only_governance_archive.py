#!/usr/bin/env python3
"""Phase 68: manual-only governance archive/index.

Builds a read-only audit index for the closed Workflow Kernel external-dispatch
ladder. No apply, no cron mutation, no external send.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-manual-only-governance-archive')
REPORT_JSON=REPORT_DIR/'workflow_kernel_manual_only_governance_archive_20260613_phase68.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_manual_only_governance_archive_20260613_phase68.md'
PHASES=[57,60,64,65,66,67]
FILES={57:'docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json',60:'docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json',64:'docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json',65:'docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json',66:'docs/private/workflow-kernel-future-unattended-reopen-packet/workflow_kernel_future_unattended_reopen_packet_20260613_phase66.json',67:'docs/private/workflow-kernel-single-point-reopen-choice/workflow_kernel_single_point_reopen_choice_20260613_phase67.json'}
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_archive(root:Path)->dict[str,Any]:
    items=[]; missing=[]
    for ph in PHASES:
        p=root/FILES[ph]; data=read(p)
        if not data: missing.append(ph)
        items.append({'phase':ph,'path':FILES[ph],'status':data.get('status'),'sha256':sha(p)})
    checks={
      'phase67_keep_closed': read(root/FILES[67]).get('choice')=='A_NO_REOPEN_KEEP_MANUAL_ONLY',
      'phase66_apply_disabled': read(root/FILES[66]).get('reopen_apply_enabled') is False,
      'phase65_monitor_clear': read(root/FILES[65]).get('status')=='MANUAL_ONLY_MONITORING_CLEAR',
      'phase64_closure_ready': read(root/FILES[64]).get('status')=='MANUAL_ONLY_CLOSURE_READY',
      'artifacts_present': not missing,
    }
    blockers=[k for k,v in checks.items() if not v]
    a={'schema':'workflow_kernel_manual_only_governance_archive_v1','phase':68,'status':'MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY' if not blockers else 'MANUAL_ONLY_GOVERNANCE_ARCHIVE_BLOCKED','created_at':now(),'items':items,'checks':checks,'blockers':blockers,'archive_policy':'read_only_index_closed_ladder','unattended_dispatch_enabled':False,'reopen_apply_enabled':False,'external_side_effects_executed':False,'cron_state_mutated':False,'message_sent':False,'read_only':True,'next_boundary':'Phase69 periodic closure health report or stop at archive'}
    a['archive_digest']=digest({k:a[k] for k in a if k!='archive_digest'})
    return a
def write_archive(a):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(a,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    rows=[f"- Phase {x['phase']}: `{x.get('status')}` `{x.get('sha256')}`" for x in a['items']]
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Manual-only Governance Archive — Phase 68','',f"Status: **{a['status']}**",f"Archive digest: `{a['archive_digest']}`",'',*rows])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); args=ap.parse_args(argv)
    a=build_archive(Path.cwd())
    if args.write_report: write_archive(a)
    if args.json: print(json.dumps(a,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if a['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
