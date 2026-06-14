from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_exact_target_apply_dry_run_compiler.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, gate_status='PUBLICATION_EXACT_TARGET_GATE_READY'):
    write(root,'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json',{'status':gate_status,'gate_only':True,'publication_apply_enabled':False,'gate_digest':'abc'})
    write(root,'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json',{'status':'MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY','read_only':True})
def test_compiler_ready_is_preview_only(tmp_path):
    m=load(); seed(tmp_path); r=m.build_preview(tmp_path)
    assert r['status']=='EXACT_TARGET_APPLY_DRY_RUN_COMPILER_READY'
    assert r['dry_run_only'] is True
    assert r['dry_run_preview']['would_publish'] is False
    assert r['publication_apply_enabled'] is False
    assert r['external_side_effects_executed'] is False
def test_compiler_blocks_if_gate_not_ready(tmp_path):
    m=load(); seed(tmp_path,'PUBLICATION_EXACT_TARGET_GATE_BLOCKED'); r=m.build_preview(tmp_path)
    assert r['status']=='EXACT_TARGET_APPLY_DRY_RUN_COMPILER_BLOCKED'
    assert 'gate_ready' in r['blockers']
