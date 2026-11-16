"""Live protected replay and Oracle transactions spike; dummy response, no real S3 key."""
import json
from pathlib import Path
import time
import uuid
import oci
from shared.runtime import engine

e=engine()
with e.db.connect() as c:
 assert c.execute('SELECT value FROM control WHERE id=?',('schema_version',)).fetchone()[0]=='2'
 privileges=[r[0] for r in c.execute('SELECT privilege FROM session_privs')]
 assert 'CREATE TABLE' not in privileges and 'CREATE SEQUENCE' not in privileges
payload={'lease_id':str(uuid.uuid4()),'access_key_id':'NONCREDENTIAL-CAPABILITY-TEST','secret_access_key':'noncredential-marker'}
ref=e.cloud.store_response(payload,time.time()+30)
assert e.cloud.get_response(ref)==payload
try:
 e.cloud.remove_response(ref)
 assert e.cloud.vault.get_secret(ref).data.lifecycle_state=='PENDING_DELETION'
 try:e.cloud.secrets.get_secret_bundle(ref)
 except oci.exceptions.ServiceError as exc:
  assert exc.status in (404,409)
 else:raise AssertionError('Pending-deletion secret was still retrievable')
 assert e.cloud.get_response(ref) is None
finally:
 e.cloud.remove_response(ref)
 with e.db.tx() as c:c.execute('UPDATE replay_refs SET removed=1 WHERE id=?',(ref,))
print(json.dumps({'mode':'live-oci','oracle_transactional_ledger':True,'runtime_create_privileges_removed':True,'vault_response_replay':True,'pending_deletion_blocks_native_retrieval':True,'customer_secret_keys_created':False},indent=2))
