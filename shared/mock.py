"""Explicit fake control plane: never creates an OCI user, key, secret, event or schedule.
Provider state is separate from the ledger so a lost create reply is a genuine saga failure.
"""
import json
import secrets
import sqlite3
import time
import uuid
from cryptography.fernet import Fernet
from shared.auth import private_file

class MockCloud:
    def __init__(self, cfg):
        self.cfg=cfg
        self.path = cfg.state_dir / 'mock-cloud.db'
        self.fernet = Fernet(private_file(cfg.state_dir / 'mock-vault.key', Fernet.generate_key()))
        self.db=None
        self.fail = {}  # deterministic in-process fault injection, never exposed by HTTP
        with self.connect() as c:
            c.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS keys(id TEXT PRIMARY KEY,user_id TEXT,display_name TEXT UNIQUE,created REAL,secret BLOB);
CREATE TABLE IF NOT EXISTS prefix_members(user_id TEXT PRIMARY KEY,group_id TEXT);
CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY,ciphertext BLOB,expires REAL);
''')
        self.path.chmod(0o600)

    @property
    def prefix_memberships(self):
        with self.connect() as c:return dict(c.execute('SELECT user_id,group_id FROM prefix_members'))

    def ensure_prefix_membership(self,lease,user):
        self.fault('prefix_provision')
        from shared.prefix_access import scope_name
        with self.db.tx() as c:
            grant=dict(c.execute('SELECT * FROM prefix_grants WHERE lease_id=?',(lease,)).fetchone())
            group=scope_name(self.cfg,grant)
            c.execute('UPDATE prefix_grants SET group_id=?,policy_id=? WHERE lease_id=?',(group,group+'-policy',lease))
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO prefix_members VALUES(?,?)',(user,group))
        self.fault('membership_after')
        with self.db.tx() as c:c.execute('UPDATE prefix_grants SET membership_id=? WHERE lease_id=?',(group+'-membership',lease))

    def remove_prefix_membership(self,grant,user):
        self.fault('membership_remove')
        with self.connect() as c:c.execute('DELETE FROM prefix_members WHERE user_id=?',(user,))

    def connect(self):
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def fault(self, action):
        n = self.fail.get(action, 0)
        if n:
            self.fail[action] = n - 1
            raise TimeoutError(action)

    def list_keys(self, user_id):
        self.fault('list')
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,user_id,display_name,created FROM keys WHERE user_id=?', (user_id,))]

    def create_key(self, user_id, display_name):
        self.fault('create_before')
        key_id = 'MOCK' + secrets.token_hex(12).upper()
        secret = secrets.token_urlsafe(32)
        created = time.time()
        with self.connect() as c:
            if c.execute('SELECT COUNT(*) FROM keys WHERE user_id=?', (user_id,)).fetchone()[0] >= 2:
                raise RuntimeError('MOCK_TWO_KEY_LIMIT')
            c.execute('INSERT INTO keys VALUES(?,?,?,?,?)',
                      (key_id, user_id, display_name, created, self.fernet.encrypt(secret.encode())))
        self.fault('create_after')
        return {'id': key_id, 'secret': secret, 'created': created}

    def delete_key(self, user_id, key_id):
        self.fault('delete')
        with self.connect() as c:
            c.execute('DELETE FROM keys WHERE id=? AND user_id=?', (key_id, user_id))
        # An absence check is part of successful deletion, including an absent-key retry.
        return not any(k['id'] == key_id for k in self.list_keys(user_id))

    def store_response(self, payload, expires):
        self.fault('vault')
        ref = str(uuid.uuid4())
        with self.connect() as c:
            c.execute('INSERT INTO secrets VALUES(?,?,?)',
                      (ref, self.fernet.encrypt(json.dumps(payload).encode()), expires))
        return ref

    def get_response(self, ref):
        with self.connect() as c:
            row = c.execute('SELECT * FROM secrets WHERE id=?', (ref,)).fetchone()
            if not row or row['expires'] <= time.time():
                return None
            return json.loads(self.fernet.decrypt(row['ciphertext']))

    def remove_response(self, ref):
        if ref:
            with self.connect() as c:
                c.execute('DELETE FROM secrets WHERE id=?', (ref,))

    def purge(self):
        with self.connect() as c:
            c.execute('DELETE FROM secrets WHERE expires<=?', (time.time(),))

    def credential(self, key_id):
        with self.connect() as c:
            r = c.execute('SELECT * FROM keys WHERE id=?', (key_id,)).fetchone()
            if r:
                return dict(r, secret=self.fernet.decrypt(r['secret']).decode())
