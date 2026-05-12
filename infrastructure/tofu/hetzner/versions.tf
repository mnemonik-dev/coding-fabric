terraform {
  # Require OpenTofu >= 1.6.0 (MPL-2.0 fork of Terraform; identical HCL syntax)
  required_version = ">= 1.6.0"

  required_providers {
    # Hetzner Cloud provider — pinned to stable minor series ~> 1.45
    # "~>" means >= 1.45.0, < 1.46.0 (compatible patch updates only)
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.45"
    }
  }

  # State backend: local for now.
  # Task 24 wires encrypted remote state via GH Actions artifact + sops/age.
  # No backend block = default local state in terraform.tfstate.
}
