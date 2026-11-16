resource "oci_core_network_security_group_security_rule" "ssh" {
  network_security_group_id = oci_core_network_security_group.runtime.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${var.bastion_private_ip}/32"
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}
resource "oci_core_instance" "runtime" {
  compartment_id      = var.compartment_ocid
  availability_domain = var.availability_domain
  display_name        = var.prefix
  shape               = "VM.Standard.E5.Flex"
  shape_config {
    ocpus         = 2
    memory_in_gbs = 16
  }
  source_details {
    source_type             = "image"
    source_id               = var.image_id
    boot_volume_size_in_gbs = 50
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.private.id
    assign_public_ip = false
    nsg_ids          = [oci_core_network_security_group.runtime.id]
  }
  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      bootstrap_b64 = base64gzip(templatefile("${path.module}/bootstrap.py.tftpl", {
        config_b64      = base64encode(jsonencode(local.issuer_config)),
        admin_secret_id = oci_vault_secret.admin.id,
        namespace       = var.namespace, bucket = oci_objectstorage_bucket.release.name,
        object_name     = oci_objectstorage_object.release.object,
        source_sha256   = filesha256("${path.module}/lcm-live-source.tar.xz"),
        image           = var.function_image == "" ? "" : "${split(":", var.function_image)[0]}@${var.function_image_digest}"
      }))
    }))
  }
  freeform_tags = local.tags
  # OCI treats cloud-init changes as replacement. Upgrades use the pinned-image
  # installer and preserve the running deletion workers and durable ledger.
  lifecycle { ignore_changes = [metadata["user_data"]] }
}
resource "oci_identity_dynamic_group" "issuer" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-issuer"
  description    = "LCM private VM instance principal only"
  matching_rule  = "ALL {instance.id = '${oci_core_instance.runtime.id}'}"
}
resource "oci_identity_policy" "issuer_credentials" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-issuer-credentials"
  description    = "Read-only identity metadata; customer-secret-key writes only on four explicit LCM pool users"
  statements = concat(["Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {GROUP_INSPECT, USER_INSPECT} in tenancy"], flatten([for u in oci_identity_user.pool : [
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {USER_INSPECT, USER_READ} in tenancy where target.user.id = '${u.id}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {USER_UPDATE, USER_SECRETKEY_ADD, USER_SECRETKEY_REMOVE} in tenancy where all {target.user.id = '${u.id}', any {request.operation = 'CreateCustomerSecretKey', request.operation = 'CreateSecretKey', request.operation = 'DeleteCustomerSecretKey'}}"
  ]]))
}
resource "oci_identity_policy" "issuer_services" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-issuer-services"
  description    = "LCM secret/schedule services; temporary bootstrap access is removable"
  statements = concat([
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to read repos in compartment id ${var.compartment_ocid} where target.repo.name = '${oci_artifacts_container_repository.worker.display_name}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to read secret-bundles in compartment id ${var.compartment_ocid} where target.secret.id = '${oci_vault_secret.application.id}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to inspect secrets in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to manage secret-family in compartment id ${var.compartment_ocid} where all {target.vault.id = '${oci_kms_vault.lcm.id}', target.secret.name = /${var.prefix}-replay-*/}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {VAULT_READ, VAULT_CREATE_SECRET} in compartment id ${var.compartment_ocid} where target.vault.id = '${oci_kms_vault.lcm.id}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {KEY_READ, KEY_ENCRYPT, KEY_DECRYPT} in compartment id ${var.compartment_ocid} where target.key.id = '${oci_kms_key.lcm.id}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {RESOURCE_SCHEDULE_CREATE, RESOURCE_SCHEDULE_INSPECT} in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {RESOURCE_SCHEDULE_READ, RESOURCE_SCHEDULE_UPDATE, RESOURCE_SCHEDULE_DELETE} in compartment id ${var.compartment_ocid} where target.resource.tag.LcmLeaseV2.deployment = '${var.prefix}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to use tag-namespaces in compartment id ${var.compartment_ocid} where target.tag-namespace.id = '${oci_identity_tag_namespace.lcm.id}'"
    ], try(local.supplied_config.oauth.secret_id, "") != "" ? [
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to read secret-bundles in compartment id ${var.compartment_ocid} where target.secret.id = '${local.supplied_config.oauth.secret_id}'"
    ] : [], var.bootstrap_enabled ? [
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to read secret-bundles in compartment id ${var.compartment_ocid} where target.secret.id = '${oci_vault_secret.admin.id}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to manage secrets in compartment id ${var.compartment_ocid} where target.secret.id = '${oci_vault_secret.admin.id}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to manage secrets in compartment id ${var.compartment_ocid} where target.secret.id = '${oci_vault_secret.application.id}'"
    ] : [], var.bootstrap_enabled || var.image_build_enabled ? [
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to manage repos in compartment id ${var.compartment_ocid} where target.repo.name = '${oci_artifacts_container_repository.worker.display_name}'"
  ] : [])
}
output "runtime" {
  value = {
    id              = oci_core_instance.runtime.id
    private_ip      = oci_core_instance.runtime.private_ip
    issuer_group_id = oci_identity_dynamic_group.issuer.id
  }
}
output "connection_profiles" {
  value = oci_database_autonomous_database.ledger.connection_strings
}
resource "oci_core_network_security_group_security_rule" "http" {
  network_security_group_id = oci_core_network_security_group.runtime.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 80
      max = 80
    }
  }
}
resource "oci_core_network_security_group_security_rule" "dns" {
  network_security_group_id = oci_core_network_security_group.runtime.id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  udp_options {
    destination_port_range {
      min = 53
      max = 53
    }
  }
}
