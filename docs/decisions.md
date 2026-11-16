# Capability decisions — live 0.2.0

The [mock-only decisions](decisions-0.1-mock.md) are historical. Live integrations are now
implemented; see [the deployment record](live-deployment.md).

Actual Unity wrapper/query-engine acceptance remains outstanding as the user requested.
That does not defer IAM, ADB, Vault or Functions. Raw native S3 tests and the reference adapter
demonstrate those paths without claiming query-engine signoff. Production readiness stays false.

Live mode uses private ADB and real OCI SDK adapters. Instance/resource principals authenticate
runtimes; deployer OCI/SSH private keys stay local. JWT trust and immutable subject mappings are
explicit. Mock credential, S3 and JWKS endpoints are disabled in live mode.

IAM CreateSecretKey/DeleteCustomerSecretKey require USER_UPDATE plus their specific key permission.
Writes are constrained by exact managed-user OCID and operation; general user modification and
foreign credential management are denied. Membership drift inspection requires read-only identity
metadata, while credential payload reads stay scoped to four users. Runtime cannot edit groups
or policies. [Oracle IAM permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/iampolicyreference.htm).

Vault replay is bounded by durable expiry references, at most 60 seconds. Pending deletion blocks
native bundle retrieval; physical deletion follows OCI retention. Schedule confirmation precedes
replay storage so scheduling does not consume the replay window. Activation waits are bounded;
undelivered keys are compensated. Observed successful issuance exceeded 15 seconds, so target
compliance requires measurement. [Vault deletion](https://docs.oracle.com/en-us/iaas/Content/secret-management/Tasks/delete-secret.htm).

One-shot schedules use UTC, ICAL COUNT=1 and JSON lease ID/generation. Confirmation parses OCI's
JSON-string body representation and validates it. Tagged scheduler principals can read approved
Function metadata and invoke deletion/reconciliation only. FN_FUNCTION_READ is required for lookup
alongside FN_INVOCATION; no general Functions management is granted. Real schedules succeed for
deletion and fail for the sentinel. [Function permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/functionspolicyreference.htm).

Events is configured in the home region. Actual lowercase credential-creation events reached the setup Function; a redacted native fixture
and managed-user correlation are recorded in live-events-evidence.json. Synchronous setup and the
durable outbox/reconciler also establish confirmed schedules when Events is absent.

The original Demo VCN lacks a DNS label. Private ADB therefore uses a new labeled VCN peered to
Demo, with SSH through DemoBastion. Reserved-tag restrictions prevented child-compartment creation;
resources remain in Demo with exact references, a dedicated Vault, owned prefixes and defined tags.

Images are built/audited on OCI and pinned by digest. Cloud-init fetches a private digest-verified
source bundle using the instance principal. The Resource Manager template is authenticated/private.
Temporary admin-secret and publishing rights are removed at handoff; bootstrap passwords rotate.

OCI user-data changes replace VMs, so upgrades preserve user data and use the image installer.
Containers have explicit names and stop/remove commands: stopping only the Podman client can leave
an orphaned runtime. Isolation tests check actual containers and the recorded deletion actor.

The isolated Function-only expiry probe confirmed IAM key absence while signed S3 reads
continued beyond the deadline. Control-plane REVOKED does not establish timed data-plane
denial. This is a failed expiry acceptance gate; the cause is not established. Native customer
secret keys have no cryptographic TTL, so this deployment cannot promise hard expiry.
