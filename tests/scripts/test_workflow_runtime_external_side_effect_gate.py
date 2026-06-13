from __future__ import annotations
import importlib.util, sys
from dataclasses import dataclass
from pathlib import Path

@dataclass
class FakeTask:
    id:str; path:Path; status:str; fm:dict

class FakeOrchestrator:
    def __init__(self):
        self.tasks=[FakeTask('task1',Path('/tmp/task1.md'),'done',{
            'status':'done','workflow_adapter_write_id':'adw_test','workflow_model_call_id':'mcall_test','workflow_consolidation_group_id':'contract_acceptance_governance','workflow_adapter_write_status':'adapter_write_local_outbox_simulated','external_side_effects_enabled':False,'evidence':[]
        })]
        self.saved=[]; self.refreshed=False
    def iter_tasks(self): return list(self.tasks)
    def save_task(self,t): self.saved.append(t.id)
    def refresh_derived_task_views(self,reason): self.refreshed=True; return {'status':'OK','reason':reason}

def load():
    p=Path(__file__).resolve().parents[2]/'scripts'/'workflow_runtime_external_side_effect_gate.py'
    s=importlib.util.spec_from_file_location('_xgate',p)
    assert s and s.loader
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

def test_external_gate_write_records_packet_without_enabling_external(tmp_path):
    m=load(); o=FakeOrchestrator()
    r=m.execute_gate(o,adapter_write_id='adw_test',model_call_id='mcall_test',output_dir=tmp_path,write=True)
    assert r['gate_status']=='blocked_pending_external_target_authorization'
    assert r['external_side_effects_enabled'] is False
    assert r['readback_verified'] is True
    assert Path(r['packet_artifact']['path']).exists()
    t=o.tasks[0]
    assert t.fm['workflow_external_side_effect_gate_status']=='blocked_pending_external_target_authorization'
    assert t.fm['external_side_effects_enabled'] is False
    assert 'proof:external_side_effect_gate_no_external_mutation' in t.fm['evidence']
    assert o.refreshed is True

def test_external_gate_preview_does_not_write_task(tmp_path):
    m=load(); o=FakeOrchestrator()
    r=m.execute_gate(o,adapter_write_id='adw_test',model_call_id='mcall_test',output_dir=tmp_path,write=False)
    assert r['readback_verified'] is False
    assert o.saved==[]
    assert 'workflow_external_side_effect_gate_status' not in o.tasks[0].fm

def test_external_gate_rejects_already_enabled_external(tmp_path):
    m=load(); o=FakeOrchestrator(); o.tasks[0].fm['external_side_effects_enabled']=True
    try:
        m.execute_gate(o,adapter_write_id='adw_test',model_call_id='mcall_test',output_dir=tmp_path,write=True)
    except SystemExit as e:
        assert 'external side effects already enabled' in str(e)
    else:
        raise AssertionError('expected rejection')
