from __future__ import annotations
import importlib.util, json, sys
from dataclasses import dataclass
from pathlib import Path

@dataclass
class FakeTask:
    id:str; path:Path; status:str; fm:dict
class FakeOrchestrator:
    def __init__(self):
        self.tasks=[FakeTask('task1',Path('/tmp/task1.md'),'done',{'workflow_external_side_effect_gate_id':'xgate_test','workflow_adapter_write_id':'adw_test','external_side_effects_enabled':False,'evidence':[]})]
        self.saved=[]; self.refreshed=False
    def iter_tasks(self): return list(self.tasks)
    def save_task(self,t): self.saved.append(t.id)
    def refresh_derived_task_views(self,reason): self.refreshed=True; return {'status':'OK','reason':reason}

def load():
    p=Path(__file__).resolve().parents[2]/'scripts'/'workflow_runtime_external_apply_scaffold.py'
    s=importlib.util.spec_from_file_location('_xapply',p); assert s and s.loader
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

def auth_packet(tmp_path):
    p=tmp_path/'auth.json'; p.write_text(json.dumps({'external_gate_id':'xgate_test','gate_status':'blocked_pending_external_target_authorization','external_side_effects_enabled':False}),encoding='utf-8'); return p

def test_apply_scaffold_write_is_dry_run_only(tmp_path):
    m=load(); o=FakeOrchestrator()
    r=m.execute_apply_scaffold(o,target_adapter='taskpool_outbox_simulator',authorization_packet=auth_packet(tmp_path),task_id='task1',external_gate_id='xgate_test',adapter_write_id='adw_test',output_dir=tmp_path,write=True)
    assert r['apply_status']=='external_apply_scaffold_dry_run_ready'
    assert r['dry_run_receipt']['would_apply'] is False
    assert r['external_side_effects_enabled'] is False
    assert r['readback_verified'] is True
    assert o.tasks[0].fm['workflow_external_apply_status']=='external_apply_scaffold_dry_run_ready'
    assert o.tasks[0].fm['external_side_effects_enabled'] is False
    assert 'proof:external_apply_scaffold_dry_run_no_external_mutation' in o.tasks[0].fm['evidence']

def test_apply_scaffold_rejects_missing_auth(tmp_path):
    m=load(); o=FakeOrchestrator()
    try:
        m.execute_apply_scaffold(o,target_adapter='taskpool_outbox_simulator',authorization_packet=tmp_path/'missing.json',task_id='task1',external_gate_id='xgate_test',adapter_write_id='adw_test',output_dir=tmp_path,write=True)
    except SystemExit as e:
        assert 'authorization-packet' in str(e)
    else: raise AssertionError('expected missing auth rejection')

def test_apply_scaffold_rejects_unallowed_target(tmp_path):
    m=load(); o=FakeOrchestrator()
    try:
        m.execute_apply_scaffold(o,target_adapter='real_lark',authorization_packet=auth_packet(tmp_path),task_id='task1',external_gate_id='xgate_test',adapter_write_id='adw_test',output_dir=tmp_path,write=True)
    except SystemExit as e:
        assert 'not allowed' in str(e)
    else: raise AssertionError('expected target rejection')
