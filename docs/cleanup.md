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

## Pending runtime rollout

The source cleanup is prepared locally. The running image and published OCI template have not
been updated. Automatic approval review rejected the build/deployment action because it changes
live-stack build permissions and publishes an image. Explicit approval is needed to build the
cleaned image, remove temporary publishing permissions, deploy it to the existing VM/Functions,
refresh the private template and verify the live flow. No database migration or lease drain is
part of that rollout. Restarting the UI will require users to sign in again.

Actual Unity integration and the previously recorded expiry/propagation acceptance limits remain.
