"""Read-only status and redacted output refresh for the recorded Resource Manager stack."""
import json
from pathlib import Path
from deploy_oci import ROOT,oci

if __name__=='__main__':
    record=ROOT/'.local/deployment.json';data=json.loads(record.read_text())
    jid=data.get('rollout_job_id',data['apply_job_id'])
    job=oci('resource-manager','job','get','--job-id',jid)
    print(json.dumps({'job_id':jid,'state':job['lifecycle-state'],'failure':job.get('failure-details')},indent=2))
    if job['lifecycle-state']=='SUCCEEDED':
        stack=oci('resource-manager','stack','get','--stack-id',data['stack_id'])
        import subprocess
        target=ROOT/'.local/current.tfstate'
        subprocess.run(['oci','resource-manager','stack','get-stack-tf-state','--stack-id',data['stack_id'],'--file',str(target)],check=True,capture_output=True)
        outputs={k:v['value'] for k,v in json.loads(target.read_text())['outputs'].items()}
        data.update({k:outputs[k] for k in ['api_endpoint','instance_id','private_ip']})
        data['gateway_hostname']=outputs['gateway_hostname']
        record.write_text(json.dumps(data,indent=2)+'\n')
        print(json.dumps({'private_ip':data['private_ip'],'instance_id':data['instance_id'],'api_endpoint':data['api_endpoint']},indent=2))
