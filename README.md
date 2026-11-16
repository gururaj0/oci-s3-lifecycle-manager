# OCI S3 Credential Lifecycle Manager

**0.2.0 live integration demo** adds real workload-principal IAM Customer Secret Keys,
a private Autonomous Database ledger, Vault replay storage, OCI Functions, Resource Scheduler,
and external cleanup alarms. The previous public mock URL now redirects to the live deployment.
See [live deployment and evidence](docs/live-deployment.md) for current acceptance results.
Actual Unity/query-engine integration remains outstanding; production readiness stays false.

See [deployed demo and evidence](docs/deployment.md), [acceptance matrix](docs/acceptance.md),
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

The original mock release remains available for local use. Its former public URL redirects
to live OCI, including API requests; anonymous synthetic credential minting is no longer public.

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

## Deploy the original mock to OCI

The current mock release supports **existing-network mode** and public/private gateway choice.
Use your OCI CLI default profile and home region. The deployment helper independently resolves and
checks the active `Demo` compartment before creation. SSH uses existing public DemoBastion and the
operator's supplied local key; the new VM has no public IP or unrestricted SSH.

1. Run `python scripts/preflight.py` and record the verified compartment OCID/home region.
2. Configure `.local/stack-variables.json` with the variables in `infra/variables.tf`: verified placement,
   existing VCN/gateway subnet/NAT route table, unused private subnet CIDR, image OCID, bastion private
   IP and **public** SSH key. Never put a private key in variables or state.
3. Build `python scripts/package.py`. Upload `release/oci-s3-lcm-0.1.0-mock.zip` to a new Resource
   Manager stack, Terraform 1.5.x, review its form/costs and run Apply. The supplied deployment helper
   `python scripts/deploy_oci.py` performs the same authenticated stack create/apply using OCI CLI.
4. Check the readiness output; an apply succeeds before application readiness is guaranteed.
5. Run `python scripts/smoke.py https://<gateway-host> --expiry --performance 100`.

The archive embeds the versioned source and pinned runtime lock in compressed cloud-init, verified
by SHA-256. VM bootstrap installs it and starts API plus independent cleanup without local Docker,
Fn CLI or manual image pushes. Runtime uses a dedicated OS user and systemd hardening. Billable
resources: one E5 Flex 1-OCPU/8-GB VM with 50-GB boot disk and API Gateway. Existing NAT/VCN/bastion
are reused without modifications. Drain before destroying or replacing the stack.

The public Deploy-to-Oracle-Cloud URL requires an externally hosted supported archive source.
This local repository has not been published. Use the authenticated ZIP-upload stack flow above;
[release/README.md](release/README.md) documents constructing the button when a release URL exists.
No unusable public button or fabricated release URL is supplied.

## Repository

`shared/` lifecycle plus mock and OCI adapters; `service/` API/JWT/UI; `functions/live_handler.py`
deployed setup/deletion/reconciliation handler; `infra/` original mock stack; `infra/live/` live
Resource Manager stack and automatic private-artifact bootstrap; `db/migrations/` applied Oracle
schema; `wrapper/` reference adapter; `tests/` local contracts; `scripts/` build, deployment,
acceptance and operations; `release/` pinned live manifest; `docs/` redacted evidence.

The live integrations, private ADB/Vault network, scanned OCI image build, Functions, schedules,
external alarms and private Resource Manager template are implemented. Actual Unity integration,
full live fault/load acceptance and the PRD service targets remain gates. See the current
[acceptance matrix](docs/acceptance.md) before making production-support claims.
