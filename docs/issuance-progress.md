# Immediate credential panel and issuance progress

Clicking Create now opens **Lease credentials · REAL OCI** immediately, above **Your leases**,
and scrolls it into view. Existing credential fields are cleared. The panel shows elapsed time
and polls committed request progress every two seconds; it does not invent a percentage or ETA.

`GET /v1/lease-requests/{request_id}` uses the UUID supplied as the create request's
`Idempotency-Key`. It requires the same owner and an authorized profile. A 404 can mean the
reservation is not committed yet. The response contains safe lease metadata and one phase:

- PREPARING: reserve a service user and create the credential.
- CONFIRMING_SCHEDULE: key creation is recorded; expiration scheduling is pending.
- PREPARING_REPLAY: scheduling is confirmed; bounded secure retry storage is being prepared.
- ACTIVE: issuance is committed. The credential is delivered only by the create response.
- REVOKE_PENDING / REVOKED: cleanup after revocation, expiry or an issuance failure.

The panel reveals the masked secret and S3 test controls only after POST /v1/leases succeeds.
Dismissing/clearing a pending panel prevents a late response from redisplaying credentials; it
does not cancel the backend request. Issuance failures retain the visible status and direct the
operator to inspect the lease list before creating another request.

This is an earlier UI/progress display, not a claim that issuance latency has decreased. Key
creation, schedule confirmation, replay storage, ownership, idempotency and cleanup guarantees
are unchanged. Customer Secret Key propagation and delayed revocation remain separate limitations.

Validation: 88 unit tests passed, including progress ownership, profile scope, UUID case and
secret redaction. A delayed-response browser fixture verifies panel ordering, immediate display,
stages, elapsed time, masking, dismissal and desktop/mobile layout. Live evidence is recorded in
`issuance-progress-live-evidence.json` after one test issuance, revoking only that test's lease.

Live verification passed: CONFIRMING_SCHEDULE was observed at 2.41 seconds,
PREPARING_REPLAY at 23.45 seconds, and the create response at 42.28 seconds.
The test lease was revoked successfully; anonymous progress requests were rejected.
Published HTML, JavaScript and OpenAPI match the tested local files.
