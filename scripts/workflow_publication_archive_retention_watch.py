#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-archive-retention-watch')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_archive_retention_watch_20260613_phase81.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_archive_retention_watch_20260613_phase81.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def build_watch(root:Path)->dict[str,Any]:
    archive_path=root/'docs/private/workflow-kernel-publication-governance-archive/workflow_kernel_publication_governance_archive_20260613_phase80.json'
    health_path=root/'docs/private/workflow-kernel-publication-closure-health-report/workflow_kernel_publication_closure_health_report_20260613_phase79.json'
    archive=read(archive_path); health=read(health_path)
    checks={'archive_ready':archive.get('status')=='PUBLICATION_GOVERNANCE_ARCHIVE_READY','archive_read_only':archive.get('read_only') is True,'archive_no_apply':archive.get('publication_apply_enabled') is False,'health_clear':health.get('status')=='PUBLICATION_CLOSURE_HEALTH_REPORT_CLEAR','health_no_apply':health.get('publication_apply_enabled') is False}
    drift=[k for k,v in checks.items() if not v]
    r={'schema':'workflow_kernel_publication_archive_retention_watch_v1','phase':81,'status':'PUBLICATION_ARCHIVE_RETENTION_WATCH_CLEAR' if not drift else 'PUBLICATION_ARCHIVE_RETENTION_WATCH_DRIFT','created_at':now(),'checks':checks,'drift':drift,'input_refs':[{'name':'phase80_archive','path':str(archive_path.relative_to(root)),'sha256':sha(archive_path),'status':archive.get('status')},{'name':'phase79_health','path':str(health_path.relative_to(root)),'sha256':sha(health_path),'status':health.get('status')}],'retention_policy':'publication_archive_retained_read_only_no_apply','read_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase82 may compile publication export review packet only'}
    r['retention_digest']=digest({k:r[k] for k in r if k!='retention_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True); REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); REPORT_MD.write_text(f"# Workflow Kernel Publication Archive Retention Watch — Phase 81\n\nStatus: **{r['status']}**\nDrift count: `{len(r['drift'])}`\nRead only: `{r['read_only']}`\nDigest: `{r['retention_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); r=build_watch(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
