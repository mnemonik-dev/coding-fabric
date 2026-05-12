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
  #
  # WARNING: the local terraform.tfstate may contain sensitive resource
  # attributes. Run tofu on a workstation with full-disk encryption and
  # DO NOT sync this directory to cloud storage (Dropbox, iCloud, etc.)
  # until Task 24 lands and remote state with sops/age encryption is wired.
}

# LOCKFILE REQUIREMENT — operator checklist:
#
# After the FIRST `tofu init` in a new checkout, you MUST commit the generated
# .terraform.lock.hcl (or .tofu.lock.hcl for newer OpenTofu versions) so that
# all subsequent CI runs and team checkouts use the SAME provider binary hash.
#
# Without a committed lockfile, `tofu init` resolves the latest 1.45.x patch
# at runtime — two consecutive CI runs on different days can use different
# provider binaries, defeating the purpose of the ~> 1.45 pin.
#
# To generate the lockfile for the canonical CI platform(s):
#   tofu -chdir=infrastructure/tofu/hetzner providers lock \
#     -platform=linux_amd64 \
#     -platform=darwin_amd64
#
# Then: git add infrastructure/tofu/hetzner/.terraform.lock.hcl && git commit
#
# The .gitignore in this directory explicitly does NOT ignore .terraform.lock.hcl
# (see the comment in that file explaining why).
