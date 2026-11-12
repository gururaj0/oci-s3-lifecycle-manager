"""Drain-first release update of the recorded mock stack. Exports audit before VM replacement."""
import json
from pathlib import Path
import subprocess
from deploy_oci import ROOT,oci

if __name__=='__main__':
    evidence=ROOT/'docs/live-smoke-evidence.json'
    if not evidence.exists() or json.loads(evidence.read_text())['checks'].get('five_minute_expiry')!='PASS_MOCK':
        raise SystemExit('Require completed first-pass expiry evidence before this release rollout.')
    record=ROOT/'.local/deployment.json';data=json.loads(record.read_text())
    if data.get('rollout_job_id'):raise SystemExit('Rollout already recorded; inspect that job instead of creating a duplicate.')
    ssh=['ssh','-F','.local/ssh-config','lcm-backend']
    operator='cd /opt/lcm && sudo -u lcm env LCM_STATE_DIR=/var/lib/lcm LCM_CONFIG=/etc/lcm/config.json /opt/lcm/venv/bin/python -m scripts.lcm_operator '
    drain=subprocess.run(ssh+[operator+'drain'],capture_output=True,text=True,check=True,cwd=ROOT)
    status=json.loads(drain.stdout)
    if not status['safe_to_remove']:raise SystemExit('Drain did not confirm safe removal')
    audit=subprocess.run(ssh+[operator+'export-audit'],capture_output=True,text=True,check=True,cwd=ROOT)
    (ROOT/'.local/audit-before-rollout.jsonl').write_text(audit.stdout)
    (ROOT/'docs/drain-evidence.json').write_text(json.dumps(status,indent=2)+'\n')
    oci('resource-manager','stack','update','--stack-id',data['stack_id'],
        '--config-source',str(ROOT/'release/oci-s3-lcm-0.1.0-mock.zip'),'--force')
    job=oci('resource-manager','job','create-apply-job','--stack-id',data['stack_id'],
            '--display-name','Drain-first final mock release rollout','--execution-plan-strategy','AUTO_APPROVED')
    data['rollout_job_id']=job['id'];record.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'rollout_job_id':job['id'],'drain':status,'audit_exported':True},indent=2))
