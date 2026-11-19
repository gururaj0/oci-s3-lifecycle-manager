# OCI S3 Credential Lifecycle Manager

**0.2.0 live integration demo** adds real workload-principal IAM Customer Secret Keys,
a private Autonomous Database ledger, Vault replay storage, OCI Functions, Resource Scheduler,
and external cleanup alarms. The obsolete public mock deployment has been retired.
See [live deployment and evidence](docs/live-deployment.md) for current acceptance results.
Actual Unity/query-engine integration remains outstanding; production readiness stays false.

See [cleanup record](docs/cleanup.md), [acceptance matrix](docs/acceptance.md),
[capability decisions](docs/decisions.md), [threat model](docs/threat-model.md) and
[operations/drain runbook](docs/operations.md).

## Dynamic prefix leases

The live UI requests a bucket, literal slash-terminated prefix, access mode and TTL.
`POST /v1/leases` accepts `bucket`, `prefix`, `operations`, `ttl_seconds` and
`delivery_mode: "immediate"`, with a Bearer token and persisted UUID Idempotency-Key.
For example: `{"bucket":"lcm-live-v2-sales","prefix":"quarterly/","operations":["GET","LIST"],"ttl_seconds":300,"delivery_mode":"immediate"}`.

The backend authorizes the bucket/operations against the connector's approved profiles and
scopes, then applies configured `allowed_prefixes` (default: all nonempty prefixes in that
approved bucket). It creates/reuses a deployment-owned prefix group and policy, allocates
an isolated user with no standing memberships, records the grant and adds membership,
then creates and returns an S3 key. Expiry removes the key and membership before reuse.
Group/policy resources remain for reuse, capped at 64 distinct scopes. Users are allocated
from a separate four-user pool; they are not created per request. New legacy profile-only
issuance is disabled when prefix mode is enabled; existing leases can still be revoked.

LIST grants bucket-level name inspection: prefix filters do not hide other object names.
Object content policies follow OCI pattern matching and propagation semantics. Grant and
revocation propagation must be measured; the old key-only benchmark is not a latency promise.
Live tests returned credentials in about 2.4–6.8 seconds, but S3 first accepted the grant
after approximately 261–321 seconds. A five-minute lease can therefore expire before it
becomes usable. The UI defaults to 15 minutes. These are observations, not latency or
revocation guarantees; key/membership absence in IAM does not prove immediate S3 denial.
The separate workload-identity `/v1/bucket-operations` API retains its existing bucket-level
contract. For current instructions use the published guide below; the PDF is a historical
snapshot from before dynamic prefix leases.

Deployment adds `003_prefix_grants.sql` to ADB, `prefix_access.tf` for isolated users and
scoped provisioning/cleanup permissions, and enables `prefix_enabled` with
`prefix_service_users` in runtime configuration. After provisioning, run
`scripts/harden_prefix_users.py` with the operator OCI configuration: the Terraform provider
does not disable the default database/OAuth credential capabilities. The runtime quarantines
users with any capability other than Customer Secret Keys. Run `scripts/prefix_live_smoke.py` to verify
real prefix GET/PUT isolation, early revocation, group reuse and TTL membership/key cleanup.

Identity Domains membership calls require the tested resource-scoped `GROUP_UPDATE` and
`USER_UPDATE` grants without operation-name filters. These remain restricted to deployment
prefix groups and the four dedicated users. They also permit metadata updates and, at the
IAM level, membership addition by cleanup workers; cleanup code only removes membership.
Key creation/deletion permissions remain separate. This tradeoff was explicitly approved.
Prefix cleanup uses the Identity Domains SCIM API to remove only the matched user from
the matched group, verifies both OCIDs, and confirms membership absence through IAM.

Validation: 127 automated tests pass. The unassisted live run passed all nine prefix
isolation/lifecycle checks, with IAM key and membership absence observed 6.48 seconds
after TTL. See [live evidence](docs/prefix-live-evidence.json) and
[negative API checks](docs/prefix-negative-evidence.json). The delete Function also
passed a separate cleanup test under its resource principal; see
[Function evidence](docs/prefix-function-evidence.json).

Live S3 diagnostics follow the returned lease prefix (including case). They default
to LIST for prefix leases, suggest returned object keys for GET, and reject GET keys
outside that prefix. For an empty prefix, a write lease can PUT its dedicated test
object and then GET it; read-only leases need an existing object to test GET.

## Diagnostic latency and asynchronous work

Background schedule setup and deletion serialize on a per-lease ADB mutex, rather than
holding the global writer mutex during OCI calls. Rate limiting and diagnostic audit
writes have separate mutexes. Pool reservation, idempotency, entitlement validation,
membership provisioning and durable key recording still finish before credential delivery.
Same-lease setup/deletion remain serialized across the API and Functions.

Scheduling and Vault replay preparation continue after immediate credential delivery.
Confirmed schedule setup is idempotent. Diagnostic audit writes run after the response
and can be lost if that process terminates; lifecycle audit writes remain durable.
Revocation returns 202 after recording intent and performs cleanup in the background,
with the reconciler recovering interrupted work. Poll until REVOKED before assuming
cleanup completed. The UI shows results before refreshing metrics.

Existing prefix scopes use stored group/policy OCIDs for direct lookup, while revalidating
ownership and the exact policy statements. A reused policy still requires a fresh user
membership and Customer Secret Key. Faster API responses do not guarantee immediate
S3 acceptance. Diagnostic results expose the exact S3 error code, S3 call duration,
backend duration and lease age; the UI also shows client round-trip time. Credential
responses include backend stage timings. None include OAuth token-generation time.

Run `scripts/async_latency_smoke.py` from the configured operator workspace to measure
OCI compute → API Gateway → backend, followed by direct S3 readiness checks. It uses
the existing DemoBastion connection, obtains OAuth before timing, and revokes/verifies
its temporary lease afterward. `--baseline` takes one S3 sample instead of polling.
The existing-policy live sample reduced diagnostic response time from 24.42 s to
0.42 s; credentials returned in 2.46 s. The same fresh key first succeeded on S3
at the 121-second poll, so fast issuance does not mean immediate readiness.
See [timing investigation](docs/async-performance.md).

## Live demo

[Unity implementer guide](https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com/unity) · [Interactive API reference](https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com/docs) · [OpenAPI JSON](https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com/openapi.json)

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

## Automatic bucket access

`POST /v1/bucket-operations` accepts a service JWT and `{"bucket":"lcm-live-v2-sales","operation":"LIST"}`.
The backend selects an authorized profile and uses its OCI workload identity for bounded
LIST/GET/PUT operations. No credential lease or expiration schedule is created for this path.
GET/PUT support objects up to 512 KiB; writes are conditional. The UI lists the first authorized
bucket after sign-in and provides read/create controls. See the Unity guide for the contract.

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
