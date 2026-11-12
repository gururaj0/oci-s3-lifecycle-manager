# Acceptance and readiness

Release **0.1.0-mock** implements and demonstrates the M1 fake-adapter lifecycle on real OCI
hosting. It is **not a completed production MVP** under the PRD. Mock READY cannot satisfy live gates.

| ID | Mock evidence | Outstanding production acceptance |
|---|---|---|
| AT01 deployment | Resource Manager stack and automated private-VM bootstrap; existing Demo network | Fresh-tenancy deployment, ADB/Vault/Functions, new-VCN mode |
| AT02 client | Raw HTTP SigV4 against mock; reference adapter | Actual Unity wrapper/query engine and real OCI S3 renewal; user acknowledged outstanding |
| AT03 scope | Positive list/read/write and negative bucket/write/delete tests in fake | Real IAM, inherited grants, multipart and user-capability tests |
| AT04 expiry | Five-minute mock lease, pre-expiry access and post-deletion denial, measured timing | Real OCI S3 p95/all-leases timing |
| AT05 concurrency | Concurrent retry tests, one lease per user, capacity and foreign-key preservation | Multi-host ADB and real OCI races |
| AT06 failure | Lost create response, crash before persistence, Vault/schedule/delete fault injection | Real provider fault injection |
| AT07 events | Duplicate, missing, out-of-order and stale generation tests | Actual home-region event fixture and independent Function execution |
| AT08 outage | Stale heartbeat and overdue stop, persisted retries, restart recovery | Live scheduler outage, external heartbeat alarms |
| AT09 auth | RS256 issuer/audience/expiry/signature, forged header and cross-owner denial | Production JWKS outage/rotation, signed delegation support (not enabled) |
| AT10 hygiene | No secrets in GET/log-like surfaces; encrypted replay; bounded replay | Real Vault immediate purge semantics; complete state/trace scan |
| AT11 IAM/quota | Fake two-key limit, pool 429; no foreign-key deletion | Runtime credential-only IAM and dynamic scheduler invoke proof |
| AT12 upgrade/drain | Restart/recovery tests and operator drain | Production rolling upgrade/rollback with active leases and full teardown |

Production readiness is not configurable with a boolean bypass. Resolve M0; implement live ADB,
Vault, IAM and scheduler adapters and production infra; collect AT01–AT12 evidence and integration
team signoff before releasing an OCI issuance profile. Do not remove the startup gate to run mocks
under an `oci` label.

Sources: [Customer Secret Keys](https://docs.oracle.com/en-us/iaas/Content/Identity/access/working-with-customer-secret-keys.htm),
[Deploy button](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Tasks/deploybutton.htm),
[Events](https://docs.oracle.com/en-us/iaas/Content/Events/Reference/eventsproducers.htm),
[Function scheduling](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsscheduling.htm),
[Terraform versions](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Reference/terraformversions.htm).
