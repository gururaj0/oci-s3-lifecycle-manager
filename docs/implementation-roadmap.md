# Remaining production implementation

The user's requested mock demonstration is implemented on OCI. The attached PRD's **complete live
production service is not implemented**. This roadmap follows the PRD's mandatory M0-before-M2 gate.

1. M0: run the actual Unity wrapper/query engine; get its integration owner signoff. Prove IAM
   credential-only per-user management, service-user capability isolation and real bucket scopes.
   Capture a real home-region credential event. Prove one-time Function scheduling, payloads,
   timing, invocation-only policy and dynamic schedules without per-lease IAM edits.
2. M2: implement the transactional Oracle ledger repository, migration runner and dedicated schema
   bootstrap. Implement workload-principal OCI key/Vault/scheduler adapters and timeout reconciliation.
   Resolve Vault replay expiry/purge semantics with evidence. Implement the scanned immutable VM
   and Function artifact build pipeline and authenticated release source.
3. M2: add private Autonomous Database/Vault access, Functions, Events, independent recurring
   Resource Scheduler cleanup, monitoring/notifications, separate narrow runtime roles and preflight.
   Implement new-network mode, deployment IdP/JWKS/authorized-subject configuration and schema forms.
4. M3: run all real S3 negative tests, concurrent/distributed reservations, create/crash/Vault/schedule
   failure compensation, control-plane outages, permission drift and external heartbeat alarms.
   Measure 100 real issuances and real revocation p95; do not reuse mock timing as evidence.
5. M4: validate active-lease rolling upgrades/rollback, security-audit retention/export, drain/removal,
   lifecycle quota pressure and operational handoff. Publish a signed/versioned release source and
   a working Deploy-to-Oracle-Cloud URL only after a supported archive URL exists.

The `oci` startup gate must stay in place until the live implementation and evidence exist.
Adding an operator bypass flag or relabeling the fake adapter is not an acceptable completion path.
