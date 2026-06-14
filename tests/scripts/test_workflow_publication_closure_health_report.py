from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_closure_health_report.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json',{'status':'PUBLICATION_EXACT_TARGET_GATE_READY','publication_apply_enabled':False,'external_publication_enabled':False})
    write(root,'docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json',{'status':'PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY','publication_apply_enabled':False,'external_publication_enabled':False})
    write(root,'docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json',{'status':'PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_RECORDED','choice':'A_KEEP_PRIVATE_NO_APPLY','publication_apply_enabled':apply,'external_publication_enabled':False})
    write(root,'docs/private/workflow-kernel-publication-apply-reopen-packet/workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json',{'status':'PUBLICATION_APPLY_REOPEN_PACKET_READY','publication_apply_enabled':False,'external_publication_enabled':False})
def test_health_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_report(tmp_path)
    assert r['status']=='PUBLICATION_CLOSURE_HEALTH_REPORT_CLEAR'
    assert r['health']=='clear'
    assert r['unhealthy_checks']==[]
    assert r['publication_apply_enabled'] is False
def test_health_detects_apply_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_report(tmp_path)
    assert r['status']=='PUBLICATION_CLOSURE_HEALTH_REPORT_DEGRADED'
    assert 'all_apply_disabled' in r['unhealthy_checks']
