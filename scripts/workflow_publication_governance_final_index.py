#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-governance-final-index')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_governance_final_index_20260613_phase90.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_governance_final_index_20260613_phase90.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_index(root:Path)->dict[str,Any]:
    entries=[
      ('phase70_archive_publication_packet','docs/private/workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json'),
      ('phase74_apply_authorization_packet','docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json'),
      ('phase78_reopen_packet','docs/private/workflow-kernel-publication-apply-reopen-packet/workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json'),
      ('phase82_export_review_packet','docs/private/workflow-kernel-publication-export-review-packet/workflow_kernel_publication_export_review_packet_20260613_phase82.json'),
      ('phase84_final_boundary_packet','docs/private/workflow-kernel-publication-apply-final-boundary-packet/workflow_kernel_publication_apply_final_boundary_packet_20260613_phase84.json'),
      ('phase86_governance_closure_summary','docs/private/workflow-kernel-publication-governance-closure-summary/workflow_kernel_publication_governance_closure_summary_20260613_phase86.json'),
      ('phase88_reopen_human_gate_packet','docs/private/workflow-kernel-publication-apply-reopen-human-gate-packet/workflow_kernel_publication_apply_reopen_human_gate_packet_20260613_phase88.json'),
      ('phase89_human_gate_retention_watch','docs/private/workflow-kernel-publication-human-gate-retention-watch/workflow_kernel_publication_human_gate_retention_watch_20260613_phase89.json'),
    ]
    items=[]; missing=[]; apply_drift=[]
    for name,rel in entries:
        p=root/rel; d=read(p)
        if not p.exists(): missing.append(name)
        if d.get('publication_apply_enabled') not in (False, None): apply_drift.append(name)
        items.append({'name':name,'path':rel,'sha256':sha(p),'status':d.get('status'),'publication_apply_enabled':d.get('publication_apply_enabled')})
    blockers=missing+apply_drift
    idx={'schema':'workflow_kernel_publication_governance_final_index_v1','phase':90,'status':'PUBLICATION_GOVERNANCE_FINAL_INDEX_READY' if not blockers else 'PUBLICATION_GOVERNANCE_FINAL_INDEX_DEGRADED','created_at':now(),'index_policy':'read_only_final_index_closed_no_apply','items':items,'item_count':len(items),'missing':missing,'apply_drift':apply_drift,'read_only':True,'final_index_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase91+ may continue read-only final-index retention; real reopen remains human-gated'}
    idx['index_digest']=digest({k:idx[k] for k in idx if k!='index_digest'})
    return idx
def write_index(idx):
    REPORT_DIR.mkdir(parents=True,exist_ok=True); REPORT_JSON.write_text(json.dumps(idx,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); REPORT_MD.write_text(f"# Workflow Kernel Publication Governance Final Index — Phase 90\n\nStatus: **{idx['status']}**\nItem count: `{idx['item_count']}`\nDigest: `{idx['index_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); idx=build_index(Path.cwd())
    if a.write_report: write_index(idx)
    if a.json: print(json.dumps(idx,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if idx['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
