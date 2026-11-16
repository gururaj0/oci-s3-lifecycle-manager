"""Real positive/negative workload-principal Function invocation test; no keys are created."""
import json
from pathlib import Path
import uuid
import oci
cfg=json.loads(Path('/etc/lcm-live/function-details.json').read_text())
signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
payload=json.dumps({'lease_id':str(uuid.uuid4()),'generation':1})
results={}
for role in ['delete','sentinel']:
 info=cfg[role]
 client=oci.functions.FunctionsInvokeClient({'region':'us-ashburn-1'},signer=signer,service_endpoint=info['endpoint'],timeout=(10,150))
 try:
  response=client.invoke_function(info['id'],invoke_function_body=payload)
  if role=='sentinel':raise AssertionError('Unapproved Function invocation was allowed')
  results['delete_invocation_status']=response.status
  results['delete_result']=json.loads(response.data.content)
 except oci.exceptions.ServiceError as exc:
  if role!='sentinel':raise
  assert exc.status in (403,404);results['unapproved_function_denied']=True
print(json.dumps(results,indent=2))
