# Live release

Build with `python scripts/package_live_release.py` and verify with
`python scripts/verify_live_release.py`. The package includes the live Terraform configuration,
secret-free source and audited immutable image manifest. Deploy through the existing private
Resource Manager stack using `scripts/live_stack.py`; never use a destroy job on the live stack
to revoke credentials.

The original mock OCI package has been retired. Local mock adapters remain for development
and unit tests. Previous releases and deployment assets remain in Git history.

Actual Unity integration and the recorded service-target acceptance gates remain outstanding.
