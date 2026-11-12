data "oci_identity_compartment" "demo" { id = var.compartment_ocid }
data "oci_identity_region_subscriptions" "current" { tenancy_id = var.tenancy_ocid }

locals {
  tags         = { "application" = "oci-s3-lcm", "release" = "0.1.0", "mode" = "mock" }
  home_regions = [for r in data.oci_identity_region_subscriptions.current.region_subscriptions : r.region_name if r.is_home_region]
}

resource "oci_core_security_list" "backend" {
  compartment_id = var.compartment_ocid
  vcn_id         = var.vcn_id
  display_name   = "${var.display_name}-empty-subnet-rules"
  freeform_tags  = local.tags
}
resource "oci_core_subnet" "backend" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = var.vcn_id
  cidr_block                 = var.private_subnet_cidr
  display_name               = "${var.display_name}-private"
  prohibit_public_ip_on_vnic = true
  route_table_id             = var.private_route_table_id
  security_list_ids          = [oci_core_security_list.backend.id]
  freeform_tags              = local.tags
  lifecycle {
    precondition {
      condition     = data.oci_identity_compartment.demo.name == "Demo" && data.oci_identity_compartment.demo.state == "ACTIVE"
      error_message = "The supplied compartment OCID must resolve to active Demo."
    }
    precondition {
      condition     = contains(local.home_regions, var.region)
      error_message = "Deployment region must be the tenancy home region."
    }
  }
}
resource "oci_core_network_security_group" "vm" {
  compartment_id = var.compartment_ocid
  vcn_id         = var.vcn_id
  display_name   = "${var.display_name}-backend"
  freeform_tags  = local.tags
}
resource "oci_core_network_security_group" "gateway" {
  compartment_id = var.compartment_ocid
  vcn_id         = var.vcn_id
  display_name   = "${var.display_name}-gateway"
  freeform_tags  = local.tags
}
resource "oci_core_network_security_group_security_rule" "ssh" {
  network_security_group_id = oci_core_network_security_group.vm.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${var.bastion_private_ip}/32"
  source_type               = "CIDR_BLOCK"
  description               = "SSH only from existing DemoBastion"
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}
resource "oci_core_network_security_group_security_rule" "api" {
  network_security_group_id = oci_core_network_security_group.vm.id
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
resource "oci_core_network_security_group_security_rule" "vm_egress" {
  for_each                  = toset(["80", "443"])
  network_security_group_id = oci_core_network_security_group.vm.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  description               = "OS repository and pinned Python package retrieval through existing NAT"
  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}
resource "oci_core_network_security_group_security_rule" "dns" {
  network_security_group_id = oci_core_network_security_group.vm.id
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
resource "oci_core_network_security_group_security_rule" "https" {
  for_each                  = toset(var.client_allowlist)
  network_security_group_id = oci_core_network_security_group.gateway.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}
resource "oci_core_network_security_group_security_rule" "gateway_egress" {
  network_security_group_id = oci_core_network_security_group.gateway.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.vm.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 8080
      max = 8080
    }
  }
}
resource "terraform_data" "release" {
  input = filesha256("${path.module}/assets/application.tar.xz.b64")
}

resource "oci_core_instance" "backend" {
  compartment_id      = var.compartment_ocid
  availability_domain = var.availability_domain
  display_name        = var.display_name
  shape               = var.shape
  shape_config {
    ocpus         = 1
    memory_in_gbs = 8
  }
  source_details {
    source_type             = "image"
    source_id               = var.image_id
    boot_volume_size_in_gbs = 50
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.backend.id
    assign_public_ip = false
    nsg_ids          = [oci_core_network_security_group.vm.id]
    display_name     = var.display_name
  }
  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64gzip(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      public_url         = "https://${oci_apigateway_gateway.demo.hostname}"
      application_b64    = file("${path.module}/assets/application.tar.xz.b64")
      application_sha256 = filesha256("${path.module}/assets/application.tar.xz.b64")
    }))
  }
  freeform_tags = local.tags
  lifecycle {
    create_before_destroy = true
    replace_triggered_by  = [terraform_data.release]
  }
}
resource "oci_apigateway_gateway" "demo" {
  compartment_id             = var.compartment_ocid
  display_name               = var.display_name
  endpoint_type              = var.gateway_type
  subnet_id                  = var.public_subnet_id
  network_security_group_ids = [oci_core_network_security_group.gateway.id]
  freeform_tags              = local.tags
}
resource "oci_apigateway_deployment" "demo" {
  compartment_id = var.compartment_ocid
  gateway_id     = oci_apigateway_gateway.demo.id
  display_name   = var.display_name
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
        type = "HTTP_BACKEND"
        url  = "http://${oci_core_instance.backend.private_ip}:8080/"
      }
    }
    routes {
      path    = "/{path*}"
      methods = ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]
      backend {
        type = "HTTP_BACKEND"
        url  = "http://${oci_core_instance.backend.private_ip}:8080/$${request.path[path]}"
      }
    }
  }
  freeform_tags = local.tags
}
