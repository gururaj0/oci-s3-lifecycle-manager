"""Operator-only additive migration; ADMIN credential stays in memory, never in argv/logs."""
import base64,json,subprocess
from pathlib import Path
import oci
ROOT=Path(__file__).resolve().parents[1]
config=oci.config.from_file();state=json.loads((ROOT/'.local/live.tfstate').read_text())
cfg=json.loads((ROOT/'.local/live-config.json').read_text())
comp=oci.identity.IdentityClient(config).get_compartment(cfg['compartment_id']).data
assert comp.name=='Demo' and comp.id=='ocid1.compartment.oc1..aaaaaaaav6gvasmb4eyn6fnuuaoabal6mdfzuu4qxf3ll5kidwj24jxcn3dq'
secret_id=next(r['instances'][0]['attributes']['id'] for r in state['resources'] if r['type']=='oci_vault_secret' and r['name']=='admin')
password=base64.b64decode(oci.secrets.SecretsClient(config).get_secret_bundle(secret_id).data.secret_bundle_content.content).decode()
ddl=(ROOT/'db/migrations/003_prefix_grants.sql').read_text().strip().rstrip(';').replace('CREATE TABLE lcm_prefix_grants','CREATE TABLE LCM.lcm_prefix_grants').replace('REFERENCES lcm_leases','REFERENCES LCM.lcm_leases')
payload=json.dumps({'password':password,'dsn':cfg['database_url'],'ddl':ddl})
code='''import json,sys,oracledb
p=json.loads(sys.stdin.read())
with oracledb.connect(user='ADMIN',password=p['password'],dsn=p['dsn'],ssl_server_dn_match=True) as c:
 cur=c.cursor()
 if not cur.execute("SELECT COUNT(*) FROM all_tables WHERE owner='LCM' AND table_name='LCM_PREFIX_GRANTS'").fetchone()[0]:cur.execute(p['ddl'])
 columns={r[0] for r in cur.execute("SELECT column_name FROM all_tab_columns WHERE owner='LCM' AND table_name='LCM_PREFIX_GRANTS'")}
 assert columns=={'LEASE_ID','BUCKET','PREFIX','OPERATIONS','GROUP_ID','POLICY_ID','MEMBERSHIP_ID'}
print('Prefix ledger migration verified; existing leases unchanged')
'''
# Transfer code as base64 (no credentials); password is carried only in SSH stdin.
command="sudo /opt/lcm-live/venv/bin/python -c \"import base64;exec(base64.b64decode('"+base64.b64encode(code.encode()).decode()+"'))\""
r=subprocess.run(['ssh','-F','.local/ssh-config','lcm-live',command],input=payload,text=True,capture_output=True,cwd=ROOT)
if r.returncode:raise SystemExit('Prefix migration failed; no credential output retained')
print(r.stdout.strip())
