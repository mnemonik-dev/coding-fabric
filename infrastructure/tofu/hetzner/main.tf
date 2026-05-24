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
# Tailscale uses UDP/41641 for direct peer-to-peer connections.
# UDP/41641 inbound allows Tailscale direct peer-to-peer from operator clients.
# DERP relay fallback uses DERP servers as intermediaries (outbound from this
# server), so no additional inbound rule is needed for DERP.
# No public SSH port: operator access is exclusively via Tailscale
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

  # TEMPORARY operator debug access: SSH from operator's current public IP.
  # Added 2026-05-23 because Tailscale GUI app on operator macOS wasn't
  # bringing the tailnet up, and the VM was otherwise unreachable for
  # debugging telegram-ai-agent (not responding). REMOVE this rule once
  # Tailscale is restored — public SSH erodes the tailnet-only invariant
  # documented in the FIREWALL NOTE below.
  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = ["194.87.227.58/32"]
    description = "TEMP debug SSH from operator IP — REMOVE after Tailscale restored"
  }

  # Outbound: allow all. The VM must reach package mirrors, Tailscale DERP
  # servers, Hetzner metadata, GitHub, and other external services.
  # Egress is intentionally open at the cloud-firewall layer (L3 between
  # internet and VM). Per-protocol egress hardening lives in Task 02 Ansible
  # base role (ufw/nftables on the VM) to guard against compromised agent
  # exfiltration or C2 callback.
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

  # Ignore operator console labels added for incident tagging or Hetzner system
  # labels (e.g. backup-related). Without this guard, tofu apply would silently
  # remove any label not declared here, including Ansible inventory labels.
  lifecycle {
    ignore_changes = [labels]
  }
}

################################################################################
# Server — Hetzner Cloud CCX33 dedicated VM in fsn1.
# Ubuntu 24.04 LTS is the only supported base image for the Ansible roles.
# cloud-init user_data injects the operator SSH public key on first boot.
# The server has no public SSH firewall rule; SSH is reachable only after
# Tailscale comes up (Task 02 Ansible role bootstrapped via cloud-init or
# operator one-time manual step described in bootstrap-checklist.md).
#
# FIREWALL NOTE: firewall_ids attaches the firewall atomically at server
# creation time — no race window between server boot and firewall attachment
# (unlike a separate hcloud_firewall_attachment resource, which leaves the
# server reachable from the public internet until the attachment apply
# completes). The hcloud_firewall_attachment resource is kept below as an
# idempotent safety net for state reconciliation, but the inline attachment
# here is the authoritative protection mechanism.
#
# SERVER TYPE CHANGE WARNING:
# Changing var.server_type (e.g. ccx33 -> ccx43) triggers a server
# DESTROY + RECREATE — Hetzner does not support in-place resize for
# dedicated CCX instances via API. The root disk is WIPED; the attached
# data volume (hcloud_volume.fabric_data) survives as a separate resource.
# Operator checklist on resize:
#   (a) Verify volume detaches and reattaches cleanly (check hcloud_volume_attachment
#       state after apply; automount = true on the attachment handles re-mount).
#   (b) Run the full Ansible playbook after the new server is up to re-provision
#       the OS (Tailscale, Docker, Caddy, base hardening, etc.).
#   (c) create_before_destroy below minimises the downtime window by starting
#       the new server before tearing down the old one.
################################################################################

