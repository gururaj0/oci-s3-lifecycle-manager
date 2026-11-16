"""Prepare nonsecret runtime config and (once) synchronize pre-bootstrap Vault passwords.
State is read privately and no credential values are printed or copied into runtime config.
"""
import argparse
import base64
import json
from pathlib import Path
import secrets
import oci
from deploy_oci import ROOT
p=argparse.ArgumentParser();p.add_argument('--sync-bootstrap-secrets',action='store_true');args=p.parse_args()
state=json.loads((ROOT/'.local/live.tfstate').read_text())
resources={(r['type'],r['name']):r['instances'] for r in state['resources']}
def first(kind,name):return resources[kind,name][0]['attributes']
settings=json.loads((ROOT/'.local/live-variables.json').read_text())
subject_file=ROOT/'.local/live-subject.txt'
if not subject_file.exists():subject_file.write_text('demo:live-integration-'+secrets.token_hex(16))
profile={};groups={x['index_key']:x['attributes']['id'] for x in resources['oci_identity_group','scope']}
users={k:[] for k in ['sales-read','sales-write']}
for x in resources['oci_identity_user','pool']:
 users['sales-read' if x['index_key'].startswith('sales-read') else 'sales-write'].append(x['attributes']['id'])
profiles=first('oci_database_autonomous_database','ledger')['connection_strings'][0]['profiles']
server=[x for x in profiles if x.get('tls_authentication')=='SERVER' and x.get('consumer_group')=='LOW']
if not server:raise SystemExit('ADB has no one-way TLS LOW connection profile')
vault=first('oci_kms_vault','lcm');key=first('oci_kms_key','lcm')
cfg={'mode':'oci','region':settings['region'],'deployment_id':settings.get('prefix','lcm-live-v2'),
 'state_dir':'/var/lib/lcm-live','tenancy_id':settings['tenancy_ocid'],
 'compartment_id':settings['compartment_ocid'],'scheduler_compartment_id':settings['compartment_ocid'],
 'secret_compartment_id':settings['compartment_ocid'],'vault_id':vault['id'],'vault_key_id':key['id'],
 'namespace':settings['namespace'],'db_user':'LCM','db_secret_id':first('oci_vault_secret','application')['id'],
 'database_url':server[0]['value'],'issuer':'urn:oci-lcm:mock-demo','audience':'oci-lcm',
 'jwks_url':'https://exjtqqyg54c5dek5vgprfx3no4.apigateway.us-ashburn-1.oci.customer-oci.com/.well-known/jwks.json',
 'identities':{'urn:oci-lcm:mock-demo':{subject_file.read_text():list(users)}},'service_users':users,
 'profiles':{p:{'bucket':first('oci_objectstorage_bucket','sales')['name'],'operations':['LIST','GET']+(['PUT'] if p=='sales-write' else []),'version':1,'group_id':groups[p]} for p in users},
 'worker_interval':10,'resource_tags':{'LcmLeaseV2':{'deployment':settings.get('prefix','lcm-live-v2')}},
 'integration_gates':{},'functions':{}}
(ROOT/'.local/live-config.json').write_text(json.dumps(cfg,indent=2)+'\n')
bootstrap=dict(cfg,admin_secret_id=first('oci_vault_secret','admin')['id'])
(ROOT/'.local/live-bootstrap.json').write_text(json.dumps(bootstrap,indent=2)+'\n')
(ROOT/'.local/live-build.json').write_text(json.dumps({'region':cfg['region'],'registry':'iad.ocir.io','namespace':cfg['namespace'],'repository':settings.get('prefix','lcm-live-v2')+'/worker'},indent=2)+'\n')
if args.sync_bootstrap_secrets:
 if (ROOT/'.local/bootstrap-success.json').exists():raise SystemExit('Bootstrap already completed; never reset rotated passwords from Terraform')
 c=oci.config.from_file();v=oci.vault.VaultsClient(c)
 for name in ['admin','application']:
  password=first('random_password',name)['result']
  assert any(ch.isdigit() for ch in password) and any(ch.isupper() for ch in password)
  ref=first('oci_vault_secret',name)['id']
  v.update_secret(ref,oci.vault.models.UpdateSecretDetails(secret_content=oci.vault.models.Base64SecretContentDetails(content_type='BASE64',content=base64.b64encode(password.encode()).decode(),stage='CURRENT')))
 print('Bootstrap Vault passwords synchronized privately; rotate after schema bootstrap.')
print('Live config prepared (only resource references and explicit identity mapping).')
