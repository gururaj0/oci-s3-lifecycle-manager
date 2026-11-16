variable "tenancy_ocid" { type = string }
variable "compartment_ocid" { type = string }
variable "region" { type = string }
variable "availability_domain" { type = string }
variable "vcn_id" { type = string }
variable "public_subnet_id" { type = string }
variable "private_subnet_id" { type = string }
variable "ssh_public_key" { type = string }
variable "bastion_private_ip" { type = string }
variable "image_id" { type = string }
variable "namespace" { type = string }
variable "prefix" { default = "lcm-live-v2" }
variable "bootstrap_enabled" { default = true }
variable "image_build_enabled" { default = false }
variable "function_image" {
  type    = string
  default = ""
}
variable "runtime_config_json" {
  type    = string
  default = ""
}

variable "function_image_digest" {
  type    = string
  default = ""
}

variable "gateway_jwt_enabled" {
  type    = bool
  default = true
}
