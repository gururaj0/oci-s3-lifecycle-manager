terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    random = { source = "hashicorp/random", version = "= 3.7.2" }
    oci    = { source = "oracle/oci", version = "= 9.13.0" }
  }
}
provider "oci" { region = var.region }
