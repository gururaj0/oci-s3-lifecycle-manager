import hashlib
import json
import math
import random
import time
import uuid
from datetime import datetime, timezone
from shared.errors import Problem


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else None

SAFE = ('id', 'state', 'profile', 'profile_version', 'requested_ttl', 'generation',
        'created_at', 'key_created_at', 'expires_at', 'delivered_at', 'deleted_at', 'last_error')

class Lifecycle:
    def __init__(self, cfg, ledger, cloud):
        self.cfg, self.db, self.cloud = cfg, ledger, cloud
        with ledger.tx() as c:
            for profile in cfg.profiles:
                ids=cfg.service_users.get(profile,[]) if cfg.mode=='oci' else [f'{cfg.deployment_id}:{profile}:{n+1}' for n in range(cfg.pool_size)]
                for user_id in ids:
                    c.execute('INSERT OR IGNORE INTO users(id,profile) VALUES(?,?)',(user_id,profile))
            c.execute("INSERT OR IGNORE INTO control VALUES('paused','0')")
            c.execute("INSERT OR IGNORE INTO control VALUES('cleanup_error','0')")
            if cfg.mode=='mock':
                c.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?)',('sales-demo','quarterly/revenue.csv',b'quarter,revenue\nQ1,124000\nQ2,148000\nQ3,176000\n'))
                c.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?)',('sales-demo','README.txt',b'Synthetic sales data.\n'))

    def healthy(self):
        now = time.time()
        with self.db.connect() as c:
            r = c.execute("SELECT value FROM control WHERE id='heartbeat'").fetchone()
            paused = c.execute("SELECT value FROM control WHERE id='paused'").fetchone()
            dependency=c.execute("SELECT value FROM control WHERE id='cleanup_error'").fetchone()
            overdue = c.execute("SELECT MIN(expires_at) FROM leases WHERE state!='REVOKED' AND expires_at<?", (now,)).fetchone()[0]
        return bool(r and now-float(r[0]) <= self.cfg.cleanup_stale and
                    (not overdue or now-overdue <= self.cfg.overdue_threshold) and paused[0] == '0' and (not dependency or dependency[0]=='0'))

    def rate(self, owner):
        key = hashlib.sha256(json.dumps({k:owner[k] for k in ('issuer','subject')}, sort_keys=True).encode()).hexdigest()
        minute = int(time.time() // 60)
        with self.db.tx() as c:
            c.execute('DELETE FROM rate_buckets WHERE minute<?', (minute-1,))
            c.execute('INSERT INTO rate_buckets VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET minute=excluded.minute,count=CASE WHEN rate_buckets.minute=excluded.minute THEN rate_buckets.count+1 ELSE 1 END', (key,minute))
            count = c.execute('SELECT count FROM rate_buckets WHERE id=?', (key,)).fetchone()[0]
        if count > self.cfg.rate_limit:
            raise Problem(429, 'RATE_LIMITED')

    def view(self, r):
        result = {('lease_id' if k == 'id' else 'status' if k == 'state' else 'scope_profile' if k == 'profile' else k):
                utc(r[k]) if k.endswith('_at') else r[k] for k in SAFE}
        result['phase']=('PREPARING_REPLAY' if r['schedule_id'] else 'CONFIRMING_SCHEDULE') if r['state']=='SCHEDULE_PENDING' else r['state']
        if self.cfg.mode=='oci':result['service_user_id']=r['user_id']
        return result

    def owned(self, lease_id, owner):
        r = self.db.lease(lease_id)
        if not r:
            raise Problem(404, 'LEASE_NOT_FOUND')
        if r['issuer'] != owner['issuer'] or r['subject'] != owner['subject']:
            raise Problem(403, 'NOT_OWNER')
        return r

    def replay(self, r, request_hash):
        if r['request_hash'] != request_hash:
            raise Problem(409, 'IDEMPOTENCY_CONFLICT', r['id'])
        if r['state'] in ('PREPARING', 'SCHEDULE_PENDING'):
            raise Problem(409, 'ISSUANCE_PENDING', r['id'])
        if r['state'] != 'ACTIVE' or not r['replay_until'] or r['replay_until'] <= time.time() or r['expires_at'] <= time.time():
            raise Problem(409, 'CREDENTIALS_NOT_REPLAYABLE', r['id'])
        payload = self.cloud.get_response(r['replay_ref'])
        if not payload:
            raise Problem(409, 'CREDENTIALS_NOT_REPLAYABLE', r['id'])
        return 200, payload

    def create(self, owner, body, idem, correlation, endpoint, defer_setup=False):
        profile, ttl = body['scope_profile'], body['ttl_seconds']
        if profile not in self.cfg.profiles:
            raise Problem(422, 'UNSUPPORTED_SCOPE')
        if not self.cfg.ttl_min <= ttl <= self.cfg.ttl_max:
            raise Problem(400, 'INVALID_TTL')
        self.rate(owner)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        lease_id = str(uuid.uuid4())
        with self.db.tx() as c:
            old = c.execute('SELECT * FROM leases WHERE issuer=? AND subject=? AND idem=?',
                            (owner['issuer'], owner['subject'], idem)).fetchone()
            if old:
                return self.replay(dict(old), digest)
            # This read is intentionally inside the same writer lock as reservation.
            heartbeat = c.execute("SELECT value FROM control WHERE id='heartbeat'").fetchone()
            paused = c.execute("SELECT value FROM control WHERE id='paused'").fetchone()[0]
            dependency=c.execute("SELECT value FROM control WHERE id='cleanup_error'").fetchone()
            overdue = c.execute("SELECT MIN(expires_at) FROM leases WHERE state!='REVOKED'").fetchone()[0]
            if (dependency and dependency[0]!='0') or not heartbeat or time.time()-float(heartbeat[0])>self.cfg.cleanup_stale or paused=='1' or (overdue and time.time()-overdue>self.cfg.overdue_threshold):
                raise Problem(503, 'CLEANUP_UNHEALTHY')
            user = c.execute('SELECT * FROM users WHERE profile=? AND active_lease IS NULL AND quarantined=0 ORDER BY id LIMIT 1', (profile,)).fetchone()
            if not user:
                raise Problem(429, 'POOL_EXHAUSTED')
            user_id = user['id']
            c.execute('UPDATE users SET active_lease=?,lock_version=lock_version+1 WHERE id=?', (lease_id,user_id))
            display = self.cfg.deployment_id + '/' + lease_id
            c.execute('''INSERT INTO leases(id,deployment,issuer,subject,profile,profile_version,user_id,
 display_name,state,requested_ttl,created_at,request_hash,idem,client_request_id,correlation_id)
 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (lease_id,self.cfg.deployment_id,owner['issuer'],owner['subject'],profile,
                       self.cfg.profiles[profile]['version'],user_id,display,'PREPARING',ttl,time.time(),digest,
                       idem,body.get('client_request_id'),correlation))
            self.db.audit(c,lease_id,'RESERVED',correlation)
        try:
            if self.cfg.mode=='oci':self.cloud.validate_user(user_id)
            if self.cloud.list_keys(user_id):
                with self.db.tx() as c:
                    c.execute('UPDATE users SET quarantined=1 WHERE id=?', (user_id,))
                raise RuntimeError('UNEXPECTED_KEYS')
            key = self.cloud.create_key(user_id, display)
            expires = key['created'] + ttl
            with self.db.tx() as c:
                r = dict(c.execute('SELECT * FROM leases WHERE id=?', (lease_id,)).fetchone())
                if r['state'] != 'PREPARING':
                    raise RuntimeError('LEASE_NO_LONGER_PREPARING')
                c.execute("UPDATE leases SET key_id=?,key_created_at=?,expires_at=?,state='SCHEDULE_PENDING' WHERE id=?", (key['id'],key['created'],expires,lease_id))
                c.execute('INSERT OR IGNORE INTO outbox(id,lease_id,generation,operation) VALUES(?,?,1,?)',
                          (lease_id+':1:setup',lease_id,'SETUP'))
                self.db.audit(c,lease_id,'KEY_RECORDED',correlation)
                if defer_setup:
                    if expires<=time.time():raise RuntimeError('EXPIRED_DURING_SETUP')
                    c.execute('UPDATE leases SET delivered_at=? WHERE id=?',(time.time(),lease_id))
                    self.db.audit(c,lease_id,'DELIVERED_SETUP_PENDING',correlation)
            payload = {'lease_id':lease_id,'status':'SCHEDULE_PENDING','access_key_id':key['id'],
                       'secret_access_key':key['secret'],'endpoint':endpoint,'region':self.cfg.region,
                       'scope_profile':profile,'expires_at':utc(expires),'expiry_mode':'managed_key_deletion'}
            if defer_setup:
                # The durable SETUP outbox and pending lease survive process loss.
                # No plaintext secret is persisted; a lost finalizer means no replay
                # and the existing stale-pending reconciler revokes the key.
                return 201,payload
            return 201,self.finish_issuance(payload,correlation)
        except Exception as exc:
            self.compensate(lease_id,correlation,exc)
            raise Problem(503,'ISSUANCE_COMPENSATED',lease_id) from None

    def compensate(self, lease_id, correlation, exc):
        import re
        failure=type(exc).__name__
        if isinstance(exc,(RuntimeError,TimeoutError)) and re.fullmatch(r'[A-Z_]{3,80}',str(exc)):
            failure+=':'+str(exc)
        with self.db.tx() as c:
            c.execute("UPDATE leases SET state='REVOKE_PENDING',revoke_intent=1,last_error=? WHERE id=? AND state!='REVOKED'",(failure,lease_id))
            self.db.audit(c,lease_id,'COMPENSATION_REQUIRED',correlation,failure)
        try:self.delete(lease_id,1)
        except Exception:pass  # Durable pending state is retried by independent reconciliation.

    def finish_issuance(self, payload, correlation):
        lease_id=payload['lease_id']
        if not self.setup(lease_id,1):raise RuntimeError('LEASE_NO_LONGER_PENDING')
        row=self.db.lease(lease_id)
        if row['state']!='SCHEDULE_PENDING' or row['expires_at']<=time.time():
            raise RuntimeError('EXPIRED_DURING_SETUP')
        complete={**payload,'status':'ACTIVE'}
        until=min(time.time()+self.cfg.replay_seconds,row['expires_at'])
        ref=self.cloud.store_response(complete,until)
        accepted=False
        try:
            with self.db.tx() as c:
                r=c.execute('SELECT * FROM leases WHERE id=?',(lease_id,)).fetchone()
                if time.time()>=r['expires_at'] or r['state']!='SCHEDULE_PENDING' or not r['schedule_id']:
                    raise RuntimeError('EXPIRED_DURING_SETUP')
                c.execute("UPDATE leases SET replay_ref=?,replay_until=?,state='ACTIVE',delivered_at=COALESCE(delivered_at,?) WHERE id=?",(ref,until,time.time(),lease_id))
                self.db.audit(c,lease_id,'ISSUANCE_FINALIZED' if r['delivered_at'] else 'DELIVERED',correlation)
            accepted=True
        finally:
            if not accepted:self.cloud.remove_response(ref)
        return complete

    def finish_in_background(self, payload, correlation):
        try:self.finish_issuance(payload,correlation)
        except Exception as exc:self.compensate(payload['lease_id'],correlation,exc)
        finally:payload.clear()

    def setup(self, lease_id, generation):
        self.cloud.fault('schedule')
        with self.db.tx() as c:
            row = c.execute('SELECT * FROM leases WHERE id=?',(lease_id,)).fetchone()
            if not row or row['deployment']!=self.cfg.deployment_id or row['generation']!=generation or row['state'] in ('REVOKED','REVOKE_PENDING'):
                return False
            if not row['key_id'] or not row['expires_at']:
                raise RuntimeError('RETRY_KEY_NOT_PERSISTED')
            if row['expires_at'] <= time.time():
                raise RuntimeError('LEASE_ALREADY_DUE')
            schedule = self.cloud.ensure_schedule(lease_id,generation,row['expires_at']) if self.cfg.mode=='oci' else f'{lease_id}:{generation}'
            c.execute("INSERT OR IGNORE INTO schedules VALUES(?,?,?,?,'ACTIVE')",(schedule,lease_id,generation,math.ceil(row['expires_at'])))
            c.execute('UPDATE leases SET schedule_id=? WHERE id=?',(schedule,lease_id))
            c.execute("DELETE FROM outbox WHERE lease_id=? AND generation=? AND operation='SETUP'",(lease_id,generation))
            self.db.audit(c,lease_id,'SCHEDULE_CONFIRMED')
            return True

    def event(self, event):
        # Event fields carry correlation hints only. TTL, user/key authority come from ledger.
        if event.get('eventType','').lower() != 'com.oraclecloud.identitycontrolplane.createcustomersecretkey':
            return 'IGNORED'
        data = event.get('data',{})
        user = data.get('additionalDetails',{}).get('userId')
        with self.db.tx() as c:
            if c.execute('SELECT 1 FROM events WHERE id=?',(event['eventID'],)).fetchone():
                return 'DUPLICATE'
            if self.cfg.mode=='oci' and c.execute('SELECT id FROM users WHERE id=?',(user,)).fetchone():
                fixture={'eventID':event['eventID'],'eventType':event['eventType'],
                         'data':{'resourceId':data.get('resourceId'),'additionalDetails':{'userId':user}}}
                self.db.audit(c,None,'EVENT_RECEIVED',detail=json.dumps(fixture,separators=(',',':')))
            r = c.execute("SELECT l.* FROM leases l JOIN users u ON l.user_id=u.id WHERE u.id=? AND l.state IN ('PREPARING','SCHEDULE_PENDING','ACTIVE') ORDER BY l.created_at DESC LIMIT 1",(user,)).fetchone()
            if not r or r['deployment']!=self.cfg.deployment_id:
                return 'IGNORED'
            lease_id,generation = r['id'],r['generation']
        self.setup(lease_id,generation)  # retryable before persistence; do not ack on failure
        with self.db.tx() as c:
            c.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)',(event['eventID'],lease_id,time.time()))
        return 'CONFIRMED'

    def delete(self, lease_id, generation):
        with self.db.tx() as c:
            r = c.execute('SELECT * FROM leases WHERE id=?',(lease_id,)).fetchone()
            if not r or r['deployment']!=self.cfg.deployment_id or r['generation']!=generation:
                return False
            if r['state']=='REVOKED':
                return True
            if not r['revoke_intent'] and (r['expires_at'] is None or time.time()<r['expires_at']):
                return False
            c.execute("UPDATE leases SET state='REVOKE_PENDING' WHERE id=?",(lease_id,))
            try:
                keys = self.cloud.list_keys(r['user_id'])
                owned = [k for k in keys if k['display_name']==r['display_name']]
                foreign = [k for k in keys if k['display_name']!=r['display_name']]
                if foreign:
                    c.execute('UPDATE users SET quarantined=1 WHERE id=?',(r['user_id'],))
                    self.db.audit(c,lease_id,'QUARANTINED_FOREIGN_KEY')
                # Lost creation reply: reconcile display name rather than create a replacement.
                if r['key_id'] and any(k['id']==r['key_id'] and k['display_name']!=r['display_name'] for k in keys):
                    raise RuntimeError('KEY_OWNERSHIP_MISMATCH')
                for key in owned:
                    if not self.cloud.delete_key(r['user_id'],key['id']):
                        raise RuntimeError('DELETE_NOT_CONFIRMED')
                    if not r['key_id']:
                        c.execute('UPDATE leases SET key_id=?,key_created_at=?,expires_at=? WHERE id=?',
                                  (key['id'],key['created'],key['created']+r['requested_ttl'],lease_id))
                if any(k['display_name']==r['display_name'] for k in self.cloud.list_keys(r['user_id'])):
                    raise RuntimeError('DELETE_NOT_CONFIRMED')
                self.cloud.remove_response(r['replay_ref'])
                if self.cfg.mode=='oci' and r['schedule_id']:
                    self.cloud.remove_schedule(r['schedule_id'])
                c.execute("UPDATE leases SET state='REVOKED',deleted_at=?,replay_ref=NULL,last_error=NULL WHERE id=?",(time.time(),lease_id))
                c.execute('UPDATE users SET active_lease=NULL,lock_version=lock_version+1 WHERE id=? AND active_lease=?',(r['user_id'],lease_id))
                c.execute('DELETE FROM schedules WHERE lease_id=?',(lease_id,))
                c.execute('DELETE FROM outbox WHERE lease_id=?',(lease_id,))
                self.db.audit(c,lease_id,'REVOKED',detail=self.cfg.workload if self.cfg.mode=='oci' else 'mock')
                return True
            except Exception as exc:
                attempts = r['retries']+1
                delay = min(60,2**min(attempts,6)) + random.uniform(0,1)
                c.execute('UPDATE leases SET retries=?,next_retry=?,last_error=? WHERE id=?',
                          (attempts,time.time()+delay,type(exc).__name__,lease_id))
                self.db.audit(c,lease_id,'DELETE_RETRY',detail=type(exc).__name__)
                return False

    def revoke(self, lease_id, owner):
        r = self.owned(lease_id,owner)
        if r['state']=='REVOKED':
            return 204
        with self.db.tx() as c:
            c.execute("UPDATE leases SET revoke_intent=1,state='REVOKE_PENDING' WHERE id=? AND state!='REVOKED'",(lease_id,))
            self.db.audit(c,lease_id,'EARLY_REVOKE_REQUESTED')
        self.delete(lease_id,r['generation'])
        return 202

    def reconcile(self):
        now = time.time()
        cleanup_errors=[]
        try:self.cloud.purge()
        except Exception as exc:
            cleanup_errors.append(type(exc).__name__)
            with self.db.tx() as c:self.db.audit(c,None,'REPLAY_CLEANUP_RETRY',detail=type(exc).__name__)
        with self.db.tx() as c:
            c.execute('UPDATE leases SET replay_ref=NULL WHERE replay_until<=?',(now,))
            # A crashed or ambiguous create may have left a key outside ledger persistence.
            c.execute("UPDATE leases SET state='REVOKE_PENDING',revoke_intent=1 WHERE state='PREPARING' AND created_at<?",(now-30,))
            c.execute("UPDATE leases SET state='REVOKE_PENDING',revoke_intent=1 WHERE state='SCHEDULE_PENDING' AND created_at<?",(now-60,))
            rows = [dict(r) for r in c.execute("SELECT * FROM leases WHERE state!='REVOKED'")]
        for r in rows:
            if r['next_retry']>now:
                continue
            if r['state']=='REVOKE_PENDING' or (r['expires_at'] and r['expires_at']<=now):
                self.delete(r['id'],r['generation'])
            elif r['state'] in ('ACTIVE','SCHEDULE_PENDING'):
                with self.db.connect() as c:
                    exists = c.execute("SELECT 1 FROM schedules WHERE lease_id=? AND generation=? AND state='ACTIVE'",(r['id'],r['generation'])).fetchone()
                if not exists:
                    try:
                        self.setup(r['id'],r['generation'])
                    except Exception:
                        with self.db.tx() as c:
                            c.execute('UPDATE leases SET retries=retries+1,next_retry=?,last_error=? WHERE id=?',
                                      (now+5,'SCHEDULE_REPAIR_FAILED',r['id']))
        # Remote IAM calls must not hold the database writer lock: a slow service
        # must not stall another host's deadline-driven deletion or reservation.
        with self.db.connect() as c:
            users=[dict(r) for r in c.execute('SELECT * FROM users')]
        for user in users:
            identity_drift=False
            if self.cfg.mode=='oci':
                try:self.cloud.validate_user(user['id'])
                except PermissionError:identity_drift=True
                except Exception as exc:cleanup_errors.append(type(exc).__name__)
            snapshot_at=time.time()
            try:keys=self.cloud.list_keys(user['id'])
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__);keys=[]
            with self.db.tx() as c:
                if identity_drift:
                    c.execute('UPDATE users SET quarantined=1 WHERE id=?',(user['id'],))
                    c.execute("UPDATE leases SET revoke_intent=1,state='REVOKE_PENDING' WHERE user_id=? AND deployment=? AND state!='REVOKED'",(user['id'],self.cfg.deployment_id))
                    if not user['quarantined']:self.db.audit(c,None,'QUARANTINED_IDENTITY_DRIFT',detail=user['id'])
                for key in keys:
                    known=c.execute("SELECT 1 FROM leases WHERE display_name=? AND user_id=? AND deployment=? AND key_id=? AND (state!='REVOKED' OR deleted_at>=?)",
                        (key['display_name'],user['id'],self.cfg.deployment_id,key['id'],snapshot_at)).fetchone()
                    if not known:
                        pending=c.execute("SELECT 1 FROM leases WHERE display_name=? AND user_id=? AND state IN ('PREPARING','REVOKE_PENDING')",
                            (key['display_name'],user['id'])).fetchone()
                        if not pending:
                            late=c.execute("SELECT * FROM leases WHERE display_name=? AND user_id=? AND deployment=? AND state='REVOKED'",
                                (key['display_name'],user['id'],self.cfg.deployment_id)).fetchone()
                            if late and (not late['key_id'] or late['key_id']==key['id']):
                                # A timed-out create can become visible after the first
                                # absence check. Reopen its own lease and retry deletion;
                                # a different recorded key ID remains a foreign-key alarm.
                                c.execute("UPDATE leases SET state='REVOKE_PENDING',revoke_intent=1,key_id=?,key_created_at=COALESCE(key_created_at,?),expires_at=COALESCE(expires_at,?),deleted_at=NULL,next_retry=0,last_error='LATE_KEY_RECOVERY' WHERE id=?",
                                    (key['id'],key['created'],key['created']+late['requested_ttl'],late['id']))
                                self.db.audit(c,late['id'],'LATE_KEY_RECOVERY',detail=user['id'])
                            c.execute('UPDATE users SET quarantined=1 WHERE id=?',(user['id'],))
                            if not user['quarantined']:self.db.audit(c,None,'QUARANTINED_ORPHAN',detail=user['id'])
        with self.db.tx() as c:
            c.execute("UPDATE control SET value=? WHERE id='cleanup_error'",('1' if cleanup_errors else '0',))
            c.execute("INSERT INTO control VALUES('heartbeat',?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",(str(time.time()),))
            c.execute('DELETE FROM audit WHERE at<?',(now-self.cfg.audit_retention_days*86400,))
        if self.cfg.mode=='oci':
            try:self.cloud.publish_metrics(self.metrics())
            except Exception as exc:
                with self.db.tx() as c:self.db.audit(c,None,'METRICS_PUBLISH_RETRY',detail=type(exc).__name__)

    def pause(self, paused=True):
        with self.db.tx() as c:
            c.execute("UPDATE control SET value=? WHERE id='paused'",('1' if paused else '0',))
            self.db.audit(c,None,'ISSUANCE_PAUSED' if paused else 'ISSUANCE_RESUMED')

    def metrics(self):
        with self.db.connect() as c:
            states = {r[0]:r[1] for r in c.execute('SELECT state,COUNT(*) FROM leases GROUP BY state')}
            pools = [dict(r) for r in c.execute('SELECT profile,COUNT(*) capacity,SUM(CASE WHEN active_lease IS NOT NULL THEN 1 ELSE 0 END) allocated,SUM(quarantined) quarantined FROM users GROUP BY profile')]
            hb = c.execute("SELECT value FROM control WHERE id='heartbeat'").fetchone()
            overdue = c.execute("SELECT COUNT(*) FROM leases WHERE state!='REVOKED' AND expires_at<?",(time.time(),)).fetchone()[0]
        return {'states':states,'pools':pools,'overdue':overdue,'heartbeat_at':utc(float(hb[0])) if hb else None,'healthy':self.healthy()}
