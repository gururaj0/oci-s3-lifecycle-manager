"""Read-only verification of deployed policy, listener isolation and image digests."""
import hashlib,json,subprocess
from pathlib import Path
import oci
ROOT=Path(__file__).resolve().parents[1]
c=oci.config.from_file();state=json.loads((ROOT/'.local/live.tfstate').read_text());cfg=json.loads((ROOT/'.local/live-config.json').read_text());manifest=json.loads((ROOT/'release/live-image-manifest.json').read_text())
resources={(r['type'],r['name']):r['instances'] for r in state['resources']}
def attrs(kind,name):return resources[kind,name][0]['attributes']
gateway=oci.apigateway.DeploymentClient(c).get_deployment(attrs('oci_apigateway_deployment','live')['id']).data
spec=gateway.specification;auth=spec.request_policies.authentication
assert auth.type=='TOKEN_AUTHENTICATION' and auth.token_header=='Authorization'
policy=auth.validation_policy
assert policy.type=='REMOTE_JWKS' and policy.uri==cfg['jwks_url'] and not policy.is_ssl_verify_disabled
assert policy.additional_validation_policy.issuers==[cfg['issuer']]
assert policy.additional_validation_policy.audiences==[cfg['audience']]
assert {v.key for v in policy.additional_validation_policy.verify_claims if v.is_required}>={'sub','scope','exp'}
protected=next(r for r in spec.routes if r.path=='/v1/{path*}')
assert protected.request_policies.authorization.type=='ANY_OF'
assert set(protected.request_policies.authorization.allowed_scope)==set(cfg['profile_scopes'].values())
assert ':8081/' in protected.backend.url
items=protected.request_policies.header_transformations.set_headers.items
expected={'x-lcm-issuer':'${request.auth[iss]}','x-lcm-subject':'${request.auth[sub]}','x-lcm-scope':'${request.auth[scope]}'}
assert {i.name.lower():i.values[0] for i in items}==expected and all(i.if_exists=='OVERWRITE' for i in items)
assert all(r.request_policies.authorization.type=='ANONYMOUS' and ':8080/' in r.backend.url for r in spec.routes if r.path!=protected.path)
network=oci.core.VirtualNetworkClient(c)
runtime_nsg=attrs('oci_core_network_security_group','runtime')['id'];gateway_nsg=attrs('oci_core_network_security_group','gateway')['id']
rules=oci.pagination.list_call_get_all_results(network.list_network_security_group_security_rules,runtime_nsg).data
api_rules=[r for r in rules if r.direction=='INGRESS' and r.protocol=='6' and r.tcp_options and r.tcp_options.destination_port_range and r.tcp_options.destination_port_range.min<=8081<=r.tcp_options.destination_port_range.max]
assert len(api_rules)==1 and api_rules[0].source==gateway_nsg and api_rules[0].source_type=='NETWORK_SECURITY_GROUP'
assert not network.get_security_list(attrs('oci_core_security_list','empty')['id']).data.ingress_security_rules
statements=oci.identity.IdentityClient(c).get_policy(attrs('oci_identity_policy','issuer_services')['id']).data.statements
assert not any('manage repos' in s for s in statements)
functions={};fm=oci.functions.FunctionsManagementClient(c)
for (kind,name),instances in resources.items():
 if kind=='oci_functions_function':
  for i in instances:
   fn=fm.get_function(i['attributes']['id']).data
   assert fn.source_details.image_digest==manifest['digest']
   functions[fn.display_name]=fn.source_details.image_digest
code="""import json,subprocess
from pathlib import Path
c=json.loads(Path('/etc/lcm-live/config.json').read_text())
assert c['auth_mode']=='gateway' and c['gateway_trusted_networks']==['10.73.0.0/24']
for unit,port in [('lcm-api','8081'),('lcm-ui','8080')]:
 subprocess.run(['systemctl','is-active','--quiet',unit],check=True)
 text=Path('/etc/systemd/system/'+unit+'.service').read_text()
 assert '--no-proxy-headers' in text and port in text
print(json.dumps({'auth_mode':c['auth_mode'],'image':json.loads(Path('/opt/lcm-live/image.json').read_text())['image'],'separate_listeners':True,'proxy_headers_disabled':True}))
"""
r=subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','sudo /opt/lcm-live/venv/bin/python -'],input=code,cwd=ROOT,text=True,capture_output=True,check=True)
host=json.loads(r.stdout);assert host['image']==manifest['image']
record=json.loads((ROOT/'.local/live-deployment.json').read_text());job=oci.resource_manager.ResourceManagerClient(c).get_job(record['job_id']).data
assert job.lifecycle_state=='SUCCEEDED'
out={'job_id':job.id,'state':job.lifecycle_state,'image':manifest['image'],'functions':functions,'runtime':host,
 'gateway_validates_jwt':True,'backend_revalidates_service_jwt':False,'identity_headers_overwritten':True,
 'api_listener_gateway_nsg_only':True,'temporary_build_permission_removed':True,
 'production_ready':False}
(ROOT/'docs/gateway-auth-deployment.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
