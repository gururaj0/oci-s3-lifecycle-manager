locals {
  deploy_functions = var.function_image != ""
}
resource "oci_functions_function" "delete" {
  count              = local.deploy_functions ? 1 : 0
  application_id     = oci_functions_application.workers.id
  display_name       = "${var.prefix}-delete"
  image              = var.function_image
  image_digest       = var.function_image_digest
  memory_in_mbs      = 1024
  timeout_in_seconds = 120
  config = {
    LCM_WORKLOAD      = "function"
    LCM_FUNCTION_ROLE = "delete"
    LCM_CONFIG_JSON   = jsonencode(local.runtime_config)
  }
  defined_tags  = { "LcmLeaseV2.deployment" = var.prefix, "LcmLeaseV2.role" = "delete" }
  freeform_tags = local.tags
  depends_on    = [oci_identity_tag.role, oci_identity_policy.function_images]
}
resource "oci_functions_function" "workers" {
  for_each           = local.deploy_functions ? toset(["setup", "reconcile", "sentinel"]) : toset([])
  application_id     = oci_functions_application.workers.id
  display_name       = "${var.prefix}-${each.key}"
  image              = var.function_image
  image_digest       = var.function_image_digest
  memory_in_mbs      = 1024
  timeout_in_seconds = 120
  config = {
    LCM_WORKLOAD      = "function"
    LCM_FUNCTION_ROLE = each.key == "sentinel" ? "delete" : each.key
    LCM_CONFIG_JSON   = jsonencode(local.runtime_config)
    LCM_FUNCTIONS     = jsonencode({ delete = oci_functions_function.delete[0].id })
  }
  defined_tags  = { "LcmLeaseV2.deployment" = var.prefix, "LcmLeaseV2.role" = each.key }
  freeform_tags = local.tags
  depends_on    = [oci_identity_tag.role, oci_identity_policy.function_images]
}
resource "oci_identity_tag" "role" {
  tag_namespace_id = oci_identity_tag_namespace.lcm.id
  name             = "role"
  description      = "LCM immutable worker role for invocation-only IAM"
}
resource "oci_identity_policy" "function_images" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-function-images"
  description    = "Functions service can read only the LCM worker repository"
  statements     = ["Allow service faas to read repos in compartment id ${var.compartment_ocid} where target.repo.name = '${oci_artifacts_container_repository.worker.display_name}'"]
}
resource "oci_identity_dynamic_group" "worker" {
  for_each       = local.deploy_functions ? toset(["setup", "delete", "reconcile"]) : toset([])
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-${each.key}-principal"
  description    = "Single LCM ${each.key} Function resource principal"
  matching_rule  = "ALL {resource.type='fnfunc', resource.id='${each.key == "delete" ? oci_functions_function.delete[0].id : oci_functions_function.workers[each.key].id}'}"
}
resource "oci_identity_dynamic_group" "schedules" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-schedules"
  description    = "Only LCM-tagged schedules; no per-lease IAM updates"
  matching_rule  = "ALL {resource.type='resourceschedule', resource.compartment.id='${var.compartment_ocid}', tag.LcmLeaseV2.deployment.value='${var.prefix}'}"
}
resource "oci_identity_policy" "worker" {
  for_each       = oci_identity_dynamic_group.worker
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-${each.key}-permissions"
  description    = "LCM ${each.key}: schema credential, scoped schedules, and owned-key cleanup"
  statements = concat(each.key == "reconcile" ? ["Allow dynamic-group id ${each.value.id} to {GROUP_INSPECT, USER_INSPECT} in tenancy"] : [], [
    "Allow dynamic-group id ${each.value.id} to read secret-bundles in compartment id ${var.compartment_ocid} where target.secret.id = '${oci_vault_secret.application.id}'",
    "Allow dynamic-group id ${each.value.id} to inspect resource-schedule-family in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group id ${each.value.id} to {RESOURCE_SCHEDULE_READ, RESOURCE_SCHEDULE_UPDATE, RESOURCE_SCHEDULE_DELETE} in compartment id ${var.compartment_ocid} where target.resource.tag.LcmLeaseV2.deployment = '${var.prefix}'"
    ], each.key != "delete" ? [
    "Allow dynamic-group id ${each.value.id} to {RESOURCE_SCHEDULE_CREATE} in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group id ${each.value.id} to use tag-namespaces in compartment id ${var.compartment_ocid} where target.tag-namespace.id = '${oci_identity_tag_namespace.lcm.id}'"
    ] : [], each.key != "setup" ? concat([
      "Allow dynamic-group id ${each.value.id} to inspect secrets in compartment id ${var.compartment_ocid}",
      "Allow dynamic-group id ${each.value.id} to {SECRET_READ, SECRET_DELETE} in compartment id ${var.compartment_ocid} where all {target.vault.id = '${oci_kms_vault.lcm.id}', target.secret.name = /${var.prefix}-replay-*/}"
  ], flatten([for u in oci_identity_user.pool : ["Allow dynamic-group id ${each.value.id} to {USER_INSPECT, USER_READ} in tenancy where target.user.id = '${u.id}'", "Allow dynamic-group id ${each.value.id} to {USER_UPDATE, USER_SECRETKEY_REMOVE} in tenancy where all {target.user.id = '${u.id}', request.operation = 'DeleteCustomerSecretKey'}"]])) : [])
}
resource "oci_identity_policy" "invocation" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-scheduler-invocation"
  description    = "Invocation only, scoped to the LCM deletion and reconciliation Function tags"
  statements = [
    "Allow dynamic-group id ${oci_identity_dynamic_group.schedules.id} to use fn-invocation in compartment id ${var.compartment_ocid} where all {target.resource.tag.LcmLeaseV2.deployment = '${var.prefix}', any {target.resource.tag.LcmLeaseV2.role = 'delete', target.resource.tag.LcmLeaseV2.role = 'reconcile'}}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.schedules.id} to {FN_FUNCTION_READ} in compartment id ${var.compartment_ocid} where all {target.resource.tag.LcmLeaseV2.deployment = '${var.prefix}', any {target.resource.tag.LcmLeaseV2.role = 'delete', target.resource.tag.LcmLeaseV2.role = 'reconcile'}}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to use fn-invocation in compartment id ${var.compartment_ocid} where all {target.resource.tag.LcmLeaseV2.deployment = '${var.prefix}', any {target.resource.tag.LcmLeaseV2.role = 'delete', target.resource.tag.LcmLeaseV2.role = 'reconcile'}}",
    "Allow service cloudEvents to use fn-invocation in compartment id ${var.compartment_ocid} where all {target.resource.tag.LcmLeaseV2.deployment = '${var.prefix}', target.resource.tag.LcmLeaseV2.role = 'setup'}"
  ]
}
resource "oci_events_rule" "keys" {
  count          = local.deploy_functions ? 1 : 0
  compartment_id = var.tenancy_ocid
  display_name   = "${var.prefix}-customer-secret-key-events"
  description    = "Home-region credential event; handler filters explicit managed users and trusts committed leases"
  condition      = jsonencode({ eventType = ["com.oraclecloud.identityControlPlane.CreateCustomerSecretKey", "com.oraclecloud.identitycontrolplane.CreateCustomerSecretKey", "com.oraclecloud.identitycontrolplane.createcustomersecretkey", "com.oraclecloud.identitycontrolplane.createsecretkey"] })
  is_enabled     = true
  actions {
    action {
      action_type = "FAAS"
      function_id = oci_functions_function.workers["setup"].id
      is_enabled  = true
    }
  }
}
output "functions" {
  value = local.deploy_functions ? merge({ delete = oci_functions_function.delete[0].id }, { for k, f in oci_functions_function.workers : k => f.id }) : {}
}
resource "oci_identity_policy" "function_network" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-function-network"
  description    = "Functions service network attachment in Demo; no runtime network-management permissions"
  statements     = ["Allow service faas to use virtual-network-family in compartment id ${var.compartment_ocid}"]
}
