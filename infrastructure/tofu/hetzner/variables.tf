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
