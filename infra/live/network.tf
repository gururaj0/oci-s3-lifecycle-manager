resource "oci_core_vcn" "lcm" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.prefix}-vcn"
  cidr_blocks    = ["10.73.0.0/16"]
  dns_label      = "lcmlive"
  freeform_tags  = local.tags
}
resource "oci_core_internet_gateway" "lcm" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = var.prefix
}
resource "oci_core_nat_gateway" "lcm" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = var.prefix
}
resource "oci_core_local_peering_gateway" "lcm" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = "${var.prefix}-to-demo"
}
resource "oci_core_local_peering_gateway" "demo" {
  compartment_id = var.compartment_ocid
  vcn_id         = var.vcn_id
  display_name   = "${var.prefix}-from-demo"
  peer_id        = oci_core_local_peering_gateway.lcm.id
}
resource "oci_core_security_list" "empty" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = "${var.prefix}-nsg-only"
}
resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  route_rules {
    destination       = local.oracle_services.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.lcm.id
  }
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_nat_gateway.lcm.id
  }
  route_rules {
    destination       = "172.16.0.0/16"
    network_entity_id = oci_core_local_peering_gateway.lcm.id
  }
}
resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.lcm.id
  }
}
resource "oci_core_subnet" "private" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.lcm.id
  cidr_block                 = "10.73.1.0/24"
  dns_label                  = "private"
  display_name               = "${var.prefix}-private"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.empty.id]
}
resource "oci_core_subnet" "public" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.lcm.id
  cidr_block        = "10.73.0.0/24"
  dns_label         = "public"
  display_name      = "${var.prefix}-gateway"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.empty.id]
}
output "peering" {
  value = {
    demo_lpg_id       = oci_core_local_peering_gateway.demo.id
    private_subnet_id = oci_core_subnet.private.id
    vcn_id            = oci_core_vcn.lcm.id
  }
}
data "oci_core_services" "lcm" {}
locals {
  oracle_services = [for s in data.oci_core_services.lcm.services : s if can(regex("All .* Services In Oracle Services Network", s.name))][0]
}
resource "oci_core_service_gateway" "lcm" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.lcm.id
  display_name   = "${var.prefix}-oracle-services"
  services { service_id = local.oracle_services.id }
}
