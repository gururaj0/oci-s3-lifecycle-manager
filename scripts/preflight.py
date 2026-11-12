"""Read-only verification using OCI CLI default profile and region."""
import argparse
import configparser
import json
from pathlib import Path
import subprocess

def cli(*args):
    p=subprocess.run(['oci',*args,'--output','json','--max-retries','0'],capture_output=True,text=True,check=True)
    return json.loads(p.stdout)['data']

if __name__=='__main__':
    cfg=configparser.ConfigParser();cfg.read(Path.home()/'.oci/config')
    profile=cfg['DEFAULT'];tenancy=profile['tenancy'];region=profile['region']
    compartments=cli('iam','compartment','list','--compartment-id',tenancy,'--compartment-id-in-subtree','true','--access-level','ACCESSIBLE','--all')
    matches=[x for x in compartments if x['name']=='Demo' and x['lifecycle-state']=='ACTIVE']
    if len(matches)!=1:raise SystemExit('Expected exactly one active compartment named Demo')
    compartment=cli('iam','compartment','get','--compartment-id',matches[0]['id'])
    assert compartment['id']==matches[0]['id'] and compartment['name']=='Demo'
    regions=cli('iam','region-subscription','list')
    if not any(x['region-name']==region and x['is-home-region'] for x in regions):raise SystemExit('Configured region must be the tenancy home region')
    instances=cli('compute','instance','list','--compartment-id',compartment['id'],'--all')
    bastions=[x for x in instances if x['display-name']=='DemoBastion' and x['lifecycle-state']=='RUNNING']
    if len(bastions)!=1:raise SystemExit('Expected exactly one running DemoBastion')
    vnics=cli('compute','instance','list-vnics','--instance-id',bastions[0]['id'])
    public=[x for x in vnics if x['public-ip']]
    if not public:raise SystemExit('DemoBastion has no public IP')
    print(json.dumps({'placement_verified':True,'compartment_name':'Demo','compartment_id':compartment['id'],
                      'region':region,'home_region':True,'bastion_id':bastions[0]['id'],
                      'bastion_public_ip':public[0]['public-ip'],'bastion_private_ip':public[0]['private-ip'],
                      'mode':'mock','production_ready':False},indent=2))
