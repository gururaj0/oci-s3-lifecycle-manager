# Asynchronous diagnostic investigation

The previous implementation ran schedule setup after credential delivery but retained a
global ADB writer lock across remote OCI calls. A subsequent diagnostic needed that lock
for rate limiting and auditing, so it waited for unrelated background work.

The live existing-policy baseline returned credentials in 2.6111 seconds, then took
24.4164 seconds for the diagnostic. Direct S3 LIST/GET returned in 0.0872/0.0046 seconds,
both with `SignatureDoesNotMatch`. That error alone does not prove IAM policy propagation;
the previous UI reported only HTTP 403. Key and membership absence were confirmed after
cleanup. This is one sample, not a percentile or a latency guarantee.

The update uses per-lease locks for schedule setup and deletion, separate locks for rate
limits and diagnostic audits, deferred diagnostic logging and revocation cleanup, and
validated group/policy OCID reuse. Credential issuance still requires authorization,
isolated-user reservation, membership creation and durable key identity recording.

The ADB concurrency experiment confirmed that same-lease work remains serialized while
rate, global and other-lease transactions complete before the held lease lock is released.
See `async-lock-evidence.json`. Unit/security regression tests: 127 passed.

## Live before/after sample

Both runs used OCI compute → API Gateway → backend with an already-existing
`quarterly/` group/policy. OAuth generation was excluded.

| Measurement | Before | After |
| --- | ---: | ---: |
| Credential response | 2.6111 s | 2.4581 s |
| Immediate diagnostic response | 24.4164 s | 0.4247 s |
| Revocation request response | 18.5121 s | 0.0189 s |

The after diagnostic completed while phase remained `CONFIRMING_SCHEDULE`:
S3 itself took 0.1976 s; backend processing took 0.3349 s. It initially returned
`SignatureDoesNotMatch`. With the same credentials and no policy modification,
LIST and GET both returned 200 at the first successful poll around 121 seconds
after credential delivery (the preceding poll at 111 seconds still failed).
This is consistent with delayed recognition of fresh credentials/authorization;
the measurements cannot isolate which OCI internal cache caused the delay.
It is separate from the application wait removed by this change.

The new issuance timings measured 0.5602 s for the IAM key-create call, 1.2680 s
for validated prefix resource lookup/membership setup, and 0.0095 s for durable
credential recording. Reused group/policy lookup used stored OCIDs. These are
single samples, not percentile or availability guarantees.

The faster 202 revocation response means durable intent was accepted, not that
S3 denial is already effective. The test waited for REVOKED and independently
confirmed IAM key and membership absence. The updated delete Function also passed
cleanup under its resource principal (`async-function-evidence.json`).
An unassisted 300-second lease was cleaned up by the instance reconciler 17.159 seconds
after TTL, with IAM key and membership absence verified (`async-ttl-evidence.json`).
This verifies managed cleanup; it does not establish the time S3 caches stop accepting
an old key.

Before evidence: `async-latency-before.json`; after: `async-latency-after.json`. Read the individual S3 error codes rather than treating every
403 as a policy delay. Fresh memberships and keys can still take time to become usable.

## Coordinated rollout

This changes the cross-process locking protocol. Deploy with issuance paused and no
nonterminal leases, then update the API and all four Functions to the same immutable
image before re-enabling issuance. Do not mix old global-lock workers with new per-lease
workers while leases are active. The live rollout checked zero active leases, updated
all workers, verified matching image digests and removed temporary build permissions.

Named mutex rows use the existing control table; no ADB grants or schema migration are
required. Per-lease mutex rows remain with historical lease state. Diagnostic audit
background tasks are best effort if the API process dies; critical lease state/audits
remain synchronous and durable. Deferred revocation has a durable intent and reconciler
recovery. This remains a demo, with actual Unity integration outstanding.
