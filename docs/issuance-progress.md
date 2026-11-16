# Immediate credentials with background finalization

The live UI sends `delivery_mode: "immediate"` when creating a lease. The backend validates
identity, profile, cleanup health and pool capacity, creates the IAM key, and commits its key ID,
expiry, ownership and SETUP outbox record to ADB. It then returns HTTP 201 with the actual
credentials and `status: "SCHEDULE_PENDING"`. This still takes the time required by IAM and ADB;
it is not a zero-latency guarantee or an assertion that S3 propagation has completed.

The **Lease credentials · REAL OCI** panel appears above **Your leases** immediately and displays
the masked secret as soon as that response arrives. It labels cleanup setup as pending. The
existing lease refresh updates the panel on completion and clears the displayed secret if
revocation is pending or complete. Dismissing the panel does not cancel the backend request.

After sending the response, a server background task confirms the expiration schedule and stores
the bounded replay response in Vault, then marks the lease ACTIVE. Failure records compensation
and attempts key deletion; failed deletions remain discoverable for reconciliation. The existing
reconciler revokes SCHEDULE_PENDING records older than 60 seconds on its next successful pass.
This covers a lost process/finalizer even if it created a schedule before crashing. A durable
outbox records setup intent, not plaintext credentials. No schema or IAM grants were added.

**Changed guarantee:** immediate mode exposes a usable key before schedule confirmation and Vault
replay storage. A process loss can lose the in-memory secret and prevent replay, and cleanup is
subject to reconciliation availability. Clients must handle early revocation. Customer Secret
Key propagation and delayed revocation remain separate limitations; this is not native STS expiry.

API clients default to `delivery_mode: "confirmed"`, retaining the prior synchronous behavior.
Immediate mode is explicit and part of the idempotency request hash. A retry while finalization
is pending returns ISSUANCE_PENDING, never a second key. Once finalized, same-key retries can
replay the ACTIVE response within the bounded window. Metadata/progress never includes secrets.
A failed/unreplayable request must be inspected and revoked before requesting a replacement.

`GET /v1/lease-requests/{request_id}` uses the create request's UUID Idempotency-Key, requiring the
same owner and an authorized profile. A 404 can mean reservation has not committed yet. Phases:

- PREPARING: reserve a service user and create the credential.
- CONFIRMING_SCHEDULE: key recorded; expiration scheduling pending.
- PREPARING_REPLAY: schedule confirmed; secure retry storage pending.
- ACTIVE: scheduling and secure retry storage complete.
- REVOKE_PENDING / REVOKED: cleanup after revocation, expiry or setup failure.

Validation: 97 tests passed, including response-before-background ordering, background schedule
and Vault failure compensation, abandoned setup recovery, retry deduplication, revocation races,
owner/profile restrictions and secret redaction. Browser fixtures cover pending-secret display,
completion, revocation clearing, dismissal and desktop/mobile layout. Real OCI timing and cleanup
results are recorded separately in `immediate-issuance-live-evidence.json`.

Live OCI verification: credentials returned in **1.50 seconds**, with CONFIRMING_SCHEDULE
observed at 1.65 seconds, PREPARING_REPLAY at 39.52 seconds and ACTIVE at 54.25 seconds.
The same-key replay succeeded after finalization and only the test lease was revoked.
These are observed timings for one test, not service guarantees. Deployed assets and OpenAPI
match the tested files.
