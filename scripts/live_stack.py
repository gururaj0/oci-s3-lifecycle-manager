"""Manage the dedicated live stack through Resource Manager, never through local TF apply."""
import argparse
import json
from pathlib import Path
import subprocess
import zipfile
import sys
from oci_cli import ROOT,oci
p=argparse.ArgumentParser();p.add_argument('action',choices=['create','update','status','logs']);args=p.parse_args()
record=ROOT/'.local/live-deployment.json'
variables=json.loads((ROOT/'.local/live-variables.json').read_text())
if args.action in ('create','update'):
    if args.action=='update':
        current=json.loads(record.read_text())
        previous=oci('resource-manager','job','get','--job-id',current['job_id'])
        if previous['lifecycle-state'] not in ('SUCCEEDED','FAILED','CANCELED'):
            raise SystemExit('Previous Resource Manager job is still running; wait for its terminal state')
        state=ROOT/'.local/live.tfstate'
        subprocess.run(['oci','resource-manager','stack','get-stack-tf-state','--stack-id',current['stack_id'],'--file',str(state)],capture_output=True,check=True)
        state.chmod(0o600)
    subprocess.run([sys.executable,str(ROOT/'scripts/package_live_source.py')],check=True,capture_output=True)
    import shutil
    shutil.copy2(ROOT/'release/lcm-live-source.tar.xz',ROOT/'infra/live/lcm-live-source.tar.xz')
    subprocess.run([str(ROOT/'.local/tools/terraform'),'-chdir='+str(ROOT/'infra/live'),'validate','-no-color'],check=True,capture_output=True)
    comp=oci('iam','compartment','get','--compartment-id',variables['compartment_ocid'])
    assert comp['name']=='Demo' and comp['lifecycle-state']=='ACTIVE'
    plan=[str(ROOT/'.local/tools/terraform'),'-chdir='+str(ROOT/'infra/live'),'plan','-input=false','-no-color','-var-file='+str(ROOT/'.local/live-variables.json')]
    if (ROOT/'.local/live.tfstate').exists():plan.append('-state='+str(ROOT/'.local/live.tfstate'))
    result=subprocess.run(plan,capture_output=True,text=True)
    log=ROOT/'.local/live-preapply-plan.log';log.write_text(result.stdout+result.stderr);log.chmod(0o600)
    if result.returncode:raise SystemExit('Live preapply plan failed; inspect .local/live-preapply-plan.log')
    if __import__('re').search(r'# (?!oci_objectstorage_object.release)[^\n]+(?:must be replaced|will be destroyed)',result.stdout):
        raise SystemExit('Live update would replace/remove resources; inspect the private preapply plan before proceeding')
    archive=ROOT/'release/oci-s3-lcm-live-foundation.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in (ROOT/'infra/live').iterdir():
            if f.is_file() and (f.suffix in ('.tf','.yaml','.tftpl','.b64','.xz') or f.name=='.terraform.lock.hcl'):z.write(f,f.name)
    if args.action=='create':
        if record.exists():raise SystemExit('Live stack already recorded; update it instead')
        stack=oci('resource-manager','stack','create','--compartment-id',comp['id'],
            '--display-name','OCI S3 Lifecycle Manager — live 0.2.0','--terraform-version','1.5.x',
            '--config-source',str(archive),'--variables',json.dumps(variables))
        data={'stack_id':stack['id'],'region':variables['region'],'compartment_id':comp['id']}
        record.write_text(json.dumps(data,indent=2)+'\n')
    else:
        data=json.loads(record.read_text())
        oci('resource-manager','stack','update','--stack-id',data['stack_id'],'--config-source',str(archive),'--variables',json.dumps(variables),'--force')
    job=oci('resource-manager','job','create-apply-job','--stack-id',data['stack_id'],
        '--display-name','Live integration foundation' if args.action=='create' else 'Live integration update',
        '--execution-plan-strategy','AUTO_APPROVED')
    data.setdefault('jobs',[]).append(job['id']);data['job_id']=job['id'];record.write_text(json.dumps(data,indent=2)+'\n');print(json.dumps({k:data[k] for k in ('stack_id','job_id','compartment_id')},indent=2))
else:
    data=json.loads(record.read_text())
    if args.action=='logs':
        logs=oci('resource-manager','job','get-job-logs','--job-id',data['job_id'],'--all')
        for l in logs[-16:]:print(l['message'])
    else:
        job=oci('resource-manager','job','get','--job-id',data['job_id'])
        print(json.dumps({'state':job['lifecycle-state'],'failure':job.get('failure-details'),'job_id':job['id']}))
        if job['lifecycle-state']=='SUCCEEDED':
            state=ROOT/'.local/live.tfstate'
            subprocess.run(['oci','resource-manager','stack','get-stack-tf-state','--stack-id',data['stack_id'],'--file',str(state)],capture_output=True,check=True)
            state.chmod(0o600)
            outputs={k:v['value'] for k,v in json.loads(state.read_text())['outputs'].items()}
            (ROOT/'.local/live-outputs.json').write_text(json.dumps(outputs,indent=2)+'\n')
            print(json.dumps({'runtime':outputs.get('runtime'),'database_id':outputs.get('foundation',{}).get('database_id'),'peering':outputs.get('peering')},indent=2))
