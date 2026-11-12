"""Deploy the embedded mock release through OCI Resource Manager, using OCI CLI defaults.
Run preflight first. Persists only deployment identifiers, never runtime tokens/credentials.
"""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def oci(*args):
    p=subprocess.run(['oci',*args,'--output','json'],capture_output=True,text=True)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout)['data']

if __name__=='__main__':
    variables=json.loads((ROOT/'.local/stack-variables.json').read_text())
    comp=oci('iam','compartment','get','--compartment-id',variables['compartment_ocid'])
    assert comp['name']=='Demo' and comp['lifecycle-state']=='ACTIVE'
    subs=oci('iam','region-subscription','list')
    assert any(s['is-home-region'] and s['region-name']==variables['region'] for s in subs)
    record=ROOT/'.local/deployment.json'
    if record.exists():
        raise SystemExit('Deployment record already exists; use the recorded stack for updates.')
    stack=oci('resource-manager','stack','create','--compartment-id',variables['compartment_ocid'],
              '--display-name','OCI S3 Lifecycle Manager — mock 0.1.0',
              '--description','Explicit synthetic demo. Production M0 gates outstanding.',
              '--config-source',str(ROOT/'release/oci-s3-lcm-0.1.0-mock.zip'),
              '--terraform-version','1.5.x','--variables',json.dumps(variables),
              '--freeform-tags',json.dumps({'application':'oci-s3-lcm','mode':'mock','release':'0.1.0'}))
    data={'stack_id':stack['id'],'region':variables['region'],'compartment_id':comp['id'],'mode':'mock'}
    record.write_text(json.dumps(data,indent=2)+'\n')
    job=oci('resource-manager','job','create-apply-job','--stack-id',stack['id'],
            '--display-name','Deploy mock 0.1.0','--execution-plan-strategy','AUTO_APPROVED')
    data['apply_job_id']=job['id']
    record.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data,indent=2))
