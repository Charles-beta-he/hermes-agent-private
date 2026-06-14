from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_single_point_reopen_choice.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root):
    write(root,'docs/private/workflow-kernel-future-unattended-reopen-packet/workflow_kernel_future_unattended_reopen_packet_20260613_phase66.json',{'status':'FUTURE_UNATTENDED_REOPEN_PACKET_READY','packet_digest':'pd','reopen_apply_enabled':False,'unattended_dispatch_enabled':False,'reopen_options':[{'option':'A_NO_REOPEN_KEEP_MANUAL_ONLY'},{'option':'B_REOPEN_DRY_RUN_ONLY_AUTOMATION_DESIGN'}]})
def test_safe_choice_recorded(tmp_path):
    m=load(); seed(tmp_path); r=m.build_choice(tmp_path)
    assert r['status']=='SINGLE_POINT_REOPEN_CHOICE_RECORDED'
    assert r['choice']=='A_NO_REOPEN_KEEP_MANUAL_ONLY'
    assert r['record_only'] is True
    assert r['reopen_apply_enabled'] is False
    assert r['unattended_dispatch_enabled'] is False
    assert r['message_sent'] is False
def test_invalid_choice_blocks(tmp_path):
    m=load(); seed(tmp_path); r=m.build_choice(tmp_path,'C_UNKNOWN')
    assert r['status']=='SINGLE_POINT_REOPEN_CHOICE_BLOCKED'
    assert 'choice_allowed' in r['blockers']
