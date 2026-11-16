resource "oci_core_network_security_group" "gateway" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = "${var.prefix}-api-gateway"
}
resource "oci_core_network_security_group_security_rule" "gateway_https" {
  network_security_group_id = oci_core_network_security_group.gateway.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}
resource "oci_core_network_security_group_security_rule" "gateway_backend" {
  network_security_group_id = oci_core_network_security_group.gateway.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.runtime.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 8080
      max = 8080
    }
  }
}
resource "oci_core_network_security_group_security_rule" "runtime_api" {
  network_security_group_id = oci_core_network_security_group.runtime.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.gateway.id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 8080
      max = 8080
    }
  }
}
resource "oci_apigateway_gateway" "live" {
  compartment_id             = var.compartment_ocid
  display_name               = var.prefix
  endpoint_type              = "PUBLIC"
  subnet_id                  = oci_core_subnet.public.id
  network_security_group_ids = [oci_core_network_security_group.gateway.id]
}
resource "oci_apigateway_deployment" "live" {
  compartment_id = var.compartment_ocid
  gateway_id     = oci_apigateway_gateway.live.id
  display_name   = var.prefix
  path_prefix    = "/"
  specification {
    request_policies {
      rate_limiting {
        rate_in_requests_per_second = 10
        rate_key                    = "CLIENT_IP"
      }
    }
    routes {
      path    = "/"
      methods = ["GET"]
      backend {
        type                       = "HTTP_BACKEND"
        connect_timeout_in_seconds = 10
        send_timeout_in_seconds    = 10
        read_timeout_in_seconds    = 120
        url                        = "http://${oci_core_instance.runtime.private_ip}:8080/"
      }
    }
    routes {
      path    = "/{path*}"
      methods = ["GET", "POST", "DELETE", "HEAD", "OPTIONS"]
      backend {
        type                       = "HTTP_BACKEND"
        connect_timeout_in_seconds = 10
        send_timeout_in_seconds    = 10
        read_timeout_in_seconds    = 120
        url                        = "http://${oci_core_instance.runtime.private_ip}:8080/$${request.path[path]}"
      }
    }
  }
}
output "api_endpoint" { value = oci_apigateway_deployment.live.endpoint }
