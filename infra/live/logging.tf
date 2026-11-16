resource "oci_logging_log_group" "lcm" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.prefix}-logs"
  description    = "LCM Function invocation diagnostics; no credential payload logging"
}
resource "oci_logging_log" "functions" {
  display_name       = "${var.prefix}-function-invocations"
  log_group_id       = oci_logging_log_group.lcm.id
  log_type           = "SERVICE"
  retention_duration = 90
  configuration {
    compartment_id = var.compartment_ocid
    source {
      source_type = "OCISERVICE"
      service     = "functions"
      resource    = oci_functions_application.workers.id
      category    = "invoke"
    }
  }
}
