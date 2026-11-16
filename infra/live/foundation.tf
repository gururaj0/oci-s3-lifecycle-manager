data "oci_identity_compartment" "demo" { id = var.compartment_ocid }
data "oci_identity_region_subscriptions" "home" { tenancy_id = var.tenancy_ocid }
locals {
  tags  = { application = "oci-s3-lcm", release = "0.2.0", mode = "live" }
  users = { for pair in setproduct(["sales-read", "sales-write"], ["1", "2"]) : "${pair[0]}-${pair[1]}" => pair[0] }
}
resource "oci_identity_user" "pool" {
  for_each       = local.users
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-${each.key}"
  email          = "${var.prefix}-${each.key}@lcm.invalid"
  description    = "Dedicated nonhuman ${each.value} credential leasing user"
  freeform_tags  = local.tags
}
resource "oci_identity_user_capabilities_management" "pool" {
  for_each                     = oci_identity_user.pool
  user_id                      = each.value.id
  can_use_console_password     = false
  can_use_api_keys             = false
  can_use_auth_tokens          = false
  can_use_smtp_credentials     = false
  can_use_customer_secret_keys = true
}
resource "oci_identity_user" "sentinel" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-foreign-sentinel"
  email          = "${var.prefix}-foreign-sentinel@lcm.invalid"
  description    = "Nonhuman negative IAM test target; no data permissions"
  freeform_tags  = local.tags
}
resource "oci_identity_group" "scope" {
  for_each       = toset(["sales-read", "sales-write"])
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-${each.value}"
  description    = "Fixed ${each.value} bucket scope"
}
resource "oci_identity_user_group_membership" "pool" {
  for_each = local.users
  user_id  = oci_identity_user.pool[each.key].id
  group_id = oci_identity_group.scope[each.value].id
}
resource "oci_objectstorage_bucket" "sales" {
  compartment_id = var.compartment_ocid
  namespace      = var.namespace
  name           = "${var.prefix}-sales"
  access_type    = "NoPublicAccess"
  freeform_tags  = local.tags
}
resource "oci_objectstorage_object" "sales_fixture" {
  namespace = var.namespace
  bucket    = oci_objectstorage_bucket.sales.name
  object    = "quarterly/revenue.csv"
  content   = "quarter,revenue\nQ1,124000\nQ2,148000\n"
}
resource "oci_objectstorage_object" "diagnostic_delete_fixture" {
  namespace = var.namespace
  bucket    = oci_objectstorage_bucket.sales.name
  object    = "diagnostics/delete-denied.txt"
  content   = "Disposable delete-denial diagnostic fixture."
}
resource "oci_identity_policy" "data_scope" {
  compartment_id = var.compartment_ocid
  name           = "${var.prefix}-data-scope"
  description    = "Bucket-scoped list/read and create/overwrite; deletion excluded"
  statements = [
    "Allow group id ${oci_identity_group.scope["sales-read"].id} to read buckets in compartment id ${var.compartment_ocid} where target.bucket.name = '${oci_objectstorage_bucket.sales.name}'",
    "Allow group id ${oci_identity_group.scope["sales-write"].id} to read buckets in compartment id ${var.compartment_ocid} where target.bucket.name = '${oci_objectstorage_bucket.sales.name}'",
    "Allow group id ${oci_identity_group.scope["sales-read"].id} to read objects in compartment id ${var.compartment_ocid} where target.bucket.name = '${oci_objectstorage_bucket.sales.name}'",
    "Allow group id ${oci_identity_group.scope["sales-write"].id} to {OBJECT_INSPECT, OBJECT_READ, OBJECT_CREATE, OBJECT_OVERWRITE} in compartment id ${var.compartment_ocid} where target.bucket.name = '${oci_objectstorage_bucket.sales.name}'"
  ]
}
resource "oci_kms_vault" "lcm" {
  compartment_id = var.compartment_ocid
  display_name   = var.prefix
  vault_type     = "DEFAULT"
  freeform_tags  = local.tags
}
resource "oci_kms_key" "lcm" {
  compartment_id      = var.compartment_ocid
  display_name        = "${var.prefix}-secrets"
  management_endpoint = oci_kms_vault.lcm.management_endpoint
  protection_mode     = "SOFTWARE"
  key_shape {
    algorithm = "AES"
    length    = 32
  }
  freeform_tags = local.tags
}
resource "random_password" "admin" {
  length      = 30
  special     = false
  min_numeric = 2
  min_upper   = 2
  min_lower   = 2
}
resource "random_password" "application" {
  length      = 30
  special     = false
  min_numeric = 2
  min_upper   = 2
  min_lower   = 2
}
resource "oci_vault_secret" "admin" {
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.lcm.id
  key_id         = oci_kms_key.lcm.id
  secret_name    = "${var.prefix}-bootstrap-admin"
  secret_content {
    content_type = "BASE64"
    content      = base64encode(random_password.admin.result)
  }
  lifecycle { ignore_changes = [secret_content] }
}
resource "oci_vault_secret" "application" {
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.lcm.id
  key_id         = oci_kms_key.lcm.id
  secret_name    = "${var.prefix}-database-app"
  secret_content {
    content_type = "BASE64"
    content      = base64encode(random_password.application.result)
  }
  lifecycle { ignore_changes = [secret_content] }
}
resource "oci_core_network_security_group" "runtime" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = "${var.prefix}-runtime"
  freeform_tags  = local.tags
}
resource "oci_core_network_security_group" "database" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = "${var.prefix}-database"
  freeform_tags  = local.tags
}
resource "oci_core_network_security_group_security_rule" "https" {
  network_security_group_id = oci_core_network_security_group.runtime.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}
