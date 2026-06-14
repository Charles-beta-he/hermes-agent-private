from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_manual_only_governance_archive.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root):
    files={57:('docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json',{'status':'DRY_RUN_CRON_ROLLOUT_APPLIED'}),60:('docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json',{'status':'ONE_SHOT_GUARDED_SEND_VERIFIED'}),64:('docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json',{'status':'MANUAL_ONLY_CLOSURE_READY'}),65:('docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json',{'status':'MANUAL_ONLY_MONITORING_CLEAR'}),66:('docs/private/workflow-kernel-future-unattended-reopen-packet/workflow_kernel_future_unattended_reopen_packet_20260613_phase66.json',{'status':'FUTURE_UNATTENDED_REOPEN_PACKET_READY','reopen_apply_enabled':False}),67:('docs/private/workflow-kernel-single-point-reopen-choice/workflow_kernel_single_point_reopen_choice_20260613_phase67.json',{'status':'SINGLE_POINT_REOPEN_CHOICE_RECORDED','choice':'A_NO_REOPEN_KEEP_MANUAL_ONLY'})}
    for rel,payload in files.values(): write(root,rel,payload)
def test_archive_ready(tmp_path):
    m=load(); seed(tmp_path); a=m.build_archive(tmp_path)
    assert a['status']=='MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY'
    assert a['read_only'] is True
    assert a['unattended_dispatch_enabled'] is False
    assert len(a['items'])==6
def test_archive_blocks_on_reopen_choice(tmp_path):
    m=load(); seed(tmp_path); write(tmp_path,'docs/private/workflow-kernel-single-point-reopen-choice/workflow_kernel_single_point_reopen_choice_20260613_phase67.json',{'choice':'B_REOPEN_DRY_RUN_ONLY_AUTOMATION_DESIGN'})
    a=m.build_archive(tmp_path)
    assert a['status']=='MANUAL_ONLY_GOVERNANCE_ARCHIVE_BLOCKED'
    assert 'phase67_keep_closed' in a['blockers']