resource "hcloud_server" "fabric" {
  name        = var.server_name
  server_type = var.server_type
  image       = "ubuntu-24.04"
  location    = var.hcloud_location

  # Attach the firewall atomically at creation time (no race window).
  # See the firewall note in the block comment above.
  firewall_ids = [hcloud_firewall.fabric.id]

  # Enable both IPv4 and IPv6 public addresses.
  # IPv6 may be absent in some Hetzner regions; outputs.tf uses try() to guard.
  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  # cloud-init: bootstraps VM with operator user + Tailscale on first boot.
  # The firewall blocks public SSH (port 22) — Tailscale-via-cloud-init is the
  # ONLY way the VM becomes reachable for Ansible. Without it, deploy is stuck
  # with chicken-and-egg: SSH needs tailnet, tailnet needs Ansible, Ansible
  # needs SSH.
  #
  # docker group is intentionally omitted: the group does not exist on a fresh
  # Ubuntu 24.04 image, causing cloud-init warnings. The Task 02 Ansible base
  # role adds op to the docker group after Docker is installed.
  # The ssh_authorized_keys list is built dynamically: the operator's persistent
  # key plus an optional ephemeral CI key. Use a plain conditional string
  # interpolation (not %{ if ~} template directives) because the latter behaved
  # unreliably inside <<- heredocs in run 26174191941 — the CI key was silently
  # omitted from the rendered cloud-config and the runner's SSH timed out
  # because the VM had no authorized key for op@ci-deploy-*.
  user_data = <<-CLOUDINIT
    #cloud-config
    users:
      - name: op
        groups: [sudo]
        shell: /bin/bash
        sudo: "ALL=(ALL) NOPASSWD:ALL"
        ssh_authorized_keys:
          - ${var.operator_ssh_pubkey}
          ${var.ci_ssh_pubkey != "" ? "- ${var.ci_ssh_pubkey}" : "# no CI key"}
    package_update: true
    packages:
      - curl
      - ca-certificates
      - gnupg
      - lsb-release
    runcmd:
      # Install + join tailnet in a single multi-line script.
      # Each runcmd entry is a separate shell invocation; using a literal block
      # avoids YAML flow-mapping ambiguity (the previous "{ ... }" form broke
      # cloud-init parser — see archived run 26157201377).
      - |
        set -e
        curl -fsSL https://tailscale.com/install.sh | sh
        # NOTE: --ssh REMOVED. Tailscale SSH on port 22 intercepts incoming
        # connections and requires interactive browser auth from the client,
        # which deadlocks the CI ansible-deploy step (run 26170050584 showed
        # "Tailscale SSH requires an additional check. To authenticate, visit:
        # https://login.tailscale.com/a/..."). Standard OpenSSH on port 22
        # authenticates via the SSH key injected above into op's
        # authorized_keys — works headless. Operator's SSH access path is
        # unchanged: traffic to port 22 is reachable only over Tailscale
        # because the cloud firewall blocks public 22 ingress.
        tailscale up \
          --authkey=${var.tailscale_auth_key} \
          --hostname=${var.server_name} \
          --accept-routes
        if ! tailscale status >/dev/null 2>&1; then
          echo "ERROR: tailscale up failed — VM will not be reachable for Ansible"
          exit 1
        fi
        echo "Tailscale joined tailnet successfully"
  CLOUDINIT

  labels = {
    managed_by = "opentofu"
    fabric     = var.server_name
    env        = "production"
    # Ansible dynamic inventory uses this label to discover the fabric host.
    role = "fabric-node"
  }

  # lifecycle notes:
  #
  # prevent_destroy = false: intentional for the dev phase; clean destroy/recreate
  # cycles are needed. Set to true after Task 06 (Restic backups) is verified and
  # the fabric is in steady-state operation, so accidental destroys require a full
  # cold-start recovery.
  #
  # ignore_changes = [user_data]: cloud-init runs only on first boot; Hetzner does
  # not re-run it on subsequent applies. Rotating operator_ssh_pubkey must be done
  # via Ansible (Task 02 base role) — not by modifying user_data and re-applying,
  # which would force a server destroy+recreate and wipe the root disk. Tofu owns
  # bootstrap injection only; Ansible owns ongoing key management.
  #
  # create_before_destroy = true: when a server type change forces replacement,
  # the new server is created (and Ansible-provisioned) before the old server is
  # destroyed, reducing the outage window. See the SERVER TYPE CHANGE WARNING above.
  #
  # ignore_changes = [labels]: guards against operator console labels or Hetzner
  # system labels being removed by a subsequent tofu apply. The role=fabric-node
  # label is declared here and will always be enforced on resource creation; only
  # post-creation label drift is ignored.
  lifecycle {
    prevent_destroy       = false
    ignore_changes        = [user_data, labels]
    create_before_destroy = true
  }
}

################################################################################
# Firewall attachment — idempotent safety net for state reconciliation.
# The hcloud_server.fabric resource already attaches the firewall inline via
# firewall_ids at creation time (atomic, no race window). This attachment
# resource ensures the firewall association is represented in Tofu state and
# reconciled on drift (e.g. if manually detached in the Hetzner console).
################################################################################

resource "hcloud_firewall_attachment" "fabric" {
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
  name     = "${var.server_name}-data"
  size     = var.volume_size_gb
  location = var.hcloud_location
  format   = "ext4"
  # NOTE: removed `automount = true` because Hetzner provider requires it to be
  # paired with `server` at volume-creation time, which couples volume lifecycle
  # to server lifecycle. Instead, automount is set on the SEPARATE
  # hcloud_volume_attachment resource below — this preserves volume independence
  # (volume survives server rebuild/replace).

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
# automount = true here is the canonical setting for re-attach operations
# (controls Hetzner automount on subsequent attach after detach/reattach cycles).
################################################################################

resource "hcloud_volume_attachment" "fabric_data" {
  volume_id = hcloud_volume.fabric_data.id
  server_id = hcloud_server.fabric.id
  # automount is controlled at attachment time (canonical for re-attach ops).
  automount = true

  depends_on = [
    hcloud_server.fabric,
    hcloud_volume.fabric_data,
  ]

  # create_before_destroy: when the server is replaced (e.g. server_type change),
  # the new volume attachment is created before the old one is destroyed, ensuring
  # the volume is re-attached to the new server before Tofu removes the old
  # attachment object from state. This avoids a brief unmount window once the
  # fabric has live data on the volume.
  lifecycle {
    create_before_destroy = true
  }
}
