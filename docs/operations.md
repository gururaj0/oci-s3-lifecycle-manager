# Operations — mock 0.1.0

Use Resource Manager for infrastructure state. The live deployment record is in docs/deployment.md.
The provided private key remains on the operator's machine. SSH must use the existing DemoBastion.
The backend has no public IP. Never copy OCI CLI authentication or the SSH key onto the VM.

## Status

Public liveness: `/health/live`. Readiness: `/health/ready`; mock success explicitly includes
`mode:mock, production_ready:false`. Server bootstrap status: `/var/lib/lcm-bootstrap-status`.
Check `systemctl status lcm-api lcm-reconcile.timer` and `journalctl -u lcm-api`. Access logging is disabled.
The API performs cleanup every two seconds; an independent systemd timer runs every 15 seconds.
No cloud Function or Resource Scheduler is part of this mock. External heartbeat alarms are pending.

SSH configuration example (replace addresses with recorded deployment values):

```sshconfig
Host demo-bastion
  HostName <DemoBastion-public-IP>
  User opc
  IdentityFile "/Users/guru/Oracle Content/Documents/Oracle Cloud/Key/privatekeyrsa.pem"
  IdentitiesOnly yes
Host lcm-private
  HostName <backend-private-IP>
  User opc
  ProxyJump demo-bastion
  IdentityFile "/Users/guru/Oracle Content/Documents/Oracle Cloud/Key/privatekeyrsa.pem"
  IdentitiesOnly yes
```

## Operator commands

Run on the private VM through the bastion, using the service user:

```sh
cd /opt/lcm
sudo -u lcm env LCM_STATE_DIR=/var/lib/lcm LCM_CONFIG=/etc/lcm/config.json \
  /opt/lcm/venv/bin/python -m scripts.lcm_operator status
```

Actions: `pause`, `resume`, `drain [--profile sales-read]`, `reconcile`,
`repair-schedule --lease-id UUID`, `reconcile-user --user-id ID`, `export-audit`.
Operators use host-local access; there is no anonymous public control API. API status and deletion
require verified ownership. Full production audit-admin roles remain outstanding.

`drain` pauses new issuance, requests revocation, reconciles and verifies no remaining provider keys.
It exits nonzero if any lease/key remains. Do not delete foreign keys or clear quarantine to free capacity.
If dependency deletion fails, keep workers running. Revocation retries do not expire after a fixed count.
Keys may work after their timestamp during outages. Cleanup-health gating prevents new issuance;
it does not magically revoke existing S3 access at exact TTL.

## Replay, data and logs

Runtime state: `/var/lib/lcm/ledger.db`, `mock-cloud.db`, RSA signing key and encryption key. Directory
0700, files 0600, service UMask 0077. Ledger contains key references, not secret values.
Credential response replay is available for at most 60 seconds and before expiry, then requires a new
lease after explicit revocation. GET endpoints never return credential material. Browser credentials
exist only in memory. Host logs contain no request/response bodies or access URLs.

Mock audit retention currently preserves records; automated 90-day retention/export to a protected
archive is production work. Preserve audit and operator evidence before removal. No customer buckets
are created or destroyed by this release; `sales-demo` is synthetic data in SQLite.

## Upgrade and rollback

Build and scan a new versioned release, export `/var/lib/lcm` privately for audit, stop new issuance,
and drain before replacing this mock VM. A source change replaces the VM and starts a fresh mock
ledger; it does not claim to preserve active leases or local audit automatically. Terraform cloud-init changes can replace the VM; never apply such an upgrade
with outstanding leases. The mock does not claim rolling production upgrades. Real production
migrations must preserve active leases and deletion workers and be tested for rollback compatibility.

The mock release embeds its source in compressed user data; provider versions/dependencies are pinned.
Resource Manager owns infrastructure state. Application version changes require a rebuilt archive and
stack-source update. No manual Fn/Docker image push is required for the mock.

## Removal

1. Pause and drain via the host-local operator. Verify zero outstanding leases and zero provider keys.
2. Export redacted audit; preserve necessary state privately. Check the independent timer finished.
3. Create a Resource Manager destroy job for **this stack only**. Never destroy DemoBastion or DemoVCN.
4. Check the private VM, gateway, private subnet and NSGs from this stack are removed.
5. Keep the source/evidence and delete private state only according to the operator's retention policy.

A plain Terraform destroy is not a credential revocation protocol. This mock creates no real OCI keys,
but the same drain-first discipline is intentionally retained.
