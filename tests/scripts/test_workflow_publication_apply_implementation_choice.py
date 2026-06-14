from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_apply_implementation_choice.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root):
    write(root,'docs/private/workflow-kernel-private-export-artifact-compiler/workflow_kernel_private_export_artifact_compiler_20260613_phase75.json',{'status':'PRIVATE_EXPORT_ARTIFACT_COMPILER_READY'})
    write(root,'docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json',{'status':'PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY','selected_option':None})
def test_choice_records_safe_default(tmp_path):
    m=load(); seed(tmp_path); r=m.build_choice(tmp_path)
    assert r['status']=='PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_RECORDED'
    assert r['choice']=='A_KEEP_PRIVATE_NO_APPLY'
    assert r['record_only'] is True
    assert r['publication_apply_enabled'] is False
def test_choice_blocks_exact_apply_without_target(tmp_path):
    m=load(); seed(tmp_path); r=m.build_choice(tmp_path,'C_OPEN_EXACT_TARGET_PUBLICATION_APPLY')
    assert r['status']=='PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_BLOCKED'
    assert 'exact_target_payload_absent' in r['blockers']
