# Live OCI integration demo — 0.2.0

The live adapters are implemented. IAM, private Autonomous Database, Vault replay storage,
Functions, Resource Scheduler and external alarms are actual OCI resources. The existing
mock deployment is preserved separately. Production readiness remains false: actual Unity
wrapper/query-engine acceptance requires the integration team, and measured service targets
must be assessed against live results.

The authenticated live UI is at
[the live gateway](https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com/).
The original mock URL redirects to the live UI. Live mode has no anonymous token minting
or mock S3 endpoint. Use **Sign in with OCI** with the approved Default-domain operator account.
The backend obtains a separate Identity Domains service JWT for the Unity connector simulation.
Client secrets are stored in Vault; access tokens remain in backend memory. The old demo issuer
is no longer trusted. See [Identity Domains authentication](identity-domain-auth.md).

## Verified placement and access

The existing OCI CLI DEFAULT configuration supplies authentication and `us-ashburn-1`,
verified as the tenancy home region. The active compartment named **Demo** resolves to
`ocid1.compartment.oc1..aaaaaaaav6gvasmb4eyn6fnuuaoabal6mdfzuu4qxf3ll5kidwj24jxcn3dq`.
The deployment helper fetches this compartment again before every create/update. Terraform
also checks its name, state and home-region placement.

All SSH uses existing public **DemoBastion** with the user-supplied local key through
`.local/ssh-config`; no private SSH key or OCI user API key is copied to a runtime.
The live VM has private address `10.73.1.6` in a new DNS-enabled VCN. The existing Demo VCN
has no DNS label, so it cannot host the private ADB endpoint. A peering connection and one
route to `10.73.0.0/16` were added while preserving the existing Demo route-table rules.
The live API listener is accessible only from its gateway NSG; SSH only from DemoBastion.

Child-compartment creation was rejected by the tenancy with
`OracleInternalReserved tag updates are restricted`. The deployment did not alter those
reserved tags. Resources remain in Demo with exact user/key/secret references, a dedicated
Vault, owned-name prefixes and defined tags enforcing write boundaries. Schedule and secret
metadata inspection is compartment scoped. Identity drift inspection requires read-only
user/group metadata; credential reads/writes remain restricted to the four managed users.
Neither issuer nor workers can modify group membership or IAM policies.

## Implementation

* Four dedicated nonhuman users, two per profile. Customer Secret Keys are enabled;
  console passwords, API keys, auth tokens and SMTP credentials are disabled. Unexpected
  memberships/capabilities or foreign keys quarantine a user.
* Bucket READ permits LIST/GET. READ_WRITE additionally permits create, overwrite and
  multipart. Neither permits deletion or access to the existing negative-test bucket.
  Object/prefix profiles are not enabled.
* The LCM ADB account has only CREATE SESSION as an explicit system privilege; it owns
  the application tables. CREATE TABLE/SEQUENCE bootstrap grants are revoked. Row locks coordinate
  the VM and Functions. Customer secret material is never stored in database rows.
* Vault holds the database credential and bounded response replay. API retrieval checks
  the durable expiry reference even during a Vault outage. Replay deletion moves secrets
  to pending deletion, which blocks native bundle retrieval; physical deletion follows
  OCI's minimum retention period. This is not a claim of physical destruction in 60 seconds.
* The image is built and dependency-audited on OCI, pushed with a workload principal and
  deployed by immutable digest. The API runs as an unprivileged user in a read-only container.
* Setup, deletion and reconciliation Functions use separate resource-principal groups.
  Schedules carry only lease ID and generation. Scheduler principals can read metadata and
  invoke only the tagged deletion/reconciliation Functions. IAM writes are never granted
  to schedules. Events plus the durable outbox use the same idempotent setup path.
* API cleanup, an independent VM timer and an hourly recurring reconciliation schedule
  repair missing work and retry deletion. Cleanup heartbeat/overdue/quarantine alarms run
  in OCI Monitoring. The dedicated alert topic has no subscribers; an approved destination
  must be configured to deliver notifications to a person or system.

## Live evidence

