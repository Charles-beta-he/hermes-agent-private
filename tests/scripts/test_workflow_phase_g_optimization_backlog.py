from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'scripts/workflow_phase_g_optimization_backlog.py'
def load():
    spec=importlib.util.spec_from_file_location('workflow_phase_g_optimization_backlog', SCRIPT); mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod); return mod

def test_phase_g_backlog_is_read_only_candidate_projection():
    m=load(); r=m.build_report()
    assert r['status'] == 'PHASE_G_OPTIMIZATION_BACKLOG_READY'
    assert r['read_only'] is True
    assert r['taskpool_mutated'] is False
    assert r['mission_mutated'] is False
    assert r['executor_enabled'] is False
    assert r['external_side_effects_executed'] is False
    assert r['candidate_count'] >= 4
    assert {'self_healing_candidate','completion_evidence_backlog','optimization_backlog'} <= set(r['candidate_classes'])

def test_phase_g_backlog_artifacts(tmp_path):
    m=load(); r=m.build_report(); out=m.write_report(r,tmp_path)
    assert Path(out['json']).exists(); assert Path(out['markdown']).exists()
    assert r['checks']['candidate_only'] is True
    assert r['checks']['no_taskpool_enqueue'] is True
