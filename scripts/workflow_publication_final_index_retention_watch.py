#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-final-index-retention-watch')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_final_index_retention_watch_20260613_phase91.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_final_index_retention_watch_20260613_phase91.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_watch(root:Path)->dict[str,Any]:
    idx_path=root/'docs/private/workflow-kernel-publication-governance-final-index/workflow_kernel_publication_governance_final_index_20260613_phase90.json'
    idx=read(idx_path)
    checks={'final_index_ready':idx.get('status')=='PUBLICATION_GOVERNANCE_FINAL_INDEX_READY','index_read_only':idx.get('read_only') is True,'item_count_eight':idx.get('item_count')==8,'apply_disabled':idx.get('publication_apply_enabled') is False,'external_publication_disabled':idx.get('external_publication_enabled') is False}
    drift=[k for k,v in checks.items() if not v]
    r={'schema':'workflow_kernel_publication_final_index_retention_watch_v1','phase':91,'status':'PUBLICATION_FINAL_INDEX_RETENTION_WATCH_CLEAR' if not drift else 'PUBLICATION_FINAL_INDEX_RETENTION_WATCH_DRIFT','created_at':now(),'checks':checks,'drift':drift,'input_refs':[{'name':'phase90_final_index','path':str(idx_path.relative_to(root)),'sha256':sha(idx_path),'status':idx.get('status')}],'retention_policy':'final_index_retained_read_only_no_apply','read_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase92 may compile read-only terminal summary'}
    r['retention_digest']=digest({k:r[k] for k in r if k!='retention_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True); REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); REPORT_MD.write_text(f"# Workflow Kernel Publication Final Index Retention Watch — Phase 91\n\nStatus: **{r['status']}**\nDrift count: `{len(r['drift'])}`\nDigest: `{r['retention_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); r=build_watch(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
