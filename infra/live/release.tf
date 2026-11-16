locals {
  supplied_config = jsondecode(var.runtime_config_json == "" ? "{}" : var.runtime_config_json)
  runtime_config = merge(local.supplied_config, {
    mode                     = "oci", region = var.region, deployment_id = var.prefix,
    auth_mode                = var.gateway_jwt_enabled ? "gateway" : "jwt",
    gateway_trusted_networks = var.gateway_jwt_enabled ? [oci_core_subnet.public.cidr_block] : [],
    compartment_id           = var.compartment_ocid, scheduler_compartment_id = var.compartment_ocid,
    secret_compartment_id    = var.compartment_ocid, tenancy_id = var.tenancy_ocid,
    namespace                = var.namespace, db_user = "LCM", db_secret_id = oci_vault_secret.application.id,
    database_url             = [for p in oci_database_autonomous_database.ledger.connection_strings[0].profiles : p.value if p.tls_authentication == "SERVER" && p.consumer_group == "LOW"][0],
    vault_id                 = oci_kms_vault.lcm.id, vault_key_id = oci_kms_key.lcm.id,
    resource_tags            = { LcmLeaseV2 = { deployment = var.prefix } },
    service_users            = { for profile in ["sales-read", "sales-write"] : profile => [for k, u in oci_identity_user.pool : u.id if local.users[k] == profile] },
    profiles = { for profile in ["sales-read", "sales-write"] : profile => {
      bucket  = oci_objectstorage_bucket.sales.name, operations = profile == "sales-read" ? ["LIST", "GET"] : ["LIST", "GET", "PUT"],
      version = 1, group_id = oci_identity_group.scope[profile].id
    } },
    functions = {}
    # A new bootstrap must validate its own resources; never inherit a prior
    # environment's approval flags from an operator-supplied JWT config.
    integration_gates = {}
  })
  issuer_config = merge(local.runtime_config, { functions = local.deploy_functions ? merge({ delete = oci_functions_function.delete[0].id }, { for k, f in oci_functions_function.workers : k => f.id }) : {} })
}
resource "oci_objectstorage_bucket" "release" {
  compartment_id = var.compartment_ocid
  namespace      = var.namespace
  name           = "${var.prefix}-release"
  access_type    = "NoPublicAccess"
  freeform_tags  = local.tags
}
resource "oci_objectstorage_object" "release" {
  namespace    = var.namespace
  bucket       = oci_objectstorage_bucket.release.name
  object       = "source/${filesha256("${path.module}/lcm-live-source.tar.xz")}.tar.xz"
  source       = "${path.module}/lcm-live-source.tar.xz"
  content_type = "application/x-xz"
}
resource "oci_identity_policy" "release_read" {
  compartment_id = var.compartment_ocid
  name           = "${var.prefix}-release-read"
  description    = "Instance principal reads only its private immutable release bundle"
  statements     = ["Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to read objects in compartment id ${var.compartment_ocid} where target.bucket.name = '${oci_objectstorage_bucket.release.name}'"]
}
