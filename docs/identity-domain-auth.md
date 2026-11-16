# Identity Domains authentication and Unity connector simulation

Open the live demo and choose **Sign in with OCI**. Use the approved Default-domain
operator account, `gururaj.mohan@oracle.com`. No bearer-token copying is required.
The UI simulates a Unity connector; actual Unity Catalog/query-engine integration remains
an outstanding acceptance gate.

## Flow and boundaries

1. The backend redirects to OCI Identity Domains using authorization code + S256 PKCE,
   a browser-bound state cookie, and a nonce.
2. After validating the returned ID token's signature, issuer, audience, expiry and nonce,
   the backend checks the operator subject against its explicit allowlist.
3. The backend reads the two confidential client secrets from the existing Demo Vault
   using its instance principal. The login client exchanges the authorization code;
   the separate connector client requests a client-credentials access token.
4. The UI sends the connector JWT through the protected API Gateway `/v1/*` route.
   The gateway validates it against this domain's public signing keys, the dedicated API
   audience and required scopes, then overwrites identity headers. The private Lifecycle
   API checks the gateway transport peer, approved bucket profiles and lease ownership.
   It does not verify the service JWT again. See [gateway authentication](gateway-auth.md).
5. The browser receives a random Secure, HttpOnly, SameSite=Lax session cookie. Service
   JWTs and client secrets remain on the backend. Cookie-authenticated mutations also
   require the configured Origin and a session-specific CSRF header.
6. The existing lease, metrics, inventory and S3 diagnostic controls operate as the connector.
   Native OCI IAM policies still enforce bucket permissions on Customer Secret Keys.

The configured domain discovery issuer is `https://identity.oraclecloud.com/`; the public
key endpoint is specific to the Default domain. The actual minted access token returns
short scopes `sales-read sales-write`, although token requests use fully qualified scope
names. Both properties were observed and signature-verified rather than guessed.

## OCI registrations

Under **Identity & Security → Domains → Default → Integrated applications**:

- **LCM Live Lease API**: resource server, dedicated API audience, two lease scopes,
  ten-minute access token lifetime.
- **LCM Live Unity Demo Connector**: confidential client, client-credentials grant,
  explicit access to the two lease scopes, no Identity Domain administrator roles.
- **LCM Live Demo UI Sign In**: separate confidential authorization-code client,
  exact live callback URI, no connector lease scopes.

Secrets reside in the Demo Vault secret named `lcm-live-v2-oauth-connector`. Only the
issuer VM's dynamic group received an additional read grant for that exact secret OCID.
The Functions do not need these OAuth client secrets. Operator SSH uses DemoBastion and
remains separate from token signing. The previous demo signing key is no longer trusted.

## Session behavior

Sessions last at most ten minutes and never outlive the service JWT or ID token. Sign in
again after expiry. Sessions are held in the single UI process's memory, so restarting
or deploying the API requires sign-in again. This is a demo implementation; multiple API
replicas would require a shared, protected session store.

Leases and audit records belong to the connector service identity. The current allowlist
contains only the operator; adding users to it would give them the shared connector's
lease view. Signing out clears the application session but does not sign out of OCI SSO
or revoke existing leases. Revoke a lease explicitly when finished.

The existing S3 diagnostic controls keep issued Customer Secret Keys in browser memory
for testing. This is distinct from OAuth client secrets/JWTs, which never enter the UI.
Managed key deletion is still not hard STS expiry; the documented OCI propagation failure
and other production acceptance gates remain unchanged.

## Operator/API testing and maintenance

`python scripts/identity_demo_token.py` uses SSH through DemoBastion and the private VM's
instance principal to obtain a service access token. Its stdout is sensitive: do not log,
screenshot or commit it. It replaces `live_demo_token.py` for live API calls.

The provisioning script stages `.local/identity-domain-config.json`, creates only dedicated
registrations, verifies the Demo compartment OCID, and saves recovery credentials privately
under ignored `.local/` with mode 0600. Deployment references only the Vault secret OCID.
`build_identity_release.py` temporarily grants repository-write access, removes it in a
`finally` block, and verifies the effective policy; database bootstrap stays disabled.

Evidence: [provisioning](identity-domain-provisioning.json) and
[live tests](identity-domain-live-evidence.json). Interactive operator sign-in is reported
separately from automated service-token and S3 verification.

References: [Oracle client credentials](https://docs.oracle.com/en-us/iaas/Content/Identity/api-getstarted/CCGT.htm),
[token validation](https://docs.oracle.com/en-us/iaas/Content/Identity/api-getstarted/TokenValidation.htm),
[OAuth configuration](https://docs.oracle.com/en-us/iaas/Content/Identity/applications/to-configure-oauth.htm).
