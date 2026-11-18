# Identity Domains diagnostic: operation filters reject membership PATCH requests.
# Resource restrictions remain; GROUP_UPDATE/USER_UPDATE also permit metadata updates
# and membership addition by cleanup principals. Runtime cleanup only removes membership.
# Prefix users have no standing group membership. Runtime grants are lease-owned.
resource "oci_identity_user" "prefix_pool" {
  for_each       = local.users
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-prefix-user-${each.key}"
  email          = "${var.prefix}-prefix-user-${each.key}@lcm.invalid"
  description    = "Isolated temporary prefix lease user; no permanent group membership"
  freeform_tags  = local.tags
}
resource "oci_identity_user_capabilities_management" "prefix_pool" {
  for_each                     = oci_identity_user.prefix_pool
  user_id                      = each.value.id
  can_use_console_password     = false
  can_use_api_keys             = false
  can_use_auth_tokens          = false
  can_use_smtp_credentials     = false
  can_use_customer_secret_keys = true
}
resource "oci_identity_policy" "prefix_issuer" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-prefix-issuer"
  description    = "On-demand deployment-owned groups and temporary membership for isolated prefix users"
  statements = concat([
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {GROUP_CREATE} in tenancy where target.group.name = /${var.prefix}-prefix-*/",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {GROUP_UPDATE} in tenancy where target.group.name = /${var.prefix}-prefix-*/",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {POLICY_READ} in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {POLICY_CREATE} in compartment id ${var.compartment_ocid} where target.policy.name = /${var.prefix}-prefix-*/"
    ], flatten([for u in oci_identity_user.prefix_pool : [
      "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {USER_INSPECT, USER_READ} in tenancy where target.user.id = '${u.id}'",
      "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {USER_UPDATE} in tenancy where target.user.id = '${u.id}'",
      "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {USER_SECRETKEY_ADD, USER_SECRETKEY_REMOVE} in tenancy where all {target.user.id = '${u.id}', any {request.operation = 'CreateCustomerSecretKey', request.operation = 'CreateSecretKey', request.operation = 'DeleteCustomerSecretKey'}}"
  ]]))
}
resource "oci_identity_policy" "prefix_cleanup" {
  for_each       = { for k, g in oci_identity_dynamic_group.worker : k => g if k != "setup" }
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-prefix-${each.key}"
  description    = "Delete owned keys and reconcile prefix membership; no key creation or policy creation"
  statements = concat([
    "Allow dynamic-group id ${each.value.id} to {GROUP_INSPECT, USER_INSPECT} in tenancy",
    "Allow dynamic-group id ${each.value.id} to {GROUP_UPDATE} in tenancy where target.group.name = /${var.prefix}-prefix-*/"
    ], flatten([for u in oci_identity_user.prefix_pool : [
      "Allow dynamic-group id ${each.value.id} to {USER_INSPECT, USER_READ} in tenancy where target.user.id = '${u.id}'",
      "Allow dynamic-group id ${each.value.id} to {USER_UPDATE} in tenancy where target.user.id = '${u.id}'",
      "Allow dynamic-group id ${each.value.id} to {USER_SECRETKEY_REMOVE} in tenancy where all {target.user.id = '${u.id}', request.operation = 'DeleteCustomerSecretKey'}"
  ]]))
}
output "prefix_users" {
  value = { for profile in ["sales-read", "sales-write"] : profile => [for k, u in oci_identity_user.prefix_pool : u.id if local.users[k] == profile] }
}
