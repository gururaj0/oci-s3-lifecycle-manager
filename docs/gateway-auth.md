# Gateway JWT validation and backend authorization

The private GitHub repository contains checkpoint `a39863e`, the working Identity Domains
implementation before this change. The new deployment separates authentication from
application authorization.

```text
Browser --session cookie--> UI session handler (private port 8080)
                              |
                              +--service JWT--> API Gateway /v1
Real Unity connector ---------service JWT-----> API Gateway /v1
                                                   |
                                            verified identity headers
                                                   |
                                      Lifecycle API (private port 8081)
                                      bucket profile + lease ownership
```

## Trust boundary

- Identity Domains continues issuing service access tokens.
- The protected `/v1/*` gateway route uses `TOKEN_AUTHENTICATION` with remote JWKS,
  TLS verification, the exact issuer and API audience, required subject/scope/expiry claims,
  and route scope authorization. The lifecycle route requires authentication; UI routes are explicitly anonymous at the gateway and enforce their own browser sessions.
- The gateway overwrites `X-LCM-Issuer`, `X-LCM-Subject`, and `X-LCM-Scope`
  with values from its verified authentication context. It never preserves caller-supplied
  values for these headers.
- Only the gateway NSG can reach the lifecycle listener on port 8081. The empty subnet
  security list adds no alternative ingress. Bastion SSH access grants port 22 only.
- The API also checks the actual transport peer belongs to the dedicated gateway subnet,
  requires a single value for each identity header, and rejects missing/malformed claims.
  Uvicorn proxy-header processing is disabled: `X-Forwarded-For` cannot change this peer.
- The lifecycle API does **not** decode or verify the service JWT. It intersects the
  asserted identity's approved profiles with its scopes, then checks lease ownership and
  profile on reads, revocations and diagnostic operations.
- OCI IAM still enforces the issued Customer Secret Key's actual S3 bucket permissions.

## Browser behavior

The existing OCI sign-in and secure session cookie remain. A separate UI process exposes
only assets, sign-in/out, API documentation, readiness, and a restricted connector proxy.
It exposes no direct `/v1` lifecycle handlers. Its `/connector/v1/...` proxy requires the
browser session and CSRF protection for mutations, uses a fixed gateway destination and
an explicit path/method allowlist, and forwards only the server's service JWT, content type,
and optional idempotency key. Caller cookies and identity headers are not forwarded.

At sign-in the UI validates the **user ID token**, including nonce and approved subject,
then obtains the separate connector service token. It calls the protected gateway to
establish the connector identity. Session lifetime comes from the Identity Domain token
response (`expires_in`), capped at ten minutes and the user ID token expiry. This user-login check is separate from
service JWT verification and does not duplicate the gateway's access-token check.

The browser still holds test Customer Secret Keys in memory for the existing S3 controls;
OAuth client secrets remain in Vault and the service JWT remains on the server. Sessions
expire after at most ten minutes and are lost on API/UI process restart. Actual Unity
query-engine integration and the previously failed hard-expiry acceptance gate remain
outstanding.

## Deployment and verification

`gateway_jwt_enabled` controls the protected gateway route for staged migration.
It defaults to enabled for new deployments and must be paired with the split image/configuration. `auth_mode=gateway` and an
explicit gateway subnet are required by the backend. Builds retain the existing live
worker configuration until the new image is deployed, and temporary registry-write
permission is removed and verified in `finally`.

Tests cover forged headers, duplicate/missing claims, spoofed proxy addresses, blocked
non-gateway transport, UI proxy restrictions, no service JWT decoding, profile scope
intersection, and cross-owner lease access. Live verification additionally checks the
actual gateway policy and private network boundary, and creates/revokes only its own test
lease. Evidence is recorded in `gateway-auth-live-evidence.json` and
`gateway-auth-deployment.json`.

For API clients, the endpoint remains `/v1/...` with `Authorization: Bearer <token>`.
For UI users, refresh the page and use **Sign in with OCI**. No token copying is required.

## Recorded result

The deployment and live gateway trust-boundary checks passed, and the user confirmed
interactive sign-in, controls and metrics after the split. Seventy unit tests passed.
Native GET, LIST and PUT succeeded and DELETE returned 403. The foreign-bucket check
returned an ambiguous 404. A follow-up independently verified the fixture exists and
successfully read the permitted bucket with the same credential before observing three
foreign-read responses of `403 SignatureDoesNotMatch`. No foreign data was returned, but
this does not conclusively isolate IAM policy denial; the combined S3 suite remains
marked incomplete rather than being reported as a clean pass. Both test leases were
revoked. See `gateway-foreign-followup.json` for the preserved observations.
