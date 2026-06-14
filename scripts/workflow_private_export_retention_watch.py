#!/usr/bin/env python3
"""Phase 77: private export retention watch.

Read-only drift watch over the private export artifact and no-apply choice.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-private-export-retention-watch')
REPORT_JSON=REPORT_DIR/'workflow_kernel_private_export_retention_watch_20260613_phase77.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_private_export_retention_watch_20260613_phase77.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_watch(root:Path)->dict[str,Any]:
    export_path=root/'docs/private/workflow-kernel-private-export-artifact-compiler/workflow_kernel_private_export_artifact_compiler_20260613_phase75.json'
    choice_path=root/'docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json'
    export=read(export_path); choice=read(choice_path)
    checks={'export_ready':export.get('status')=='PRIVATE_EXPORT_ARTIFACT_COMPILER_READY','export_private':(export.get('export_manifest') or {}).get('visibility')=='private_local_only','export_no_apply':export.get('publication_apply_enabled') is False,'choice_recorded':choice.get('status')=='PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_RECORDED','choice_keep_private':choice.get('choice')=='A_KEEP_PRIVATE_NO_APPLY','choice_no_apply':choice.get('publication_apply_enabled') is False}
    drift=[k for k,v in checks.items() if not v]
    refs=[{'name':'phase75_export','path':str(export_path.relative_to(root)),'sha256':sha(export_path),'status':export.get('status')},{'name':'phase76_choice','path':str(choice_path.relative_to(root)),'sha256':sha(choice_path),'status':choice.get('status')}]
    r={'schema':'workflow_kernel_private_export_retention_watch_v1','phase':77,'status':'PRIVATE_EXPORT_RETENTION_WATCH_CLEAR' if not drift else 'PRIVATE_EXPORT_RETENTION_WATCH_DRIFT','created_at':now(),'input_refs':refs,'checks':checks,'drift':drift,'read_only':True,'retention_policy':'private_export_retained_no_publication','publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase78 publication apply reopen packet remains packet-only'}
    r['retention_digest']=digest({k:r[k] for k in r if k!='retention_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Private Export Retention Watch — Phase 77','',f"Status: **{r['status']}**",f"Drift count: `{len(r['drift'])}`",f"Read only: `{r['read_only']}`",f"Digest: `{r['retention_digest']}`"])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_watch(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
