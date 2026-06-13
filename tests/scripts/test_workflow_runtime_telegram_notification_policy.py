from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path

def load():
    p=Path(__file__).resolve().parents[2]/'scripts'/'workflow_runtime_telegram_notification_policy.py'
    s=importlib.util.spec_from_file_location('_tgpolicy',p); assert s and s.loader
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

def policy(tmp_path):
    p=tmp_path/'policy.json'
    p.write_text(json.dumps({'version':'test','adapter':'telegram_notification','default_mode':'dry_run_preview','unattended_send_enabled':False,'allowlist':[{'target':'telegram','chat_id':'1','label':'home','allowed_purposes':['system_test']}],'rate_limit':{'window_seconds':3600,'max_sends_per_window':1,'scope':'target_chat'},'idempotency':{'key_fields':['target','chat_id','purpose','message_digest'],'duplicate_policy':'block_duplicate_send_and_return_existing_receipt'},'dry_run':{},'receipt':{},'rollback':{}}),encoding='utf-8')
    return p

def test_dry_run_compiles_send_instruction_without_sending(tmp_path):
    m=load(); p=policy(tmp_path)
    r=m.compile_request(policy_path=p,ledger_path=tmp_path/'ledger.json',output_dir=tmp_path,chat_id='1',purpose='system_test',message='hello',write=True)
    assert r['status']=='dry_run_ready'
    assert r['send_instruction']['tool']=='send_message'
    assert r['unattended_send_enabled'] is False
    ledger=json.loads((tmp_path/'ledger.json').read_text())
    assert ledger['entries'][0]['preview_only'] is True

def test_duplicate_dry_run_is_blocked(tmp_path):
    m=load(); p=policy(tmp_path); ledger=tmp_path/'ledger.json'
    m.compile_request(policy_path=p,ledger_path=ledger,output_dir=tmp_path,chat_id='1',purpose='system_test',message='hello',write=True)
    r=m.compile_request(policy_path=p,ledger_path=ledger,output_dir=tmp_path,chat_id='1',purpose='system_test',message='hello',write=False)
    assert r['status']=='blocked_duplicate_existing_request'
    assert r['idempotency']['duplicate'] is True

def test_rejects_unallowlisted_target(tmp_path):
    m=load(); p=policy(tmp_path)
    try:
        m.compile_request(policy_path=p,ledger_path=tmp_path/'ledger.json',output_dir=tmp_path,chat_id='2',purpose='system_test',message='hello')
    except SystemExit as e:
        assert 'not allowed' in str(e)
    else: raise AssertionError('expected allowlist rejection')

def test_record_send_result_validates_chat_and_records_receipt(tmp_path):
    m=load(); p=policy(tmp_path); send=tmp_path/'send.json'
    send.write_text(json.dumps({'success':True,'platform':'telegram','chat_id':'1','message_id':'9'}),encoding='utf-8')
    r=m.compile_request(policy_path=p,ledger_path=tmp_path/'ledger.json',output_dir=tmp_path,chat_id='1',purpose='system_test',message='hello',mode='record_send_result',send_result_path=send,write=True)
    assert r['status']=='receipt_recorded'
    assert r['send_result']['message_id']=='9'
    ledger=json.loads((tmp_path/'ledger.json').read_text())
    assert ledger['entries'][0]['message_id']=='9'
