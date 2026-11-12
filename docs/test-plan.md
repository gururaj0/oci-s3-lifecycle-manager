# Validation method

Local unit/contract tests use separate temporary ledger/provider files for each test. Fault injection
is in-process only; no public HTTP endpoint can request a cloud failure or change operator controls.
Tests cover signatures/claims, owner access, TTL defaults and bounds, canonical request identity,
concurrent retries, pool limits, no early/stale-generation deletion, foreign-key preservation,
unknown create outcomes, compensation and retry, process reconstruction after a simulated crash,
event duplication/drop/reordering, missing schedules, stale heartbeat, and replay secret purge.

The opt-in deployed smoke tool signs **raw HTTP requests** with AWS Signature V4. It exercises
positive READ/LIST/WRITE, negative cross-bucket/write/delete/wrong-secret, status hygiene,
owner isolation, idempotency conflict, capacity and revocation. It creates/drains 100 sequential
leases for warmed issuance latency, then holds a 300-second lease and polls until signed-S3 denial.
The measured denial is bounded by the two-second poll interval plus HTTP/request pacing; it is
an observation time, not the exact internal deletion timestamp. All results are labeled mock.

Chromium tests desktop and 390-pixel mobile layouts, no horizontal overflow, live session/issuance,
secret masking, S3 access and scope/revocation denials. Screenshots clear the credential panel first.
They contain no access/secret pair or bearer token. Console/page errors are checked separately from
expected HTTP denials. Gateway 429 during initial unpaced automation caused the test pacing to be
corrected, preserving the configured gateway rate limit and restrictive CSP.

A finalized Resource Manager source rollout follows drain and audit export; the VM replacement
starts from an empty mock ledger. The public gateway hostname remains stable. Test the final deployed
build after bootstrap. This does not establish production rolling upgrade compatibility.

Runtime dependency scanning uses the exact transitive requirements-runtime.lock and pip-audit.
Release scans check archive paths, links, embedded manifest checksum and private-key markers.
The runtime ledger/status/audit tests verify that issued secret plaintext is absent. OCI Terraform
state/outputs never contain Customer Secret Key values because this release creates no actual keys.
