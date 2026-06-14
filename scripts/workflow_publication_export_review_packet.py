#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-export-review-packet')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_export_review_packet_20260613_phase82.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_export_review_packet_20260613_phase82.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_packet(root:Path)->dict[str,Any]:
    watch=read(root/'docs/private/workflow-kernel-publication-archive-retention-watch/workflow_kernel_publication_archive_retention_watch_20260613_phase81.json')
    checks={'retention_clear':watch.get('status')=='PUBLICATION_ARCHIVE_RETENTION_WATCH_CLEAR','retention_read_only':watch.get('read_only') is True,'retention_no_apply':watch.get('publication_apply_enabled') is False}
    blockers=[k for k,v in checks.items() if not v]
    p={'schema':'workflow_kernel_publication_export_review_packet_v1','phase':82,'status':'PUBLICATION_EXPORT_REVIEW_PACKET_READY' if not blockers else 'PUBLICATION_EXPORT_REVIEW_PACKET_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'recommended_default':'A_REVIEW_PRIVATE_ARCHIVE_ONLY','selected_option':None,'packet_only':True,'review_only':True,'export_apply_enabled':False,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase83 may update review retention watch; real export/publication requires exact target/payload'}
    p['packet_digest']=digest({k:p[k] for k in p if k!='packet_digest'})
    return p
def write_packet(p):
    REPORT_DIR.mkdir(parents=True,exist_ok=True); REPORT_JSON.write_text(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); REPORT_MD.write_text(f"# Workflow Kernel Publication Export Review Packet — Phase 82\n\nStatus: **{p['status']}**\nRecommended default: `{p['recommended_default']}`\nPacket only: `{p['packet_only']}`\nDigest: `{p['packet_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); p=build_packet(Path.cwd())
    if a.write_report: write_packet(p)
    if a.json: print(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if p['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
