"""Verify final workload permissions after removing temporary bootstrap rights."""
import json
from pathlib import Path
import oci
from shared.runtime import engine
e=engine();cfg=e.cfg
bootstrap=json.loads(Path('/etc/lcm-live/bootstrap.json').read_text())
scope=json.loads(Path('/etc/lcm-live/scope.json').read_text())
out={'workload':'instance-principal','checks':{}}
def denied(name,call):
 try:
  value=call();del value
 except oci.exceptions.ServiceError as exc:
  assert exc.status in (403,404),name+': '+str(exc.status)
  out['checks'][name]=True
 else:raise AssertionError('Unexpected workload permission: '+name)
denied('bootstrap_admin_secret_denied',lambda:e.cloud.secrets.get_secret_bundle(bootstrap['admin_secret_id']))
denied('database_secret_modification_denied',lambda:e.cloud.vault.update_secret(cfg.db_secret_id,oci.vault.models.UpdateSecretDetails(description='LCM database application credential')))
denied('foreign_user_keys_denied',lambda:e.cloud.identity.list_customer_secret_keys(scope['sentinel_id']))
user=next(iter(cfg.service_users.values()))[0]
value=e.cloud.identity.get_user(user).data
denied('general_user_update_denied',lambda:e.cloud.identity.update_user(user,oci.identity.models.UpdateUserDetails(description=value.description)))
group=cfg.profiles['sales-read']['group_id'];description=e.cloud.identity.get_group(group).data.description
denied('group_modification_denied',lambda:e.cloud.identity.update_group(group,oci.identity.models.UpdateGroupDetails(description=description)))
with e.db.connect() as c:
 privileges={r[0] for r in c.execute('SELECT privilege FROM session_privs')}
assert privileges=={'CREATE SESSION'},'Unexpected runtime schema privileges'
out['checks']['explicit_system_privileges_only_create_session']=True
for users in cfg.service_users.values():
 for user in users:e.cloud.validate_user(user)
out['checks']['all_pool_capabilities_and_memberships_match']=True
print(json.dumps(out,indent=2))
