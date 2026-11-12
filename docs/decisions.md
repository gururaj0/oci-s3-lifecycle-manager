# Capability decisions — 0.1.0 mock release

These decisions enforce the PRD M0 gate. No unverified live integration is presented as a passing mock.

## ADR-001: Actual Unity compatibility remains outstanding

The actual Unity wrapper/query engine was not supplied. The user explicitly requested recording
that integration as outstanding on 12 November 2026. Ship the reference adapter and mock demo;
keep live issuance disabled. A raw signed-S3 smoke test against the fake does not close AT02.

## ADR-002: OCI hosting with explicitly simulated credential services

Provision real OCI Compute, private subnet, NSGs, API Gateway and Resource Manager stack in the
verified Demo compartment. Simulate IAM users/keys, Vault responses, schedules and object storage.
The mock uses SQLite writer transactions and a separate encrypted fake control-plane database.
It creates no real IAM users, policies, Customer Secret Keys, buckets, ADB, Vault, Functions or schedules.
Readiness means `READY` **for mock operation** and always returns `production_ready:false`.
`LCM_MODE=oci` fails startup until M0 signoff and the live runtime are implemented.

The application is embedded into the stack archive and compressed cloud-init, avoiding manual
Docker/Fn builds and image pushes for this mock release. This is not the production immutable,
scanned-image pipeline. Exact runtime dependencies and the provider are pinned; a vulnerability
scan and source manifest are included in evidence. Source updates must use a new release.

## ADR-003: Scheduler API requires live validation, not guessed syntax

Oracle documentation supports one-time UTC Function schedules with invocation payloads.
OCI SDK 2.191.0 exposes `START_RESOURCE`, `CRON`/`ICAL`, and `Resource.parameters`.
The Functions guide explicitly uses action Start for Functions, so the generic enum is **not**
evidence that Function scheduling is unsupported. A read-only live `list-resource-types` call in Demo
returned `FunctionsFunction` for `START_RESOURCE`; see scheduler-capability-evidence.json. Confirm the one-time/payload encoding,
rounding, dynamic-group membership, invocation-only permission, and recovery from create timeouts
in the tenancy before implementing the live adapter. Do not grant tenancy-wide Functions management
just because the documentation example does. No live schedule or scheduler policy was created here.

## ADR-004: IAM scope cannot be inferred from returned metadata

Bucket READ/READ_WRITE are implemented only in the fake. Production needs dedicated nonhuman users,
disabled unrelated capabilities, exact credential permissions, target-user conditions and negative
raw-S3 tests. Default grants and existing group memberships must be checked. No employee key is
created. Supplied OCI CLI authentication is used for deployment only and never copied to the VM.

## ADR-005: Autonomous Database and Vault are production work, not silent substitutions

Oracle ledger migration DDL is a design artifact; the released runtime uses SQLite in mock mode only.
Do not enable the Function handlers on OCI: their mock engine reads host-local state and is suitable
for local contract tests, not a distributed Functions ledger. Live ADB repository, Vault lifecycle,
bootstrap/rotation, workload-principal adapters and separate runtime roles remain unimplemented.
The API rejects OCI mode instead of pretending that a private SQLite file is an Autonomous Database.

## ADR-006: Existing network mode for this release

Reuse DemoVCN's existing NAT/service routes and public gateway subnet. Create an isolated private
backend subnet and dedicated NSGs. VM SSH ingress is only DemoBastion's private IP /32. VM has no
public IP. The release supports public or private API Gateway selection. New-VCN mode and production
IdP egress allowlisting remain future work. Demo JWT signing uses its own persisted RSA key, not the
user's SSH/OCI authentication key. Public `/demo/session` is intentionally synthetic and rate-limited.
