"""Provisioner-only service-user hardening and membership evidence; no employee users touched."""
import json
from pathlib import Path
import oci
from deploy_oci import ROOT
s=json.loads((ROOT/'.local/live.tfstate').read_text());settings=json.loads((ROOT/'.local/live-variables.json').read_text())
a={(r['type'],r['name']):r['instances'] for r in s['resources']}
groups={x['index_key']:x['attributes']['id'] for x in a['oci_identity_group','scope']}
iam=oci.identity.IdentityClient(oci.config.from_file());result={'service_users':{},'scope':'dedicated LCM population only'}
for x in a['oci_identity_user','pool']+a['oci_identity_user','sentinel']:
 uid=x['attributes']['id'];name=x.get('index_key','sentinel')
 caps=oci.identity.models.UpdateUserCapabilitiesDetails(can_use_console_password=False,can_use_api_keys=False,
    can_use_auth_tokens=False,can_use_smtp_credentials=False,can_use_db_credentials=False,can_use_customer_secret_keys=True,can_use_o_auth2_client_credentials=False)
 iam.update_user_capabilities(uid,caps)
 user=iam.get_user(uid).data
 members=iam.list_user_group_memberships(settings['tenancy_ocid'],user_id=uid).data
 expected={groups['sales-read' if name.startswith('sales-read') else 'sales-write']} if name!='sentinel' else set()
 assert {m.group_id for m in members}==expected,'Unexpected membership: do not enable this user'
 assert len(iam.list_api_keys(uid).data)==0 and len(iam.list_auth_tokens(uid).data)==0 and len(iam.list_smtp_credentials(uid).data)==0
 assert len(iam.list_customer_secret_keys(uid).data)==0,'Outstanding customer key needs review before issuance'
 result['service_users'][name]={'user_id':uid,'groups_match':True,'unrelated_credentials_absent':True,'capabilities':oci.util.to_dict(user.capabilities)}
(ROOT/'docs/live-identity-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print('Dedicated pool and sentinel identities hardened and verified; no credentials were generated.')
