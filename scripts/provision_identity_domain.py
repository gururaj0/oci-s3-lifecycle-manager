"""Provision dedicated OAuth apps; secret values are private and never printed.
Uses the operator's existing OCI config, verifies Demo before any mutation.
"""
import base64
import json
import os
from pathlib import Path
import oci
import requests
import jwt

ROOT=Path(__file__).resolve().parents[1]
DOMAIN='https://idcs-1ee8a5696b814af291e90b1adc2fe166.identity.oraclecloud.com'
DEMO='ocid1.compartment.oc1..aaaaaaaav6gvasmb4eyn6fnuuaoabal6mdfzuu4qxf3ll5kidwj24jxcn3dq'
URL='https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com'
AUD=URL+'/lifecycle/'
SCOPES={p:AUD+p for p in ('sales-read','sales-write')}

def main():
 os.umask(0o077)
 c=oci.config.from_file();iam=oci.identity.IdentityClient(c)
 compartment=iam.get_compartment(DEMO).data
 assert compartment.name=='Demo' and compartment.lifecycle_state=='ACTIVE'
 d=oci.identity_domains.IdentityDomainsClient(c,service_endpoint=DOMAIN)
 users=d.list_users(filter='ocid eq "'+c['user']+'"',attributes='id,userName').data.resources
 assert len(users)==1
 path=ROOT/'.local/identity-domain-private.json'
 private=json.loads(path.read_text()) if path.exists() else {}
 def app(key,**kwargs):
  if key in private:return private[key]
  name='LCM Live '+key
  assert not d.list_apps(filter='displayName eq "'+name+'"',attributes='id').data.resources,'Existing registration needs recovery; do not duplicate'
  value=d.create_app(app=oci.identity_domains.models.App(
   schemas=['urn:ietf:params:scim:schemas:oracle:idcs:App'],display_name=name,
   description='Dedicated OCI S3 Lifecycle Manager demo; no domain administration privileges',
   based_on_template=oci.identity_domains.models.AppBasedOnTemplate(value='CustomWebAppTemplateId'),
   active=True,**kwargs)).data
  private[key]={'id':value.id,'client_id':value.name,'client_secret':value.client_secret}
  path.write_text(json.dumps(private));path.chmod(0o600)
  return private[key]
 resource=app('Lease API',is_o_auth_resource=True,audience=AUD,access_token_expiry=600,
  scopes=[oci.identity_domains.models.AppScopes(value=p,description='Request '+p+' credential leases',requires_consent=False) for p in SCOPES])
 connector=app('Unity Demo Connector',is_o_auth_client=True,client_type='confidential',
  allowed_grants=['client_credentials'],trust_scope='Explicit',
  allowed_scopes=[oci.identity_domains.models.AppAllowedScopes(fqs=s) for s in SCOPES.values()])
 browser=app('Demo UI Sign In',is_o_auth_client=True,client_type='confidential',
  allowed_grants=['authorization_code'],trust_scope='Explicit',redirect_uris=[URL+'/auth/callback'],
  post_logout_redirect_uris=[URL+'/'],is_login_target=True,show_in_my_apps=False)
 discovery=requests.get(DOMAIN+'/.well-known/openid-configuration',timeout=20).json()
 r=requests.post(discovery['token_endpoint'],auth=(connector['client_id'],connector['client_secret']),
  data={'grant_type':'client_credentials','scope':' '.join(SCOPES.values())},timeout=30)
 if r.status_code!=200:raise RuntimeError('Token issuance failed: '+str(r.status_code)+' '+r.json().get('error',''))
 token=r.json()['access_token']
 jwks=jwt.PyJWKClient(discovery['jwks_uri'])
 claims=jwt.decode(token,jwks.get_signing_key_from_jwt(token).key,algorithms=['RS256'],audience=AUD,issuer=discovery['issuer'])
 cfg=json.loads((ROOT/'.local/live-config.json').read_text())
 # This script stages a new config; it does not replace the running trust configuration.
 cfg.update(issuer=discovery['issuer'],audience=AUD,jwks_url=discovery['jwks_uri'],
  identities={discovery['issuer']:{claims['sub']:list(SCOPES)}},profile_scopes={p:p for p in SCOPES},
  oauth={'domain_url':DOMAIN,'authorization_endpoint':discovery['authorization_endpoint'],
   'token_endpoint':discovery['token_endpoint'],'login_client_id':browser['client_id'],
   'connector_client_id':connector['client_id'],'scope':' '.join(SCOPES.values()),
   'allowed_login_subjects':[users[0].user_name,users[0].id],
   'public_url':URL})
 if 'vault_secret_id' not in private:
  payload=json.dumps({'login_client_secret':browser['client_secret'],'connector_client_secret':connector['client_secret']})
  vault=oci.vault.VaultsClient(c)
  result=vault.create_secret(oci.vault.models.CreateSecretDetails(compartment_id=DEMO,vault_id=cfg['vault_id'],key_id=cfg['vault_key_id'],
   secret_name=cfg['deployment_id']+'-oauth-connector',description='LCM confidential OAuth clients',
   freeform_tags={'deployment':cfg['deployment_id']},secret_content=oci.vault.models.Base64SecretContentDetails(content_type='BASE64',content=base64.b64encode(payload.encode()).decode()))).data
  private['vault_secret_id']=result.id;path.write_text(json.dumps(private))
 cfg['oauth']['secret_id']=private['vault_secret_id']
 (ROOT/'.local/identity-domain-config.json').write_text(json.dumps(cfg,indent=2)+'\n')
 evidence={'compartment_verified':DEMO,'domain':DOMAIN,'issuer':claims['iss'],'audience':claims['aud'],
  'subject':claims['sub'],'scope':claims.get('scope'),'lifetime_seconds':claims['exp']-claims['iat'],
  'signature_verified':True,'apps':{k:{x:v[x] for x in ('id','client_id')} for k,v in private.items() if isinstance(v,dict)}}
 (ROOT/'docs/identity-domain-provisioning.json').write_text(json.dumps(evidence,indent=2)+'\n')
 print(json.dumps(evidence,indent=2))
if __name__=='__main__':main()
