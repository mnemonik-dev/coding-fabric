################################################################################
# Provider configuration
# Token is read from the hcloud_token variable (sensitive; sourced from env
# HCLOUD_TOKEN in CI and local operator runs).
################################################################################

provider "hcloud" {
  token = var.hcloud_token
}

################################################################################
# Firewall — deny all public inbound except Tailscale handshake port UDP/41641.
# Tailscale uses UDP/41641 for direct peer-to-peer connections and DERP relay
# fallback. No public SSH port: operator access is exclusively via Tailscale
# (configured in Task 02 Ansible role `tailscale`).
# No public HTTP/HTTPS: Caddy binds to the tailnet IP only (Task 03).
################################################################################

resource "hcloud_firewall" "fabric" {
  name = "${var.server_name}-fw"

  # Allow Tailscale direct/DERP handshake traffic from any source.
  # This is the sole permitted public inbound rule; everything else is dropped
  # implicitly by Hetzner's default-deny firewall model.
  rule {
    direction   = "in"
    protocol    = "udp"
    port        = "41641"
    source_ips  = ["0.0.0.0/0", "::/0"]
    description = "Tailscale direct peer-to-peer and DERP handshake"
  }

  # Outbound: allow all. The VM must reach package mirrors, Tailscale DERP
  # servers, Hetzner metadata, GitHub, and other external services.
  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "any"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Allow all TCP egress"
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "any"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Allow all UDP egress"
  }

  rule {
    direction       = "out"
    protocol        = "icmp"
    destination_ips = ["0.0.0.0/0", "::/0"]
    description     = "Allow ICMP egress (ping, traceroute)"
  }

  labels = {
    managed_by = "opentofu"
    fabric     = var.server_name
  }
}

################################################################################
# Server — Hetzner Cloud CCX33 dedicated VM in fsn1.
# Ubuntu 24.04 LTS is the only supported base image for the Ansible roles.
# cloud-init user_data injects the operator SSH public key on first boot.
# The server has no public SSH firewall rule; SSH is reachable only after
# Tailscale comes up (Task 02 Ansible role bootstrapped via cloud-init or
# operator one-time manual step described in bootstrap-checklist.md).
################################################################################

resource "hcloud_server" "fabric" {
  name        = var.server_name
  server_type = var.server_type
  image       = "ubuntu-24.04"
  location    = var.hcloud_location

  # Enable both IPv4 and IPv6 public addresses.
  # IPv6 may be absent in some Hetzner regions; outputs.tf uses try() to guard.
  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  # cloud-init: create operator OS user with the injected SSH public key.
  # This is the bootstrap access path used until Tailscale is active.
  user_data = <<-CLOUDINIT
    #cloud-config
    users:
      - name: op
        groups: [sudo, docker]
        shell: /bin/bash
        sudo: "ALL=(ALL) NOPASSWD:ALL"
        ssh_authorized_keys:
          - ${var.operator_ssh_pubkey}
    package_update: true
    packages:
      - curl
      - ca-certificates
      - gnupg
      - lsb-release
  CLOUDINIT

  labels = {
    managed_by = "opentofu"
    fabric     = var.server_name
    env        = "production"
    # Ansible dynamic inventory uses this label to discover the fabric host.
    role       = "fabric-node"
  }

  # lifecycle.prevent_destroy = false is intentional for now:
  # during development we want clean destroy/recreate cycles.
  # Set to true in a later wave once the fabric is in steady-state operation
  # and accidental destroys would require a full cold-start recovery.
  lifecycle {
    prevent_destroy = false
  }
}

################################################################################
# Firewall attachment — apply the firewall to the server.
# Explicit depends_on ensures the server exists before we try to attach.
################################################################################

resource "hcloud_firewall_attachment" "fabric" {
  # Attach the fabric firewall to the fabric server.
  firewall_id = hcloud_firewall.fabric.id
  server_ids  = [hcloud_server.fabric.id]

  depends_on = [
    hcloud_server.fabric,
    hcloud_firewall.fabric,
  ]
}

################################################################################
# Volume — 200 GB ext4 block device for persistent fabric data.
# Separate from the server root disk so it survives server rebuild/replace.
# Contains: Docker volumes, worktree data, sccache, Vaultwarden data dir,
# Restic local cache, and any other stateful data not in the OS image.
################################################################################

resource "hcloud_volume" "fabric_data" {
  name      = "${var.server_name}-data"
  size      = var.volume_size_gb
  location  = var.hcloud_location
  format    = "ext4"
  # automount = true asks Hetzner to mount the volume automatically after
  # attachment. The Ansible base role (Task 02) writes /etc/fstab for
  # persistence across reboots.
  automount = true

  labels = {
    managed_by = "opentofu"
    fabric     = var.server_name
  }

  # lifecycle.prevent_destroy = false: same reasoning as the server resource.
  # The volume holds stateful data; ensure Restic backups (Task 06) are verified
  # before setting prevent_destroy = true in steady state.
  lifecycle {
    prevent_destroy = false
  }
}

################################################################################
# Volume attachment — attach the persistent volume to the fabric server.
# Explicit depends_on on the server (not implied by the server_id reference)
# is listed for clarity and to guard against any future refactoring that might
# break implicit ordering.
################################################################################

resource "hcloud_volume_attachment" "fabric_data" {
  # Attach the data volume to the fabric server.
  volume_id = hcloud_volume.fabric_data.id
  server_id = hcloud_server.fabric.id
  automount = true

  depends_on = [
    hcloud_server.fabric,
    hcloud_volume.fabric_data,
  ]
}
