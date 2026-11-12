# Versioned mock release

`python scripts/package.py` builds `oci-s3-lcm-0.1.0-mock.zip` and `manifest.json`.
The ZIP is a Resource Manager Terraform source with embedded secret-free application and pinned lock.
No OCI authentication, private SSH key, runtime JWT, encryption key or credential response is packaged.

For an authenticated local release, upload the ZIP to Resource Manager or use scripts/deploy_oci.py.
When the owner publishes a secret-free ZIP to a supported source, construct:

```text
https://cloud.oracle.com/resourcemanager/stacks/create?zipUrl=<URL-encoded-supported-release-ZIP-URL>
```

Supported sources are documented by [Oracle](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Tasks/deploybutton.htm).
The user still signs in, configures and reviews the schema-driven stack, and explicitly selects Run apply.
The mock release is not published to a public repository and therefore has no public deploy-button URL.

All shipped production readiness assertions remain false. manifest.json identifies the mock profile.
