#!/usr/bin/env python3
"""Phase 80: publication governance archive update.

Read-only index update over publication governance artifacts.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-governance-archive')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_governance_archive_20260613_phase80.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_governance_archive_20260613_phase80.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_archive(root:Path)->dict[str,Any]:
    entries=[
      ('phase70_archive_publication_packet','docs/private/workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json'),
      ('phase72_exact_target_gate','docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json'),
      ('phase74_authorization_packet','docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json'),
      ('phase76_implementation_choice','docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json'),
      ('phase78_reopen_packet','docs/private/workflow-kernel-publication-apply-reopen-packet/workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json'),
      ('phase79_health_report','docs/private/workflow-kernel-publication-closure-health-report/workflow_kernel_publication_closure_health_report_20260613_phase79.json'),
    ]
    items=[]; missing=[]; apply_drift=[]
    for name, rel in entries:
        p=root/rel; data=read(p)
        if not p.exists(): missing.append(name)
        if data.get('publication_apply_enabled') not in (False, None): apply_drift.append(name)
        items.append({'name':name,'path':rel,'sha256':sha(p),'status':data.get('status'),'publication_apply_enabled':data.get('publication_apply_enabled')})
    blockers=missing+apply_drift
    a={'schema':'workflow_kernel_publication_governance_archive_v1','phase':80,'status':'PUBLICATION_GOVERNANCE_ARCHIVE_READY' if not blockers else 'PUBLICATION_GOVERNANCE_ARCHIVE_DEGRADED','created_at':now(),'archive_policy':'read_only_publication_governance_index','items':items,'missing':missing,'apply_drift':apply_drift,'item_count':len(items),'read_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase81 may run archive retention watch; Phase82 may compile publication export review packet'}
    a['archive_digest']=digest({k:a[k] for k in a if k!='archive_digest'})
    return a
def write_archive(a):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(a,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Publication Governance Archive — Phase 80','',f"Status: **{a['status']}**",f"Item count: `{a['item_count']}`",f"Read only: `{a['read_only']}`",f"Digest: `{a['archive_digest']}`"])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_archive(Path.cwd())
    if a.write_report: write_archive(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
