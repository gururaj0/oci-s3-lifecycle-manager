resource "oci_ons_notification_topic" "lcm" {
  compartment_id = var.compartment_ocid
  name           = "${var.prefix}-alerts"
  description    = "LCM credential and cleanup alarms; operator subscribes an approved destination"
}
resource "oci_identity_policy" "metrics" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-metrics"
  description    = "LCM cleanup principals publish only the LCM metric namespace"
  statements = concat([
    "Allow dynamic-group id ${oci_identity_dynamic_group.issuer.id} to {METRIC_WRITE} in compartment id ${var.compartment_ocid} where target.metrics.namespace = 'lcm_live'"
  ], [for k, g in oci_identity_dynamic_group.worker : "Allow dynamic-group id ${g.id} to {METRIC_WRITE} in compartment id ${var.compartment_ocid} where target.metrics.namespace = 'lcm_live'" if k == "reconcile"])
}
locals {
  alarm_queries = {
    heartbeat  = "CleanupHeartbeat[1m]{deployment = \"${var.prefix}\", workload = \"instance\"}.absent()"
    overdue    = "OverdueKeys[1m]{deployment = \"${var.prefix}\"}.max() > 0"
    quarantine = "QuarantinedUsers[1m]{deployment = \"${var.prefix}\"}.max() > 0"
    unhealthy  = "CleanupHealthy[1m]{deployment = \"${var.prefix}\"}.min() < 1"
  }
}
resource "oci_monitoring_alarm" "lcm" {
  for_each              = local.alarm_queries
  compartment_id        = var.compartment_ocid
  display_name          = "${var.prefix}-${each.key}"
  destinations          = [oci_ons_notification_topic.lcm.id]
  is_enabled            = true
  metric_compartment_id = var.compartment_ocid
  namespace             = "lcm_live"
  query                 = each.value
  severity              = "CRITICAL"
  pending_duration      = "PT2M"
  body                  = "LCM cleanup requires operator attention. Check the redacted ledger audit and continue revocation retries."
}
resource "oci_resource_scheduler_schedule" "reconcile" {
  count              = local.deploy_functions ? 1 : 0
  compartment_id     = var.compartment_ocid
  display_name       = "${var.prefix}-independent-reconciler"
  action             = "START_RESOURCE"
  recurrence_type    = "ICAL"
  recurrence_details = "FREQ=HOURLY;INTERVAL=1"
  local_time_zone    = "UTC"
  resources { id = oci_functions_function.workers["reconcile"].id }
  defined_tags  = { "LcmLeaseV2.deployment" = var.prefix }
  freeform_tags = local.tags
  depends_on    = [oci_identity_policy.invocation]
}
output "alerts_topic_id" { value = oci_ons_notification_topic.lcm.id }
