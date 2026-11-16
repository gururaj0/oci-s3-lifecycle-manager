# OCI S3 Credential Lifecycle Manager

**0.2.0 live integration demo** adds real workload-principal IAM Customer Secret Keys,
a private Autonomous Database ledger, Vault replay storage, OCI Functions, Resource Scheduler,
and external cleanup alarms. The obsolete public mock deployment has been retired.
See [live deployment and evidence](docs/live-deployment.md) for current acceptance results.
Actual Unity/query-engine integration remains outstanding; production readiness stays false.

See [cleanup record](docs/cleanup.md), [acceptance matrix](docs/acceptance.md),
[capability decisions](docs/decisions.md), [threat model](docs/threat-model.md) and
[operations/drain runbook](docs/operations.md).

## Live demo

[Open the live OCI demo](https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com/).
Use **Sign in with OCI** in the live UI with the approved Default-domain operator account.
The UI simulates the Unity connector: the backend obtains an Identity Domains client-credentials
JWT and keeps OAuth secrets in Vault. The browser receives an HttpOnly session cookie.
For direct API testing, `python scripts/identity_demo_token.py` obtains a service token through
DemoBastion; treat its stdout as a secret. The old demo-signed JWT is no longer accepted.
See [Identity Domains authentication](docs/identity-domain-auth.md) and
[gateway JWT validation / backend authorization](docs/gateway-auth.md).

The timed expiry acceptance test failed: signed S3 reads still succeeded 237.995 seconds after
the deadline despite Function revocation and confirmed IAM key absence. Issuance samples also
exceeded 15 seconds; a 100-request p95 run has not been completed. See the evidence before relying
on lease deadlines. Real-credential issuance is enabled for demo testing at the user’s request. The UI displays
the failed expiry target explicitly. This deployment remains a test environment with
`production_ready:false`; its requested deadlines are not hard access cutoffs.

The live UI includes pool health, service-user/key-count inventory, lease countdowns,
owner-bound activity, and real S3 fixture checks for read/list/write, delete denial and foreign
bucket denial. Create a lease to reveal its test controls; keep its credentials in memory to
repeat a check after revocation. Customer Secret Keys appear under the listed service user,
not your personal OCI user.

## Original mock demo

The mock remains available for local development and unit tests. Its former OCI deployment,
custom issuer and redirect URL have been retired. Use the live URL above.

The browser opens a signed synthetic session, lets you request a READ or READ_WRITE profile,
issues a distinct fake key from a two-user pool, and tests AWS Signature V4 reads/writes and denials.
Revoke a lease and repeat the S3 request to observe denial. Choose a five-minute lease to observe
expiry. Fake credentials stay in browser memory and are masked by default. No AWS session token
is returned. Expiry means managed key deletion; the S3 endpoint does not enforce the response timestamp.

The ledger survives VM service restarts. Exclusive database writer transactions serialize reservations
and owner-bound idempotency. Duplicate retries never create replacement keys. Key-creation unknown
outcomes reconcile by unique display name. Partial failures compensate and retain REVOKE_PENDING
until confirmed deletion. Foreign keys quarantine a user and are never deleted to free capacity.

## Local development

Python 3.11+ (deployed: Python 3.12). Run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-runtime.lock -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn service.main:app --host 127.0.0.1 --port 8080 --no-access-log
```

Open `http://127.0.0.1:8080` and `/docs`. Do not bind a developer server publicly. Config is optional
JSON via `LCM_CONFIG`; runtime state uses `LCM_STATE_DIR` (default `.local`). Live issuance is gated on recorded capability checks.

## REST

`POST /v1/leases` requires `Authorization: Bearer <RS256 JWT>` and UUID `Idempotency-Key`, with
`{"scope_profile":"sales-read","ttl_seconds":900,"client_request_id":"query-123"}`. TTL is 300–3600
seconds. Initial issuance is 201, authorized bounded replay is 200. After 60 seconds, replays return
409 CREDENTIALS_NOT_REPLAYABLE and the lease ID. Renewal creates a new lease, never extends an old key.

`GET /v1/leases/{id}` returns owner-only metadata; `DELETE` requests early revocation (202; repeated
confirmed deletion 204). `/v1/config` discovers authorized profiles. `/health/live` and `/health/ready`
return redacted health. The generated [OpenAPI spec](openapi/openapi.json) and [reference wrapper](wrapper/client.py)
are included. `/demo/*` and `/mock-s3/*` are intentionally synthetic demo surfaces.

## Deploy to OCI

Use `infra/live/`, `scripts/live_stack.py` and the private Resource Manager package described
in [release instructions](release/README.md). The retired mock deployment assets are available
only in Git history. Existing DemoBastion and the operator's existing SSH key remain in use.

## Repository

`shared/` lifecycle plus mock and OCI adapters; `service/` API/JWT/UI; `functions/live_handler.py`
deployed setup/deletion/reconciliation handler; `infra/live/` live
Resource Manager stack and automatic private-artifact bootstrap; `db/migrations/` applied Oracle
schema; `wrapper/` reference adapter; `tests/` local contracts; `scripts/` build, deployment,
acceptance and operations; `release/` pinned live manifest; `docs/` redacted evidence.

The live integrations, private ADB/Vault network, scanned OCI image build, Functions, schedules,
external alarms and private Resource Manager template are implemented. Actual Unity integration,
full live fault/load acceptance and the PRD service targets remain gates. See the current
[acceptance matrix](docs/acceptance.md) before making production-support claims.
