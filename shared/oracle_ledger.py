"""Autonomous Database ledger adapter. Durable row locks serialize cross-host sagas.
The compatibility layer handles only this service's explicit SQL, not arbitrary SQLite SQL.
"""
from collections.abc import Mapping
from contextlib import contextmanager
import re
import time

class Row(Mapping):
    def __init__(self,names,values):
        self.names=names;self.values=values;self.data=dict(zip(names,values))
    def __getitem__(self,k):return self.values[k] if isinstance(k,int) else self.data[k]
    def __iter__(self):return iter(self.data)
    def __len__(self):return len(self.data)

class Result:
    def __init__(self,cursor):
        self.cursor=cursor
        self.names=[x[0].lower() for x in cursor.description] if cursor.description else []
    def fetchone(self):
        r=self.cursor.fetchone();return Row(self.names,r) if r is not None else None
    def fetchall(self):return [Row(self.names,r) for r in self.cursor.fetchall()]
    def __iter__(self):return iter(self.fetchall())

UNIQUE={'users':['id'],'control':['id'],'objects':['bucket','name'],'outbox':['id'],
        'events':['id'],'schedules':['id']}

class Connection:
    def __init__(self,raw):self.raw=raw
    def execute(self,sql,params=()):
        # Three deliberate upserts used by the domain, under the writer row lock.
        if 'ON CONFLICT' in sql:
            if sql.startswith('INSERT INTO rate_buckets'):
                key,minute=params
                current=self.execute('SELECT minute,count FROM rate_buckets WHERE id=?',(key,)).fetchone()
                if current:
                    return self.execute('UPDATE rate_buckets SET minute=?,count=? WHERE id=?',
                        (minute,current['count']+1 if current['minute']==minute else 1,key))
                return self.execute('INSERT INTO rate_buckets(id,minute,count) VALUES(?,?,?)',(key,minute,1))
            if sql.startswith('INSERT INTO control'):
                key='heartbeat';value=params[0]
                if self.execute('SELECT id FROM control WHERE id=?',(key,)).fetchone():
                    return self.execute('UPDATE control SET value=? WHERE id=?',(value,key))
                return self.execute('INSERT INTO control(id,value) VALUES(?,?)',(key,value))
            raise ValueError('Unsupported ledger upsert')
        if sql.startswith('INSERT OR IGNORE INTO'):
            m=re.match(r'INSERT OR IGNORE INTO (\w+)(?:\(([^)]+)\))? VALUES\(([^)]+)\)',sql)
            if not m:raise ValueError('Unsupported insert')
            table,columns,values=m.groups()
            columns=columns.split(',') if columns else {'control':['id','value'],'events':['id','lease_id','received_at'],
                     'schedules':['id','lease_id','generation','due','state'],'objects':['bucket','name','content']}[table]
            tokens=[x.strip() for x in values.split(',')];bound={};j=0
            for col,value in zip(columns,tokens):
                if value=='?':bound[col.strip()]=params[j];j+=1
                elif value.startswith("'"):bound[col.strip()]=value[1:-1]
                else:bound[col.strip()]=int(value)
            keys=UNIQUE[table]
            if self.execute('SELECT '+keys[0]+' FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),tuple(bound[k] for k in keys)).fetchone():
                return Result(self.raw.cursor())
            sql=sql.replace('INSERT OR IGNORE INTO','INSERT INTO',1)
        sql=re.sub(r'\b(users|leases|outbox|schedules|events|audit|control|rate_buckets|replay_refs)\b',lambda m:'lcm_'+m[0],sql)
        sql=re.sub(r'LIMIT (\d+)\s*$',r'FETCH FIRST \1 ROWS ONLY',sql,flags=re.I)
        n=0
        def bind(_):
            nonlocal n;n+=1;return ':'+str(n)
        sql=re.sub(r'\?',bind,sql)
        cur=self.raw.cursor();cur.execute(sql,tuple(params));return Result(cur)

class OracleLedger:
    def __init__(self,cfg,secrets_client=None):
        import base64
        import oci
        import oracledb
        signer=oci.auth.signers.get_resource_principals_signer() if cfg.workload=='function' else oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        client=secrets_client or oci.secrets.SecretsClient({'region':cfg.region},signer=signer)
        response=client.get_secret_bundle(cfg.db_secret_id).data
        password=base64.b64decode(response.secret_bundle_content.content).decode()
        self.pool=oracledb.create_pool(user=cfg.db_user,password=password,dsn=cfg.database_url,min=1,max=8,increment=1,
                                     ssl_server_dn_match=True,timeout=60,wait_timeout=15000)
        del password

    @contextmanager
    def connect(self):
        with self.pool.acquire() as raw:
            try:yield Connection(raw)
            finally:raw.rollback()

    @contextmanager
    def tx(self):
        with self.pool.acquire() as raw:
            try:
                c=Connection(raw)
                c.execute("SELECT value FROM control WHERE id='writer_lock' FOR UPDATE WAIT 30").fetchone()
                yield c
                raw.commit()
            except BaseException:
                raw.rollback();raise

    @staticmethod
    def audit(c,lease_id,action,correlation_id=None,detail=None):
        c.execute('INSERT INTO audit(at,lease_id,action,correlation_id,detail) VALUES(?,?,?,?,?)',
                  (time.time(),lease_id,action,correlation_id,detail))

    def lease(self,lease_id):
        with self.connect() as c:
            row=c.execute('SELECT * FROM leases WHERE id=?',(lease_id,)).fetchone()
            return dict(row) if row else None
