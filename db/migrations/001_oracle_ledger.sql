-- Production design artifact; NOT automatically provisioned by the mock release.
-- Execute only as the dedicated application schema after M0 and ADB bootstrap implementation.
-- Credentials must be in Vault, not in SQL, Terraform outputs or lease rows.
CREATE TABLE lcm_users (
 user_id VARCHAR2(255) PRIMARY KEY, profile VARCHAR2(80) NOT NULL,
 active_lease VARCHAR2(36) UNIQUE, quarantined NUMBER(1) DEFAULT 0 NOT NULL,
 lock_version NUMBER DEFAULT 0 NOT NULL
);
CREATE TABLE lcm_leases (
 lease_id VARCHAR2(36) PRIMARY KEY, deployment_id VARCHAR2(100) NOT NULL,
 owner_issuer VARCHAR2(512) NOT NULL, owner_subject VARCHAR2(255) NOT NULL,
 profile VARCHAR2(80) NOT NULL, profile_version NUMBER NOT NULL,
 user_id VARCHAR2(255) REFERENCES lcm_users(user_id), key_id VARCHAR2(255),
 display_name VARCHAR2(255) UNIQUE NOT NULL, generation NUMBER DEFAULT 1 NOT NULL,
 state VARCHAR2(24) NOT NULL CHECK(state IN ('PREPARING','SCHEDULE_PENDING','ACTIVE','REVOKE_PENDING','REVOKED')),
 requested_ttl NUMBER NOT NULL CHECK(requested_ttl BETWEEN 300 AND 3600),
 created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 key_created_at TIMESTAMP WITH TIME ZONE, expires_at TIMESTAMP WITH TIME ZONE,
 delivered_at TIMESTAMP WITH TIME ZONE, deleted_at TIMESTAMP WITH TIME ZONE,
 schedule_id VARCHAR2(255), replay_secret_ref VARCHAR2(255), replay_until TIMESTAMP WITH TIME ZONE,
 idempotency_key VARCHAR2(36) NOT NULL, request_hash VARCHAR2(64) NOT NULL,
 client_request_id VARCHAR2(120), revoke_intent NUMBER(1) DEFAULT 0 NOT NULL,
 retries NUMBER DEFAULT 0 NOT NULL, next_retry TIMESTAMP WITH TIME ZONE,
 last_error VARCHAR2(100), correlation_id VARCHAR2(36) NOT NULL,
 CONSTRAINT lcm_idempotency UNIQUE(owner_issuer,owner_subject,idempotency_key)
);
CREATE INDEX lcm_cleanup_idx ON lcm_leases(state,expires_at,next_retry);
CREATE TABLE lcm_outbox (
 operation_id VARCHAR2(100) PRIMARY KEY,
 lease_id VARCHAR2(36) REFERENCES lcm_leases(lease_id), generation NUMBER NOT NULL,
 operation VARCHAR2(40) NOT NULL, attempts NUMBER DEFAULT 0 NOT NULL,
 next_retry TIMESTAMP WITH TIME ZONE,
 CONSTRAINT lcm_outbox_unique UNIQUE(lease_id,generation,operation)
);
CREATE TABLE lcm_audit (
 audit_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 observed_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 lease_id VARCHAR2(36), action VARCHAR2(80) NOT NULL,
 correlation_id VARCHAR2(36), redacted_detail VARCHAR2(2000)
);
-- Reservation implementation must SELECT ... FOR UPDATE SKIP LOCKED in one transaction.
-- Apply migrations with backward-compatible additive changes while deletion workers continue.
