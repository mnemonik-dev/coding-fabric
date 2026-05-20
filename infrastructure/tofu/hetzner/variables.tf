variable "hcloud_token" {
  # Hetzner Cloud API token — sensitive; never log or output this value.
  # Sourced from environment variable HCLOUD_TOKEN via provider configuration.
  # In CI: injected from GitHub Actions secret HCLOUD_TOKEN.
  description = "Hetzner Cloud API token (sensitive). Set via env var HCLOUD_TOKEN or pass -var."
  type        = string
  sensitive   = true
}

variable "operator_ssh_pubkey" {
  # Operator's SSH public key injected into the VM via cloud-init user_data.
  # Operator SSH access is only over Tailscale (Task 02); this key is the
  # credential for that access. Must be the full public key string, e.g.:
  #   ssh-ed25519 AAAA... user@host
  description = "Operator SSH public key to inject into cloud-init authorized_keys."
  type        = string

  # Validate the key is a well-formed single-line OpenSSH public key.
  # The raw value is interpolated directly into a YAML heredoc in user_data;
  # an unvalidated multi-line or specially-crafted value could inject extra
  # YAML keys and create additional users (YAML injection defense-in-depth).
  validation {
    condition     = can(regex("^(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+( [^\\n\\r]*)?$", var.operator_ssh_pubkey))
    error_message = "operator_ssh_pubkey must be a single-line OpenSSH public key (ssh-rsa, ssh-ed25519, or ecdsa-sha2-nistp256/384/521)."
  }

  # Sanity-check length: a typical ed25519 key is ~100 chars; RSA-4096 is ~760.
  # A value over 1024 chars almost certainly indicates a pasted private key or
  # a certificate blob — reject it early rather than silently truncate YAML.
  validation {
    condition     = length(var.operator_ssh_pubkey) <= 1024
    error_message = "operator_ssh_pubkey must be <= 1024 characters (typical public keys are well under this limit)."
  }
}

variable "hcloud_location" {
  # Hetzner datacenter region. fsn1 = Falkenstein, EU.
  # Must match the volume location — volume and server must be in the same location.
  description = "Hetzner Cloud datacenter location (e.g. fsn1, nbg1, hel1)."
  type        = string
  default     = "fsn1"
}

variable "server_type" {
  # CCX33 = dedicated 4 vCPU, 16 GB RAM. Dedicated (not shared) vCPU line.
  # Upsize to CCX43 (8 vCPU / 32 GB) if watchdog disk/CPU alerts fire frequently.
  # One-line change here + tofu apply; no data migration required for attached volume.
  description = "Hetzner Cloud server type (e.g. ccx33, ccx43)."
  type        = string
  default     = "ccx33"
}

variable "volume_size_gb" {
  # Attached block volume for persistent data (worktrees, Docker volumes, sccache).
  # 200 GB provides ~150 GB usable after OS headroom and 50 GB worktree budget.
  description = "Size in GB for the attached block volume."
  type        = number
  default     = 200
}

variable "server_name" {
  # Stable DNS-safe name. Ansible inventory (Task 24) queries this via
  # Hetzner API label selectors — do not change after first apply without
  # updating the inventory template too.
  description = "Name assigned to the Hetzner server resource and its Ansible inventory entry."
  type        = string
  default     = "mnemonic-fabric"
}

variable "ci_ssh_pubkey" {
  # Ephemeral CI deploy key, generated per workflow run by deploy-fabric.yml
  # and injected into the op user's authorized_keys via cloud-init. The
  # corresponding private key is uploaded as a 1-day artifact and consumed by
  # the ansible-deploy job. Operator's persistent key (operator_ssh_pubkey) is
  # also injected; both keys grant op user SSH. Empty string disables the
  # extra key (e.g. for local operator-only applies).
  description = "Optional second SSH public key (CI ephemeral) for the op user."
  type        = string
  default     = ""

  validation {
    condition     = var.ci_ssh_pubkey == "" || can(regex("^(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+( [^\\n\\r]*)?$", var.ci_ssh_pubkey))
    error_message = "ci_ssh_pubkey must be empty or a single-line OpenSSH public key."
  }
}

variable "tailscale_auth_key" {
  # Tailscale auth key (tskey-auth-...) used by cloud-init at first boot to
  # join the VM to the operator's tailnet. After this, all admin access
  # (SSH, Ansible, fabric services) goes via the tailnet — public SSH is
  # firewall-blocked by design.
  #
  # Generate at: https://login.tailscale.com/admin/settings/keys
  # Settings: reusable, ephemeral=false (we want persistent membership),
  # pre-approved, tag tag:fabric, TTL 90 days.
  #
  # CI injects via TF_VAR_tailscale_auth_key from GH secret TAILSCALE_AUTH_KEY
  # (mirrored from sops field tailscale_auth_key).
  description = "Tailscale auth key for VM cloud-init tailnet join (sensitive)."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^tskey-auth-[A-Za-z0-9-]+$", var.tailscale_auth_key))
    error_message = "tailscale_auth_key must start with 'tskey-auth-' (got something else — probably wrong key type)."
  }
}
