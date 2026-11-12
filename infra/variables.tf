variable "tenancy_ocid" { type = string }
variable "compartment_ocid" { type = string }
variable "region" { type = string }
variable "availability_domain" { type = string }
variable "vcn_id" { type = string }
variable "public_subnet_id" { type = string }
variable "private_route_table_id" { type = string }
variable "private_subnet_cidr" {
  type    = string
  default = "172.16.61.0/24"
}
variable "ssh_public_key" { type = string }
variable "bastion_private_ip" { type = string }
variable "image_id" { type = string }
variable "display_name" {
  type    = string
  default = "oci-s3-lcm-demo"
}
variable "shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}
variable "gateway_type" {
  type    = string
  default = "PUBLIC"
  validation {
    condition     = contains(["PUBLIC", "PRIVATE"], var.gateway_type)
    error_message = "Choose PUBLIC or PRIVATE gateway."
  }
}
variable "client_allowlist" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}
variable "deployment_profile" {
  type    = string
  default = "mock"
  validation {
    condition     = var.deployment_profile == "mock"
    error_message = "Only mock is released. Live OCI issuance is gated by docs/acceptance.md."
  }
}
