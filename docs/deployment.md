# OCI deployment — 0.1.0 mock

[Open the live demo](https://exjtqqyg54c5dek5vgprfx3no4.apigateway.us-ashburn-1.oci.customer-oci.com/)
· [Interactive API](https://exjtqqyg54c5dek5vgprfx3no4.apigateway.us-ashburn-1.oci.customer-oci.com/docs)
· [Readiness](https://exjtqqyg54c5dek5vgprfx3no4.apigateway.us-ashburn-1.oci.customer-oci.com/health/ready)

This is a public **synthetic demo** hosted on real OCI infrastructure. No real Customer Secret Keys
or OCI IAM service users are created. Production readiness is false. Actual Unity compatibility
remains outstanding, as requested. See [acceptance matrix](acceptance.md) for remaining implementation.

Deployment uses the user's existing OCI CLI DEFAULT authentication and configured home region
`us-ashburn-1`. `Demo` was resolved by name and its OCID fetched directly before creation:
`ocid1.compartment.oc1..aaaaaaaav6gvasmb4eyn6fnuuaoabal6mdfzuu4qxf3ll5kidwj24jxcn3dq`.

[Resource Manager stack](https://cloud.oracle.com/resourcemanager/stacks/ocid1.ormstack.oc1.iad.amaaaaaa5kvp65iauzugdd2oy4friv4bdjhdulqgc6y3fs3qccidvstjrslq?region=us-ashburn-1)
ID: `ocid1.ormstack.oc1.iad.amaaaaaa5kvp65iauzugdd2oy4friv4bdjhdulqgc6y3fs3qccidvstjrslq`.

Initial Resource Manager apply: **SUCCEEDED**, 12 November 2026, 08:25:28–08:30:06 America/New_York,
4 minutes 38 seconds. This is measured existing-network demo deployment time, not a clean-tenancy
production benchmark. Bootstrap finished automatically with `READY_MOCK_ONLY` at 08:29:03.
See [apply evidence](resource-manager-evidence.json) and [placement evidence](preflight-evidence.json).

New resources: private backend subnet `172.16.61.0/24`, restrictive subnet rules, two NSGs and their
rules, one E5 Flex VM (1 OCPU, 8 GB RAM, 50 GB boot), public HTTPS API Gateway/deployment.
The VM has **no public IP**. SSH is allowed only from DemoBastion private IP `172.16.0.71/32`.
API backend ingress is only from the gateway NSG. Existing DemoVCN, public subnet, NAT/service
routes and DemoBastion were reused without changes. Infrastructure remains billable while running.

Existing DemoBastion: public `152.70.204.167`, private `172.16.0.71`.
All backend SSH uses that jump host and the supplied local private key. The key and OCI CLI config
were never copied to OCI, Terraform variables, release source or outputs.

Initial live testing found and fixed gateway Host rewriting for SigV4 and empty HTTP 202 handling
in the browser. The release archive includes both fixes and pins the public gateway URL during
bootstrap. A drain-first Resource Manager rollout is used to verify that final source independently.

Evidence files:

- [Unit/contract tests](unit-test-evidence.txt)
- [Raw SigV4, latency and five-minute expiry](live-smoke-evidence.json)
- [Real-browser desktop/mobile checks](browser-evidence.json)
- [Runtime permissions and independent timer](runtime-security-evidence.json)
- [Dependency vulnerability scan](dependency-audit.json)
- [Release path/private-key/manifest scan](release-scan-evidence.json)
- [Desktop screenshot](demo-desktop-tested.png) and [mobile screenshot](demo-mobile.png)

Use the [operations runbook](operations.md) for status, pause/resume, revocation, drain and removal.
Do not use plain Terraform destroy or change the release source with outstanding leases.
No public release archive or Deploy button has been published. The versioned local ZIP is a supported
authenticated Resource Manager upload source; publication to a supported URL is a separate owner action.

First-pass measured results: 100 warmed mock issuances, p50 **0.280 s**, p95 **0.307 s**, max **0.346 s**.
The 300-second test lease expired at 13:41:25.448 UTC; signed-S3 access succeeded at 13:41:25.188,
and denial was observed at 13:41:25.714, **0.265 s after deadline**. No premature revocation was
observed. These are single-run mock measurements, not real OCI S3 or production p95 expiry evidence.
Before the final rollout, all 109 test leases were revoked; zero keys, zero quarantined users and
healthy cleanup were observed. Audit was exported locally before replacement.

Final packaged rollout: **SUCCEEDED**. Final VM:
`ocid1.instance.oc1.iad.anuwcljt5kvp65ica5lbvq6ssdczgp3obudpiaw4hrma6ml7bqci45zicekq`,
private IP **172.16.61.78**, no public IP. Automatic bootstrap finished at 08:46:22 America/New_York.
The old drained VM was removed by the same Resource Manager job. Gateway URL is unchanged.
[Final apply evidence](final-resource-manager-evidence.json), [runtime source hash proof](deployed-source-evidence.json),
[final-build lifecycle/expiry checks](final-live-smoke-evidence.json).

The final Resource Manager archive is `release/oci-s3-lcm-0.1.0-mock.zip`, SHA-256
`64a41c8211d5994c016041a3e01f8bc58c6912847b20e62263e1e588d5f5a608`.
The running service's application, SigV4 handler, UI script, lifecycle engine, operator helper and
runtime lock hashes match the source used to build that archive. No manual application copy was
needed on this final VM.

Final-build timed test: 300-second lease, no premature revocation; signed-S3 denial observed
**2.073 seconds after expiry**. Final status was REVOKED. Final pool check: zero allocated users,
zero quarantined users, zero overdue keys, cleanup healthy. All test leases were cleaned up.
See [final pool status](final-pool-evidence.json). The mock remains running for review.
