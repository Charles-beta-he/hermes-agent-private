#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-final-boundary-retention-watch')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_final_boundary_retention_watch_20260613_phase85.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_final_boundary_retention_watch_20260613_phase85.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_watch(root:Path)->dict[str,Any]:
    boundary_path=root/'docs/private/workflow-kernel-publication-apply-final-boundary-packet/workflow_kernel_publication_apply_final_boundary_packet_20260613_phase84.json'
    boundary=read(boundary_path)
    checks={'boundary_ready':boundary.get('status')=='PUBLICATION_APPLY_FINAL_BOUNDARY_PACKET_READY','default_stop_private':boundary.get('recommended_default')=='A_STOP_AT_PRIVATE_REVIEW','selected_option_null':boundary.get('selected_option') is None,'packet_only':boundary.get('packet_only') is True,'final_boundary':boundary.get('final_boundary') is True,'apply_disabled':boundary.get('publication_apply_enabled') is False}
    drift=[k for k,v in checks.items() if not v]
    r={'schema':'workflow_kernel_publication_final_boundary_retention_watch_v1','phase':85,'status':'PUBLICATION_FINAL_BOUNDARY_RETENTION_WATCH_CLEAR' if not drift else 'PUBLICATION_FINAL_BOUNDARY_RETENTION_WATCH_DRIFT','created_at':now(),'checks':checks,'drift':drift,'input_refs':[{'name':'phase84_final_boundary','path':str(boundary_path.relative_to(root)),'sha256':sha(boundary_path),'status':boundary.get('status')}],'retention_policy':'final_boundary_packet_retained_fail_closed_no_apply','read_only':True,'packet_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase86 may compile read-only closure summary'}
    r['retention_digest']=digest({k:r[k] for k in r if k!='retention_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True); REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); REPORT_MD.write_text(f"# Workflow Kernel Publication Final Boundary Retention Watch — Phase 85\n\nStatus: **{r['status']}**\nDrift count: `{len(r['drift'])}`\nDigest: `{r['retention_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); r=build_watch(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
