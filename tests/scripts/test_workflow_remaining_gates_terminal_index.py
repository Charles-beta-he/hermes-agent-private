from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'scripts/workflow_remaining_gates_terminal_index.py'
def load():
    spec=importlib.util.spec_from_file_location('workflow_remaining_gates_terminal_index', SCRIPT); mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod); return mod

def test_remaining_gates_terminal_index_is_fail_closed():
    m=load(); r=m.build_report()
    assert r['status'] in {'REMAINING_GATES_TERMINAL_INDEX_READY','REMAINING_GATES_TERMINAL_INDEX_DEGRADED'}
    assert r['read_only'] is True
    assert r['external_side_effects_executed'] is False
    assert r['unattended_dispatch_enabled'] is False
    assert r['publication_apply_enabled'] is False
    assert set(r['terminal_gates']) == {'unattended_dispatch','publication_apply','broader_adapter_operations'}

def test_remaining_gates_ready_when_current_artifacts_are_present(tmp_path):
    m=load(); r=m.build_report()
    assert r['missing_artifacts'] == []
    assert all(r['checks'].values())
    assert r['status'] == 'REMAINING_GATES_TERMINAL_INDEX_READY'
    out=m.write_report(r,tmp_path)
    assert Path(out['json']).exists(); assert Path(out['markdown']).exists()
