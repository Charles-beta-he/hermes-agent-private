from __future__ import annotations
import importlib.util, sys
from dataclasses import dataclass
from pathlib import Path

@dataclass
class FakeTask:
    id: str; path: Path; status: str; fm: dict
class FakeOrchestrator:
    def __init__(self):
        self.tasks=[FakeTask('task1',Path('/tmp/task1.md'),'done',{'workflow_external_apply_contract_id':'xcontract_test','workflow_external_apply_contract_status':'real_apply_contracts_defined_no_target_selected','external_side_effects_enabled':False,'workflow_external_apply_contract_record':{'target_adapters':{'taskpool_outbox_simulator':{'kind':'local_simulator'}}},'evidence':[]})]
        self.saved=[]
    def iter_tasks(self): return list(self.tasks)
    def save_task(self,t): self.saved.append(t.id)
    def refresh_derived_task_views(self,reason): return {'status':'OK','reason':reason}

def load():
    p=Path(__file__).resolve().parents[2]/'scripts'/'workflow_runtime_external_apply_local.py'
    s=importlib.util.spec_from_file_location('_xlocal',p); assert s and s.loader
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

def test_local_apply_records_outbox_receipt_and_rollback_without_external_effects(tmp_path):
    m=load(); o=FakeOrchestrator()
    r=m.execute_local_apply(o,task_id='task1',contract_id='xcontract_test',target_adapter='taskpool_outbox_simulator',output_dir=tmp_path,write=True)
    assert r['apply_status']=='local_outbox_simulator_apply_recorded'
    assert r['apply_mode']=='local_real_apply_no_external_dispatch'
    assert r['external_side_effects_enabled'] is False
    assert r['readback_verified'] is True
    assert o.tasks[0].fm['workflow_external_local_apply_status']=='local_outbox_simulator_apply_recorded'
    assert o.tasks[0].fm['workflow_external_apply_selected_target']=='taskpool_outbox_simulator'
    assert o.tasks[0].fm['external_side_effects_enabled'] is False
    assert Path(r['outbox_item']['path']).exists()
    assert Path(r['receipt']['path']).exists()
    assert Path(r['rollback_evidence']['path']).exists()

def test_local_apply_rejects_external_target(tmp_path):
    m=load(); o=FakeOrchestrator()
    try:
        m.execute_local_apply(o,task_id='task1',contract_id='xcontract_test',target_adapter='telegram_notification',output_dir=tmp_path,write=True)
    except SystemExit as e:
        assert 'taskpool_outbox_simulator' in str(e)
    else: raise AssertionError('expected target rejection')

def test_local_apply_rejects_external_side_effects_enabled(tmp_path):
    m=load(); o=FakeOrchestrator(); o.tasks[0].fm['external_side_effects_enabled']=True
    try:
        m.execute_local_apply(o,task_id='task1',contract_id='xcontract_test',target_adapter='taskpool_outbox_simulator',output_dir=tmp_path,write=True)
    except SystemExit as e:
        assert 'external_side_effects_enabled' in str(e)
    else: raise AssertionError('expected side-effect rejection')