resource "oci_core_network_security_group_security_rule" "db_in" {
  network_security_group_id = oci_core_network_security_group.database.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.runtime.id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 1521
      max = 1522
    }
  }
}
resource "oci_core_network_security_group_security_rule" "db_out" {
  network_security_group_id = oci_core_network_security_group.runtime.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.database.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 1521
      max = 1522
    }
  }
}
resource "oci_database_autonomous_database" "ledger" {
  compartment_id                      = var.compartment_ocid
  db_name                             = "LCMLEDGER"
  display_name                        = "${var.prefix}-ledger"
  db_workload                         = "OLTP"
  compute_model                       = "ECPU"
  compute_count                       = 2
  data_storage_size_in_gb             = 20
  is_auto_scaling_enabled             = false
  is_auto_scaling_for_storage_enabled = false
  license_model                       = "LICENSE_INCLUDED"
  admin_password                      = random_password.admin.result
  subnet_id                           = oci_core_subnet.private.id
  nsg_ids                             = [oci_core_network_security_group.database.id]
  private_endpoint_label              = "lcmledger"
  is_mtls_connection_required         = false
  freeform_tags                       = local.tags
  lifecycle {
    ignore_changes = [admin_password]
    precondition {
      condition     = data.oci_identity_compartment.demo.name == "Demo" && data.oci_identity_compartment.demo.state == "ACTIVE" && contains([for r in data.oci_identity_region_subscriptions.home.region_subscriptions : r.region_name if r.is_home_region], var.region)
      error_message = "Placement must be verified active Demo in the tenancy home region."
    }
  }
}
resource "oci_functions_application" "workers" {
  compartment_id             = var.compartment_ocid
  display_name               = var.prefix
  subnet_ids                 = [oci_core_subnet.private.id]
  network_security_group_ids = [oci_core_network_security_group.runtime.id]
  shape                      = "GENERIC_X86"
  freeform_tags              = local.tags
}
resource "oci_artifacts_container_repository" "worker" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.prefix}/worker"
  is_public      = false
}
output "foundation" {
  value = {
    vault_id                = oci_kms_vault.lcm.id
    vault_key_id            = oci_kms_key.lcm.id
    secret_compartment_id   = var.compartment_ocid
    worker_compartment_id   = var.compartment_ocid
    admin_secret_id         = oci_vault_secret.admin.id
    app_secret_id           = oci_vault_secret.application.id
    database_id             = oci_database_autonomous_database.ledger.id
    runtime_nsg_id          = oci_core_network_security_group.runtime.id
    function_application_id = oci_functions_application.workers.id
    pool                    = { for k, u in oci_identity_user.pool : k => u.id }
    sentinel_id             = oci_identity_user.sentinel.id
    groups                  = { for k, g in oci_identity_group.scope : k => g.id }
    bucket                  = oci_objectstorage_bucket.sales.name
    repository_id           = oci_artifacts_container_repository.worker.id
  }
}
resource "oci_identity_tag_namespace" "lcm" {
  compartment_id = var.compartment_ocid
  name           = "LcmLeaseV2"
  description    = "LCM schedule ownership for IAM resource boundaries"
}
resource "oci_identity_tag" "deployment" {
  tag_namespace_id = oci_identity_tag_namespace.lcm.id
  name             = "deployment"
  description      = "Approved LCM deployment owner"
}
resource "oci_objectstorage_bucket" "foreign" {
  compartment_id = var.compartment_ocid
  namespace      = var.namespace
  name           = "${var.prefix}-foreign"
  access_type    = "NoPublicAccess"
  freeform_tags  = local.tags
}
resource "oci_objectstorage_object" "foreign_fixture" {
  namespace = var.namespace
  bucket    = oci_objectstorage_bucket.foreign.name
  object    = "negative-scope.txt"
  content   = "Dedicated negative scope fixture; lease users must not read this."
}
