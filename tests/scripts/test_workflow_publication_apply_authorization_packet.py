from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_apply_authorization_packet.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, compiler='EXACT_TARGET_APPLY_DRY_RUN_COMPILER_READY'):
    write(root,'docs/private/workflow-kernel-exact-target-apply-dry-run-compiler/workflow_kernel_exact_target_apply_dry_run_compiler_20260613_phase73.json',{'status':compiler,'dry_run_only':True})
    write(root,'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json',{'status':'PUBLICATION_EXACT_TARGET_GATE_READY','publication_apply_enabled':False})
def test_packet_ready_fail_closed(tmp_path):
    m=load(); seed(tmp_path); p=m.build_packet(tmp_path)
    assert p['status']=='PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY'
    assert p['recommended_default']=='A_KEEP_PRIVATE_NO_PUBLICATION'
    assert p['selected_option'] is None
    assert p['publication_apply_enabled'] is False
    assert p['packet_only'] is True
def test_packet_blocks_without_compiler(tmp_path):
    m=load(); seed(tmp_path,'EXACT_TARGET_APPLY_DRY_RUN_COMPILER_BLOCKED'); p=m.build_packet(tmp_path)
    assert p['status']=='PUBLICATION_APPLY_AUTHORIZATION_PACKET_BLOCKED'
    assert 'compiler_ready' in p['blockers']
