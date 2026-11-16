# Remaining production acceptance

Live IAM/S3, private ADB, Vault, Functions, scheduling, immutable OCI image build, automatic
Resource Manager bootstrap and external alarms are implemented. See [acceptance](acceptance.md)
and [live deployment](live-deployment.md) for evidence and limits.

1. Obtain actual Unity wrapper/query-engine integration and owner signoff. The user explicitly
   accepted this as outstanding; raw S3/reference tests do not close it.
2. Resolve failed timed S3 denial acceptance: Function deletion and IAM absence were observed
   while signed reads still succeeded. Finish native concurrency, outage/recovery and 100-request
   issuance measurements. Record service timing and failures without substituting mock results.
3. Extend live duplicate/reorder event testing. An actual home-region credential creation fixture
   is now captured, and durable setup/outbox also works without Events.
4. Configure an approved alert destination, production JWT issuer/subjects and operational owner.
   Alarms and a dedicated topic exist, with no unrelated recipients subscribed.
5. Replay the authenticated private release in a separate fresh environment. Drain/revoke and
   export audit before teardown; preserve customer data. Terraform destroy is not key revocation.

Live readiness requires real capability gates and a fresh cleanup heartbeat. Production readiness
remains false until PRD acceptance completes; relabeling a fake adapter cannot enable it.
