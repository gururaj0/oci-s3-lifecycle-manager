# Automatic bucket access

The bearer-authenticated HTTP connector can call `POST /v1/bucket-operations` with a bucket
and operation. It does not supply a profile, service user, lease duration or OCI credential.

```json
{"bucket":"lcm-live-v2-sales","operation":"LIST","limit":20}
```

API Gateway validates the Identity Domain service JWT and overwrites verified identity headers.
The backend intersects that connector's explicit profile mapping with its JWT scopes, then selects
the least-privileged matching configured profile for the exact bucket and operation. Unauthorized
requests are rejected before obtaining an Object Storage client. No IAM policy text is interpreted
as a substitute for OCI authorization.

The existing VM instance principal performs the operation through the OCI Object Storage SDK.
Two statements in `lcm-live-v2-data-scope` grant this principal bucket-scoped read/create/overwrite
permissions; they do not grant object deletion. Existing service-user group grants are preserved.
Object Storage enforces the workload principal's effective IAM permissions. The caller's profile
restrictions are enforced by the backend; JWT identity does not automatically inherit OCI user IAM.

This path creates no Customer Secret Keys, leases, replay secrets or expiration schedules. It
records intent/outcome audit entries in ADB, closes response bodies and HTTP sessions, and lets the
SDK manage workload credentials. The independent cleanup machinery continues to handle existing
credential leases. The four-user lease pool does not constrain this HTTP path.

## Current contract

- LIST: optional `prefix`, `start` and `limit` (1–100). Continue with returned `next_start`.
- GET: `key` required. Returns base64 content, size and ETag. Maximum object size is 512 KiB.
- PUT: `key` and `content_base64` required, up to 512 KiB decoded. Defaults to create-only using
  `If-None-Match: *`; provide `if_match` for a conditional overwrite. HTTP 412 means the precondition
  failed. After an uncertain write outcome, inspect the object before retrying.
- No DELETE, arbitrary target URL, multipart, range reads or streaming in this initial HTTP version.
- No credential fields in responses. The original lease API and S3 diagnostic endpoints remain.

The UI automatically lists its first authorized bucket after sign-in and provides read/create
controls. Its session proxy still requires a session, same-origin CSRF protection and fixed routing.
Signing in alone does not create any objects; the default operation is LIST.

## Validation

86 unit tests passed, including profile/bucket rejection before OCI calls, unmapped identities,
conditional writes, response-body cleanup, native IAM error handling and UI session/CSRF protection.
Live LIST/GET/PUT, conditional conflict and overwrite, and authorization checks passed. The automatic
requests left the lease and Customer Secret Key inventory unchanged. An independent regression
created and revoked one new lease through the original API; only the test's own artifacts were
removed. See [live evidence](automatic-access-live-evidence.json).

The browser test uses a clearly labeled synthetic session to verify automatic LIST and GET control
behavior, CSRF forwarding and mobile/desktop layout. It does not automate an operator's actual OCI
sign-in. See [UI evidence](automatic-ui-evidence.json). Actual Unity/query-engine acceptance and the
previous credential-leasing expiry limitations remain outstanding.
