from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_periodic_closure_health_report.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, drift=None):
    write(root,'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json',{'status':'MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY','read_only':True})
    write(root,'docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json',{'status':'MANUAL_ONLY_MONITORING_CLEAR','drift':drift or []})
    write(root,'docs/private/workflow-kernel-single-point-reopen-choice/workflow_kernel_single_point_reopen_choice_20260613_phase67.json',{'status':'SINGLE_POINT_REOPEN_CHOICE_RECORDED','choice':'A_NO_REOPEN_KEEP_MANUAL_ONLY'})
    write(root,'docs/private/workflow-kernel-sequence-map/workflow_kernel_sequence_map_20260613_phase38.json',{'status':'WORKFLOW_SEQUENCE_MAP_READY','phase_count':52})
def test_health_report_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_report(tmp_path)
    assert r['status']=='PERIODIC_CLOSURE_HEALTH_REPORT_CLEAR'
    assert r['stage_report_only'] is True
    assert r['read_only'] is True
    assert r['message_sent'] is False
def test_health_report_degraded_on_drift(tmp_path):
    m=load(); seed(tmp_path, ['unexpected_unattended_enabled']); r=m.build_report(tmp_path)
    assert r['status']=='PERIODIC_CLOSURE_HEALTH_REPORT_DEGRADED'
    assert 'monitoring_no_drift' in r['blockers']
