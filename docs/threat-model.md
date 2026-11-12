# Threat model and release boundary

Assets: client credential responses, owner identity, durable key references, user reservations,
cleanup intent, audit evidence and deployment authentication.

Trust boundaries: public browser -> HTTPS API Gateway -> private FastAPI; operator -> existing
DemoBastion -> private VM. The mock provider and ledger are separate SQLite databases on the VM.
No OCI CLI config or supplied SSH private key is copied to cloud-init, application artifacts or VM.

Controls implemented: RS256 verification with required issuer/audience/expiry/subject, explicit
profile mappings, request validation, server UTC, exclusive database reservations, unique owner-bound
idempotency, response encryption, no-store headers, no credential-bearing status response, persistent
revocation retries, no early scheduler authority, generation/ownership checks, foreign-key quarantine,
S3 SigV4 verification, deny deletion permission, body limits, rate limits, CSP and host service hardening.

The browser keeps fake credentials and its demo JWT in memory only. Secrets are masked by default.
The fake cloud's provider-key secret is encrypted for signature verification; this emulates an external
provider and differs from the production service, which must not retain live keys beyond replay.
The encryption/signing keys reside in /var/lib/lcm, permissions 0600. VM root remains trusted.
Replay records are removed by cleanup after their 60-second window and unavailable immediately at
window end. Fake provider material is removed after confirmed key deletion.

Residual risks: public demo sessions intentionally allow issuance of synthetic leases; anonymous
visitors can consume the four-user pool until expiry. Mock scope and revocation behavior establish
no real OCI enforcement. Single-VM failure interrupts cleanup until recovery. Snapshot theft can
expose mock encryption keys. The mock is not a production credential broker, an HA service, native
STS, or an assertion of native Unity Catalog OCI support. Exact TTL is never an S3 expiry guarantee.

Production requirements still needing implementation are enumerated in acceptance.md: ADB, Vault,
workload principals, IdP trust and egress configuration, credential-only scoped IAM, actual Events,
Function artifacts, independent cloud scheduling, external monitoring/alerts and upgrade/teardown.
