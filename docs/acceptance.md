# Acceptance and readiness — live 0.2.0

The previous public mock URL redirects to the live OCI deployment. Real integrations have separate native OCI
proof; mock results are not substituted. Production readiness remains false pending full PRD
acceptance and actual Unity signoff.

| ID | Current evidence | Remaining acceptance |
|---|---|---|
| AT01 deployment | Resource Manager, automatic private-artifact bootstrap, private ADB/Vault, Functions, new VCN, private template | Separate fresh-tenancy replay |
| AT02 client | Native signed S3 and reference adapter | Actual Unity acquisition/use/renewal; explicitly outstanding |
| AT03 scope | All four users: read/list, write/overwrite/multipart, foreign bucket/delete/native-IAM denials; exact capabilities/memberships | Production drift operations |
| AT04 expiry | Function-only control-plane revocation proved; S3 reads continued beyond deadline in instrumented run | Timed S3 denial target FAILED; revocation distribution remains unmeasured |
| AT05 concurrency | Local race/idempotency/capacity/foreign-key tests; real replay/conflict | Real distributed race/load dataset |
| AT06 failure | Local unknown-create/crash faults; real Vault activation compensation revoked undelivered keys | Full native control-plane fault matrix |
| AT07 events | Actual home-region credential Events received; one-shot body/generation/idempotency | Extended duplicate/reorder injection |
| AT08 outage | Independent timer, recurring schedule, external absence/overdue/quarantine alarms | Complete deletion-denial/recovery matrix; alert subscription |
| AT09 auth | Local signature/header/delegation misuse tests; real JWT/owner denials | Production IdP rotation/outage; optional delegation disabled |
| AT10 hygiene | Real encrypted replay and native pending-deletion denial; redacted GET; package scans | Complete native trace/state/log dataset |
| AT11 IAM/quota | Real foreign-key/user/group/admin-secret denials; schedule allowed/sentinel denied | Extended live pool/quota load |
| AT12 upgrade/drain | Durable ADB across restarts; actual same-schema image rollback/upgrade preserves active lease; operator drain | Cross-schema migration/rollback matrix |

See [live deployment evidence](live-deployment.md). Observed issuance exceeded the 15-second
target. The 100-request run was not completed; timed expiry acceptance failed first.
The required 100-request latency run and revocation distribution must be reported
explicitly; small samples do not establish p95 compliance.

Forty-nine local tests cover lifecycle, API ownership, recovery, adapter boundaries, concurrent-deletion
inspection and drift-triggered revocation. They use isolated fakes unless evidence explicitly
identifies an opt-in native OCI run.

The 2026-11-13 live cutover enabled real issuance at the user's request. The prior public mock
URL redirects to live; the existing issuer JWKS remains available. A real browser created a key,
performed a native signed OCI S3 read, revoked it and cleared the secret fields before screenshots.
See [cutover evidence](live-cutover-evidence.json). This does not reverse the failed expiry target.

Restored live UI diagnostics now have native browser evidence for read/list/write allowance,
delete/foreign denial, inventory, scoped metrics, owner audit redaction and mobile layout:
[live controls](live-controls-evidence.json). Positive propagation retries and after-revocation
observations are recorded; the failed timing target remains a separate unaccepted gate.
