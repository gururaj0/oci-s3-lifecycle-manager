"""Run once on the private VM. Bootstrap secrets never enter CLI args, files or logs.
Both Terraform bootstrap passwords are rotated to make old sensitive-state values obsolete.
"""
import base64
import json
import os
from pathlib import Path
import secrets
import oci
import oracledb

cfg=json.loads(Path(os.environ['LCM_BOOTSTRAP_CONFIG']).read_text())
signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
secrets_client=oci.secrets.SecretsClient({'region':cfg['region']},signer=signer)
vault=oci.vault.VaultsClient({'region':cfg['region']},signer=signer)

def read(ref):return base64.b64decode(secrets_client.get_secret_bundle(ref).data.secret_bundle_content.content).decode()
def write(ref,pw):
    vault.update_secret(ref,oci.vault.models.UpdateSecretDetails(secret_content=oci.vault.models.Base64SecretContentDetails(
        content_type='BASE64',content=base64.b64encode(pw.encode()).decode(),stage='CURRENT')))

admin=read(cfg['admin_secret_id']);app=read(cfg['db_secret_id'])
with oracledb.connect(user='ADMIN',password=admin,dsn=cfg['database_url'],ssl_server_dn_match=True) as c:
    cursor=c.cursor()
    exists=cursor.execute("SELECT COUNT(*) FROM dba_users WHERE username='LCM'").fetchone()[0]
    if not exists:
        cursor.execute('CREATE USER LCM IDENTIFIED BY "'+app+'" QUOTA 5G ON DATA')
        cursor.execute('GRANT CREATE SESSION, CREATE TABLE TO LCM')
    # Avoid resetting a working password: ADB enforces password reuse history.
    if exists:
        try:
            probe=oracledb.connect(user='LCM',password=app,dsn=cfg['database_url'],ssl_server_dn_match=True);probe.close()
        except oracledb.DatabaseError:
            cursor.execute('ALTER USER LCM IDENTIFIED BY "'+app+'"')
    cursor.execute('GRANT CREATE TABLE, CREATE SEQUENCE TO LCM')
    with oracledb.connect(user='LCM',password=app,dsn=cfg['database_url'],ssl_server_dn_match=True) as a:
        ac=a.cursor()
        for statement in Path('db/migrations/002_live_runtime.sql').read_text().split(';'):
            statement=statement.strip()
            if not statement:continue
            try:ac.execute(statement)
            except oracledb.DatabaseError as exc:
                if exc.args[0].code not in (955,1):
                    print('Migration statement failed:',statement.split('(')[0][:120]);raise
        a.commit()
    cursor.execute('REVOKE CREATE TABLE, CREATE SEQUENCE FROM LCM')
    new_app='Qv7Z'+secrets.token_urlsafe(18)
    # Persist the recovery value before ALTER; a retry syncs the account from Vault on mismatch.
    write(cfg['db_secret_id'],new_app)
    cursor.execute('ALTER USER LCM IDENTIFIED BY "'+new_app+'"')
    new_admin='Bp8Y'+secrets.token_urlsafe(18)
    write(cfg['admin_secret_id'],new_admin)
    cursor.execute('ALTER USER ADMIN IDENTIFIED BY "'+new_admin+'"')
print(json.dumps({'schema':'LCM','version':2,'bootstrap_passwords_rotated':True,'runtime_create_table_privilege':False}))