| Check | Recorded evidence |
|---|---|
| Dedicated capabilities and exact memberships | [identity evidence](live-identity-evidence.json) |
| All four users: native S3 positive/negative scope, multipart, native IAM denial | [scope evidence](live-scope-evidence.json) |
| Private ADB transactions, CREATE grants revoked, encrypted replay and native pending-deletion denial | [Vault evidence](live-vault-evidence.json) |
| Real Function invocation and unapproved Function denial | [Function evidence](live-function-evidence.json) |
| Actual schedule principal allowed for deletion and denied for sentinel | [schedule delivery](live-schedule-delivery-evidence.json) |
| Schedule activation, body, generation and idempotency | [schedule confirmation](live-scheduler-evidence.json) |
| Runtime cannot read admin secret, modify users/groups or read foreign keys | [runtime permissions](live-runtime-permissions-evidence.json) |
| API authentication, replay, conflict, owner binding, control revocation, actual key-material hygiene | [final smoke](live-final-smoke-evidence.json) |
| Active lease preserved through same-schema image rollback/upgrade | [upgrade evidence](live-active-upgrade-evidence.json) |
| Actual home-region credential Events received | [Events fixture](live-events-evidence.json) |

Fresh keys in the capability tests required several minutes before native S3 reads succeeded.
The successful requests verified the expected CSV body using AWS Signature V4 on the regional
`oci.customer-oci.com` endpoint; OCI CLI success was not used as an S3 compatibility substitute.
The TTL clock starts at actual key creation and is never extended to hide propagation delay.

Live API testing exposed the gateway's initial 10-second timeout and variable asynchronous
Vault/schedule activation. Undelivered keys were revoked through compensation. The gateway
timeout and bounded activation handling were corrected. These findings affect the PRD's
15-second issuance target; live measurements must determine acceptance.

The instrumented Function-only expiry test stopped the API and VM cleanup containers.
The Function recorded revocation, and a separate IAM inventory confirmed zero managed keys.
Nevertheless, verified signed S3 reads continued through 237.995 seconds after the deadline.
The timed S3 denial target failed; the cause is not established. See
[per-poll expiry evidence](live-scheduler-expiry-evidence.json). No p95 claim is made from this
single run. Earlier single-denial probes are retained as historical, insufficient timing evidence.

The current immutable image has 72 audited dependencies and zero reported vulnerabilities;
post-release Function logs contain zero SDK DEBUG records. Earlier test-image logs included
key identifiers in SDK URLs; those test keys were deleted, and historical logs remain retained.
See [image audit](live-image-verification.json) and [log evidence](live-function-log-evidence.json).

## Release and operations

`scripts/package_live_release.py` builds the private Resource Manager ZIP with schema,
Terraform, automatic cloud-init bootstrap, a digest-verified private source object and pinned
image manifest. `scripts/verify_live_release.py` checks archive boundaries and scans for
private-key or bearer-token material. The private OCIR image requires authenticated access;
the release is not a public deploy-button archive.

The initial bootstrap provisions the schema and rotates both database passwords into Vault.
Initial sensitive Terraform-state password values are obsolete after rotation. Disable
`bootstrap_enabled` afterward. A separate temporary `image_build_enabled` switch permits
publishing only to this deployment's repository without re-enabling admin-secret access.
Both switches must be false at handoff.

OCI treats VM cloud-init changes as replacement. The live Terraform configuration ignores
subsequent user-data changes, and the deployment helper rejects unplanned resource removal
or replacement. Image upgrades pull the new digest before restarting the API, preserving the
ADB ledger and independent deletion paths. Do not use Terraform destroy to revoke credentials.

Run operator commands locally through DemoBastion with the pinned image and mounted
nonsecret configuration: `python scripts/live_operator.py status|pause|resume|drain|reconcile`.
`repair-schedule --lease-id`, `reconcile-user --user-id`, and `export-audit` are host-local
operator actions. Drain disables issuance, revokes owned keys and checks every managed user;
only `safe_to_remove:true` permits removal. Preserve audit exports and customer buckets.

Acceptance helpers use real services and keep tokens/credentials in process memory:
`scripts/live_acceptance.py --expiry --scheduler-only` observes a five-minute lease with
the API/timer stopped, restoring them afterward; `--performance-only --count 100` measures
sequential HTTPS issuance and revocation. Run these only in the dedicated demo environment.

