# Opt-in integration tests

Run `python scripts/smoke.py https://<gateway-host> --expiry --performance 100`.
This uses raw HTTP AWS Signature V4 requests against the mock S3 endpoint, not OCI CLI calls.
The five-minute test observes access before expiry and actual denial after provider deletion.
Output contains timing and lease IDs, never credentials or JWTs. The script drains its test leases.

These results establish **mock lifecycle behavior on OCI-hosted infrastructure**. They do not
establish real OCI S3 compatibility, real IAM scope enforcement or actual Unity compatibility.
Do not point the mock script at a real credential service. Actual OCI integration remains M0/M2 work.
