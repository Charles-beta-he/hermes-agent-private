#!/usr/bin/env python3
"""Phase 71: private archive retention watch.

Read-only retention/drift watch over the private Workflow Kernel governance
archive and publication packet. No publication, no external send, no cron
mutation, no credential access.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-private-archive-retention-watch')
REPORT_JSON=REPORT_DIR/'workflow_kernel_private_archive_retention_watch_20260613_phase71.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_private_archive_retention_watch_20260613_phase71.md'
INPUTS={
 'archive':'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json',
 'health':'docs/private/workflow-kernel-periodic-closure-health-report/workflow_kernel_periodic_closure_health_report_20260613_phase69.json',
 'publication_packet':'docs/private/workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json',
}
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_watch(root:Path)->dict[str,Any]:
    docs={k:read(root/v) for k,v in INPUTS.items()}
    refs=[{'name':k,'path':v,'sha256':sha(root/v),'status':docs[k].get('status')} for k,v in INPUTS.items()]
    checks={
      'archive_ready':docs['archive'].get('status')=='MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY',
      'archive_read_only':docs['archive'].get('read_only') is True,
      'health_clear':docs['health'].get('status')=='PERIODIC_CLOSURE_HEALTH_REPORT_CLEAR',
      'publication_packet_ready':docs['publication_packet'].get('status')=='ARCHIVE_PUBLICATION_PACKET_READY',
      'publication_apply_disabled':docs['publication_packet'].get('publication_apply_enabled') is False,
      'default_private':docs['publication_packet'].get('recommended_default')=='A_KEEP_PRIVATE_NO_PUBLICATION',
    }
    drift=[k for k,v in checks.items() if not v]
    r={'schema':'workflow_kernel_private_archive_retention_watch_v1','phase':71,'status':'PRIVATE_ARCHIVE_RETENTION_WATCH_CLEAR' if not drift else 'PRIVATE_ARCHIVE_RETENTION_WATCH_DRIFT','created_at':now(),'input_refs':refs,'checks':checks,'drift':drift,'retention_policy':'private_archive_retained_no_publication','read_only':True,'private_archive_retained':not drift,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase72 exact-target publication gate remains disabled unless exact target and payload are authorized'}
    r['retention_digest']=digest({k:r[k] for k in r if k!='retention_digest'})
    return r
def write_watch(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    rows=[f"- {x['name']}: `{x.get('status')}` `{x.get('sha256')}`" for x in r['input_refs']]
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Private Archive Retention Watch — Phase 71','',f"Status: **{r['status']}**",f"Policy: `{r['retention_policy']}`",f"Digest: `{r['retention_digest']}`",'',*rows])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_watch(Path.cwd())
    if a.write_report: write_watch(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
