"""Durable transactional ledger. SQLite is the demo backend; Oracle DDL is shipped separately.
SQLite BEGIN IMMEDIATE serializes writers across processes, including reservation/retry races.
No customer credential plaintext belongs in this database.
"""
from contextlib import contextmanager
import sqlite3
from pathlib import Path
import time

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, profile TEXT NOT NULL, active_lease TEXT UNIQUE,
 quarantined INTEGER NOT NULL DEFAULT 0, lock_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS leases (
 id TEXT PRIMARY KEY, deployment TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL,
 profile TEXT NOT NULL, profile_version INTEGER NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 key_id TEXT, display_name TEXT UNIQUE NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL, requested_ttl INTEGER NOT NULL, created_at REAL NOT NULL,
 key_created_at REAL, expires_at REAL, delivered_at REAL, deleted_at REAL,
 schedule_id TEXT, replay_ref TEXT, replay_until REAL,
 request_hash TEXT NOT NULL, idem TEXT NOT NULL, client_request_id TEXT,
 revoke_intent INTEGER NOT NULL DEFAULT 0, retries INTEGER NOT NULL DEFAULT 0,
 next_retry REAL NOT NULL DEFAULT 0, last_error TEXT, correlation_id TEXT NOT NULL,
 UNIQUE(issuer,subject,idem));
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, lease_id TEXT NOT NULL REFERENCES leases(id), generation INTEGER NOT NULL,
 operation TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_retry REAL NOT NULL DEFAULT 0,
 UNIQUE(lease_id,generation,operation));
CREATE TABLE IF NOT EXISTS schedules (
 id TEXT PRIMARY KEY, lease_id TEXT NOT NULL REFERENCES leases(id), generation INTEGER NOT NULL,
 due REAL NOT NULL, state TEXT NOT NULL, UNIQUE(lease_id,generation));
CREATE TABLE IF NOT EXISTS replay (id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS provider_keys (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL UNIQUE,
 created REAL NOT NULL, secret_ciphertext BLOB NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, lease_id TEXT, received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, lease_id TEXT, action TEXT NOT NULL,
 correlation_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS control (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rate_buckets (id TEXT PRIMARY KEY, minute INTEGER NOT NULL, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS objects (bucket TEXT, name TEXT, content BLOB, PRIMARY KEY(bucket,name));
CREATE TABLE IF NOT EXISTS prefix_grants (
 lease_id TEXT PRIMARY KEY REFERENCES leases(id), bucket TEXT NOT NULL, prefix TEXT NOT NULL,
 operations TEXT NOT NULL, group_id TEXT, policy_id TEXT, membership_id TEXT);
CREATE INDEX IF NOT EXISTS leases_cleanup ON leases(state,expires_at,next_retry);
'''

class Ledger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as c:
            c.executescript(SCHEMA)
        Path(path).chmod(0o600)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=30000')
        return c

    @contextmanager
    def tx(self, lock_key="writer_lock"):
        # SQLite retains its single-writer semantics; OCI uses scoped row locks.
        c = self.connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def audit(c, lease_id, action, correlation_id=None, detail=None):
        c.execute('INSERT INTO audit(at,lease_id,action,correlation_id,detail) VALUES(?,?,?,?,?)',
                  (time.time(), lease_id, action, correlation_id, detail))

    def lease(self, lease_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM leases WHERE id=?', (lease_id,)).fetchone()
            return dict(row) if row else None