## Historical paused handoff — 2026-11-12

At the 2026-11-12 handoff, live issuance was paused after the failed timed S3 denial test.
[Handoff checks](live-handoff-evidence.json) confirm live liveness 200 and readiness 503
from the deliberate pause, while the original mock remains ready (200). API and independent cleanup
remain running, and the original mock demo remains available. The [final drain evidence](live-drain-evidence.json) confirms zero outstanding leases and zero
managed-user keys. This is control-plane evidence; it does not establish immediate S3 denial.
The private deployment is retained
for investigation; resources are billable. Use the operator's explicit `resume` action only for
further controlled demo testing with the recorded expiry limitation understood.

A 100-request issuance p95 run, full distributed fault/load matrix, cross-schema upgrade,
fresh-environment replay, production IdP and actual Unity integration remain unaccepted.
The final smoke sample took 41.524 seconds to issue, and does not establish a p95 statistic.

## Live demo cutover — 2026-11-13

At the user's explicit request, real-credential demo issuance is enabled. The previous mock URL
redirects to the live OCI deployment, including `/docs`. The legacy VM and signing key remain,
but their JWTs are rejected by the live API following the Identity Domains migration.
There is no anonymous public token-minting endpoint. Use OCI sign-in in the UI, or obtain an
operator-only service token with `python scripts/identity_demo_token.py` through DemoBastion.
The UI simulates the connector; actual Unity query-engine integration remains outstanding.

The page states that it creates real OCI credentials and prominently reports the measured expiry
limitation. IAM, ADB, Vault, Functions and schedules use their existing real adapters. Issuance is
enabled for controlled testing; `production_ready` remains false. The previous failed expiry test
has not been reclassified as a pass. Do not use requested deadlines as hard access cutoffs.

The API reference now authorizes a pinned Swagger UI asset version and the exact initializer hash.
The application retains its original content policy. The previous mock origin redirects instead
of presenting simulated keys as real credentials. `scripts/activate_live_demo.py` verifies live
readiness and the Demo OCID before applying the redirect without replacing either VM.

The [live cutover test](live-cutover-evidence.json) issued a real key through the UI in 38.643
seconds, read the dedicated CSV through OCI S3 Signature V4, then revoked the lease through the UI.
Secret fields were cleared before desktop/mobile screenshots. The live OpenAPI schema excludes
synthetic endpoints; its functioning reference is verified separately from issuance.

## Restored live test controls

The live UI exposes read/list/write/delete/foreign-bucket diagnostics, scoped pool metrics,
lease countdowns, owner-bound audit and actual IAM service-user/key-count inventory. Keys
are created on the four dedicated service users, not the operator's personal OCI account.
The inventory is refreshed on connection, creation or explicit refresh; the dashboard polls
lease metadata/metrics/audit every five seconds. Clearing the credential panel discards the
pair from browser memory and disables further tests until another pair is issued.

`POST /v1/leases/{lease_id}/tests` verifies both JWT ownership and the ledger's exact key ID
before any S3 request. The secret travels only in the HTTPS request body and process memory;
it is neither audited nor returned. Tests can target only provisioned synthetic fixtures:
`quarterly/revenue.csv`, an owned `diagnostics/{lease_id}.txt`, the disposable deletion-denial
fixture, or the fixed negative bucket fixture. The endpoint returns status and verification
metadata, never object contents, and accepts no arbitrary endpoint, bucket or object path.
It is a bounded diagnostic harness; production/query data continues to use S3 directly.
Missing-object errors are not reported as proof of authorization denial. Actual results can
fail expectations during propagation or delayed deletion; those results are shown honestly.

Deployment preserves the ADB ledger and existing user leases; acceptance tests revoke only
the new lease they create. No pool drain or key rotation is part of this UI update.

[Live-controls browser evidence](live-controls-evidence.json) records actual GET/LIST/PUT 200
responses and DELETE/FOREIGN 403 responses. Initial 403s during positive propagation retries
are retained in the evidence. Owner-bound audit and metadata contain neither test key component.
The browser repeated a read after its own lease was revoked and recorded the observation;
this single sample does not reverse the earlier failed timed-expiry acceptance gate. Desktop
and mobile checks passed after clearing credential fields. Forty-nine local tests pass.
