# Cleanup — 2026-11-16

The current architecture is Identity Domains → API Gateway JWT validation → private Lifecycle
API profile/lease authorization. The UI remains the Unity connector simulation. The live URL is:
https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com/

## OCI cleanup

The Demo compartment was fetched and its name, ACTIVE state and OCID verified:
`ocid1.compartment.oc1..aaaaaaaav6gvasmb4eyn6fnuuaoabal6mdfzuu4qxf3ll5kidwj24jxcn3dq`.
The existing OCI CLI DEFAULT configuration and us-ashburn-1 region were used.

The retired mock stack owns the old redirect gateway, custom-issuer VM and boot disk,
private subnet, security list, NSGs and their rules. Its managed resource IDs were checked
against the live stack state: none were referenced. Its mock ledger was drained and confirmed
zero outstanding leases and zero keys; audit history was exported privately before teardown.
The old public URL is retired, including its JWKS endpoint. Use the live URL above.

Sixteen unused OCIR images were deleted. The exact digest running on the VM and all four
Functions was verified and retained, along with the last pre-gateway Identity Domain checkpoint.
The checkpoint is historical; restoring it would require its matching configuration, not a blind
image-only rollback. Two inactive one-shot schedules from November 12 were removed after
confirming their lease IDs are absent from the current ledger. The recurring reconciler remains.

The live API/VM, ADB, buckets and test fixtures, service-user pools, IAM policies, Identity Domain
applications, Vault, Functions, alarms and active reconciliation schedule remain required.
DemoBastion, its shared network, the live peering route and the operator's SSH key remain required.
The admin secret is retained for database recovery; pending-deletion replay secrets follow OCI's
retention period. Historical audit and acceptance evidence remains available.

See [machine-readable cleanup evidence](cleanup-evidence.json) for terminal resource status and checks.

## Code cleanup

Removed the original mock Terraform deployment and packaged release, custom live-token issuer,
redirect-only migration code and test, obsolete bearer-input UI tests, legacy acceptance and
hard-coded rollback helpers, and diagrams depicting the retired custom-issuer architecture.
Shared CLI support now lives in `scripts/oci_cli.py`. The config preparation helper preserves
current Identity Domain/gateway settings instead of restoring the retired issuer defaults.

The local mock and its lifecycle tests remain useful for offline development. Historical files
and previous releases remain in Git history. Current test helpers are `gateway_auth_smoke.py`,
`gateway_ui_smoke.py` and `identity_demo_token.py`. The older expiry/load harness needs a current
architecture replacement before those outstanding acceptance gates can be rerun.

Validation: 69 unit tests passed; the cleaned live source/package passed the private-key and
bearer-token scan. Read-only live checks returned 200 for the UI, liveness, readiness, reference,
authorized profiles, metrics and test setup; anonymous lifecycle access returned 401.

## Completed runtime rollout

The user approved the rollout after automatic approval review requested explicit authorization.
The cleaned image is deployed on the existing VM and all four Functions. Temporary image
publishing permission was removed and database bootstrap stayed disabled. The private Resource
Manager template was refreshed and its downloaded archive hash verified.

The build now removes retired files from the managed source directories before extraction.
The running API's source hashes match the cleaned repository; retired host scripts are absent.
Eight gateway/backend authentication checks and all automated sign-in UI checks passed. These
checks do not perform the operator's interactive login or repeat the historical S3 expiry suite.
The UI restart clears browser sessions; sign in with OCI again. Existing leases were preserved.

Actual Unity integration and the previously recorded expiry/propagation acceptance limits remain.
