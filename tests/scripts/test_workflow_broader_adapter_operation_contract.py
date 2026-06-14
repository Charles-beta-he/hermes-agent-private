from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/workflow_broader_adapter_operation_contract.py'


def load():
    spec = importlib.util.spec_from_file_location('workflow_broader_adapter_operation_contract', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_broader_adapter_contract_is_fail_closed():
    mod = load()
    report = mod.build_report()
    assert report['status'] == 'BROADER_ADAPTER_OPERATION_CONTRACT_READY'
    assert report['global_invariants']['external_side_effects_enabled'] is False
    assert report['global_invariants']['unattended_dispatch_enabled'] is False
    assert report['global_invariants']['publication_apply_enabled'] is False
    assert report['global_invariants']['no_generic_adapter_write_through'] is True
    assert report['safe_default_next'] == 'keep_current_narrow_taskpool_kanban_only'
    assert {i['adapter'] for i in report['current_supported_operations']} == {'taskpool_note', 'kanban_db'}


def test_candidate_operations_require_exact_future_gates(tmp_path):
    mod = load()
    report = mod.build_report()
    assert report['contract_digest']
    for item in report['candidate_operations_locked']:
        assert 'exact' in item['required_gate'] or item['adapter'] == 'generic_workflow'
    artifacts = mod.write_report(report, tmp_path)
    assert Path(artifacts['json']).exists()
    assert Path(artifacts['markdown']).exists()
