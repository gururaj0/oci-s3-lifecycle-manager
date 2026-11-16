# Browser routes remain on port 8080. Only the authenticated lifecycle route can
# address the lifecycle API listener on port 8081.
resource "oci_core_network_security_group_security_rule" "gateway_lifecycle" {
  network_security_group_id = oci_core_network_security_group.gateway.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.runtime.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 8081
      max = 8081
    }
  }
}
resource "oci_core_network_security_group_security_rule" "runtime_lifecycle" {
  network_security_group_id = oci_core_network_security_group.runtime.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.gateway.id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 8081
      max = 8081
    }
  }
}
resource "oci_core_network_security_group_security_rule" "gateway_jwks" {
  network_security_group_id = oci_core_network_security_group.gateway.id
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
