from __future__ import annotations
import importlib.util, json, sys
from dataclasses import dataclass
from pathlib import Path

@dataclass
class FakeTask:
    id: str; path: Path; status: str; fm: dict
class FakeOrchestrator:
    def __init__(self):
        self.tasks=[FakeTask('task1',Path('/tmp/task1.md'),'done',{'workflow_external_apply_id':'xapply_test','workflow_external_apply_status':'external_apply_scaffold_dry_run_ready','external_side_effects_enabled':False,'evidence':[]})]
        self.saved=[]
    def iter_tasks(self): return list(self.tasks)
    def save_task(self,t): self.saved.append(t.id)
    def refresh_derived_task_views(self,reason): return {'status':'OK','reason':reason}

def load():
    p=Path(__file__).resolve().parents[2]/'scripts'/'workflow_runtime_external_apply_contract.py'
    s=importlib.util.spec_from_file_location('_xcontract',p); assert s and s.loader
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

def write_support_files(tmp_path):
    reg=tmp_path/'registry.json'; reg.write_text(json.dumps({'version':'test','adapters':{'taskpool_outbox_simulator':{'status':'implemented_dry_run_only','required_authority':'local_repo_write'}}}),encoding='utf-8')
    receipt=tmp_path/'receipt.schema.json'; receipt.write_text(json.dumps({'title':'Hermes Workflow Runtime External Apply Receipt'}),encoding='utf-8')
    rollback=tmp_path/'rollback.schema.json'; rollback.write_text(json.dumps({'title':'Hermes Workflow Runtime External Apply Rollback Evidence'}),encoding='utf-8')
    return reg, receipt, rollback

def test_contract_enumerates_targets_without_enabling_external_effects(tmp_path):
    m=load(); o=FakeOrchestrator(); reg,receipt,rollback=write_support_files(tmp_path)
    r=m.build_contract(o,task_id='task1',apply_id='xapply_test',registry_path=reg,receipt_schema=receipt,rollback_schema=rollback,output_dir=tmp_path,write=True)
    assert r['selection_status']=='target_adapter_options_fully_enumerated_pending_selection'
    assert r['real_apply_enabled'] is False
    assert r['external_side_effects_enabled'] is False
    assert r['readback_verified'] is True
    assert o.tasks[0].fm['workflow_external_apply_contract_status']=='real_apply_contracts_defined_no_target_selected'
    assert o.tasks[0].fm['external_side_effects_enabled'] is False

def test_contract_rejects_missing_adapter_registry(tmp_path):
    m=load(); o=FakeOrchestrator(); reg,receipt,rollback=write_support_files(tmp_path)
    reg.write_text(json.dumps({'version':'test','adapters':{}}),encoding='utf-8')
    try:
        m.build_contract(o,task_id='task1',apply_id='xapply_test',registry_path=reg,receipt_schema=receipt,rollback_schema=rollback,output_dir=tmp_path,write=True)
    except SystemExit as e:
        assert 'no adapters' in str(e)
    else: raise AssertionError('expected registry rejection')

def test_contract_rejects_task_with_external_side_effects_enabled(tmp_path):
    m=load(); o=FakeOrchestrator(); o.tasks[0].fm['external_side_effects_enabled']=True; reg,receipt,rollback=write_support_files(tmp_path)
    try:
        m.build_contract(o,task_id='task1',apply_id='xapply_test',registry_path=reg,receipt_schema=receipt,rollback_schema=rollback,output_dir=tmp_path,write=True)
    except SystemExit as e:
        assert 'external_side_effects_enabled' in str(e)
    else: raise AssertionError('expected side-effect rejection')
