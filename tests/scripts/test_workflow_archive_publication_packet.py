from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_archive_publication_packet.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, health='PERIODIC_CLOSURE_HEALTH_REPORT_CLEAR'):
    write(root,'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json',{'status':'MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY'})
    write(root,'docs/private/workflow-kernel-periodic-closure-health-report/workflow_kernel_periodic_closure_health_report_20260613_phase69.json',{'status':health,'stage_report_only':True})
def test_packet_ready_private_default(tmp_path):
    m=load(); seed(tmp_path); p=m.build_packet(tmp_path)
    assert p['status']=='ARCHIVE_PUBLICATION_PACKET_READY'
    assert p['recommended_default']=='A_KEEP_PRIVATE_NO_PUBLICATION'
    assert p['packet_only'] is True
    assert p['publication_apply_enabled'] is False
    assert p['external_side_effects_executed'] is False
def test_packet_blocks_without_clear_health(tmp_path):
    m=load(); seed(tmp_path,'PERIODIC_CLOSURE_HEALTH_REPORT_DEGRADED'); p=m.build_packet(tmp_path)
    assert p['status']=='ARCHIVE_PUBLICATION_PACKET_BLOCKED'
    assert 'health_clear' in p['blockers']
