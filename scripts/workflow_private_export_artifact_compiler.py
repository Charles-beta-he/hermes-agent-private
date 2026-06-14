#!/usr/bin/env python3
"""Phase 75: private export artifact compiler.

Compiles a local/private export bundle manifest from governance archive evidence.
No external publication, no send, no credential access.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-private-export-artifact-compiler')
REPORT_JSON=REPORT_DIR/'workflow_kernel_private_export_artifact_compiler_20260613_phase75.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_private_export_artifact_compiler_20260613_phase75.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_export(root:Path)->dict[str,Any]:
    auth=read(root/'docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json')
    archive_path=root/'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json'
    seq_path=root/'docs/private/workflow-kernel-sequence-map/workflow_kernel_sequence_map_20260613_phase38.json'
    checks={'authorization_packet_ready':auth.get('status')=='PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY','authorization_apply_disabled':auth.get('publication_apply_enabled') is False,'archive_exists':archive_path.exists(),'sequence_exists':seq_path.exists()}
    blockers=[k for k,v in checks.items() if not v]
    files=[{'role':'governance_archive','path':str(archive_path.relative_to(root)),'sha256':sha(archive_path)},{'role':'sequence_map','path':str(seq_path.relative_to(root)),'sha256':sha(seq_path)},{'role':'authorization_packet','path':'docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json','sha256':auth.get('packet_digest')}]
    r={'schema':'workflow_kernel_private_export_artifact_compiler_v1','phase':75,'status':'PRIVATE_EXPORT_ARTIFACT_COMPILER_READY' if not blockers else 'PRIVATE_EXPORT_ARTIFACT_COMPILER_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'export_manifest':{'visibility':'private_local_only','files':files,'redaction_required':True,'public_share_ready':False},'compile_mode':'private_manifest_only','private_export_ready':not blockers,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase76 implementation choice stays safe default unless exact target/payload exists'}
    r['export_digest']=digest({k:r[k] for k in r if k!='export_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Private Export Artifact Compiler — Phase 75','',f"Status: **{r['status']}**",f"Visibility: `{r['export_manifest']['visibility']}`",f"Export digest: `{r['export_digest']}`",'',*[f"- {f['role']}: `{f['path']}` `{f.get('sha256')}`" for f in r['export_manifest']['files']]])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_export(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
