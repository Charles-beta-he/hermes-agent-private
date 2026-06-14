#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-export-review-retention-watch')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_export_review_retention_watch_20260613_phase83.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_export_review_retention_watch_20260613_phase83.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_watch(root:Path)->dict[str,Any]:
    review_path=root/'docs/private/workflow-kernel-publication-export-review-packet/workflow_kernel_publication_export_review_packet_20260613_phase82.json'
    retention_path=root/'docs/private/workflow-kernel-publication-archive-retention-watch/workflow_kernel_publication_archive_retention_watch_20260613_phase81.json'
    review=read(review_path); retention=read(retention_path)
    checks={
      'review_packet_ready':review.get('status')=='PUBLICATION_EXPORT_REVIEW_PACKET_READY',
      'review_packet_only':review.get('packet_only') is True,
      'review_no_export_apply':review.get('export_apply_enabled') is False,
      'review_no_publication_apply':review.get('publication_apply_enabled') is False,
      'archive_retention_clear':retention.get('status')=='PUBLICATION_ARCHIVE_RETENTION_WATCH_CLEAR',
    }
    drift=[k for k,v in checks.items() if not v]
    r={'schema':'workflow_kernel_publication_export_review_retention_watch_v1','phase':83,'status':'PUBLICATION_EXPORT_REVIEW_RETENTION_WATCH_CLEAR' if not drift else 'PUBLICATION_EXPORT_REVIEW_RETENTION_WATCH_DRIFT','created_at':now(),'checks':checks,'drift':drift,'input_refs':[{'name':'phase82_review_packet','path':str(review_path.relative_to(root)),'sha256':sha(review_path),'status':review.get('status')},{'name':'phase81_archive_retention','path':str(retention_path.relative_to(root)),'sha256':sha(retention_path),'status':retention.get('status')}],'retention_policy':'export_review_packet_retained_packet_only_no_apply','read_only':True,'packet_only':True,'export_apply_enabled':False,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase84 may compile final boundary packet only'}
    r['retention_digest']=digest({k:r[k] for k in r if k!='retention_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text(f"# Workflow Kernel Publication Export Review Retention Watch — Phase 83\n\nStatus: **{r['status']}**\nDrift count: `{len(r['drift'])}`\nPacket only: `{r['packet_only']}`\nDigest: `{r['retention_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_watch(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
