# coding-fabric — decisions log

## Task 01 — OpenTofu Hetzner VM + volume + firewall

**Status:** complete | **Commit:** 4fb136b | **Agent:** tofu-hetzner (terraform-master)

**Summary:** Created the full OpenTofu module under `infrastructure/tofu/hetzner/` that provisions a Hetzner Cloud CCX33 VM with an attached 200 GB ext4 volume and a deny-all-inbound firewall permitting only UDP/41641 (Tailscale handshake). Cloud-init injects the operator SSH pubkey; no public SSH port is opened.

**Key decisions:**
- Firewall default-deny inbound: the only inbound rule is UDP/41641 for Tailscale. All other ports (22, 80, 443, etc.) are blocked at the Hetzner firewall layer, not just by ufw. Defence-in-depth with ufw comes in Task 02 Ansible `base` role.
- `lifecycle { prevent_destroy = false }` on both server and volume is intentional for now to allow clean dev destroy/recreate cycles. A later steady-state wave should flip this to `true` once Restic backups (Task 06) are verified and the fabric is in production.
- Provider pinned to `~> 1.45` (hetznercloud/hcloud) — stable minor series with compatible patch updates only. Lock file (`terraform.lock.hcl`) will be committed on first `tofu init`.
- `automount = true` on both `hcloud_volume` and `hcloud_volume_attachment` — Hetzner auto-mounts at attachment time; Ansible base role (Task 02) writes a persistent `/etc/fstab` entry on top.
- Outputs use `try(hcloud_server.fabric.ipv6_address, null)` to gracefully handle regions where IPv6 is not assigned.
- State backend left local (no `backend` block). Task 24 wires encrypted remote state via GH Actions artifact + sops/age (per tech-spec D31).
- cloud-init creates OS user `op` (not `root`) with the injected SSH pubkey; `op` is in `sudo` and `docker` groups so Ansible can bootstrap without further user-creation steps.

**Self-review verdict:** pass

Security-auditor lens:
- No secrets in code; `hcloud_token` is `sensitive = true`, variable-only, never in outputs.
- Firewall denies all inbound except UDP/41641. No 0.0.0.0/0 TCP rules.
- No public SSH port. No public web ports.
- State is local; production encrypted state documented as Task 24.

Reliability-engineer lens:
- Explicit `depends_on` on `hcloud_firewall_attachment` and `hcloud_volume_attachment`.
- `lifecycle { prevent_destroy = false }` documented with rationale and upgrade path.
- `try()` on IPv6 output.
- Server has stable name + `role = "fabric-node"` label for Ansible dynamic inventory.
- Provider pinned with `~>`.

**Deferred smoke (operator to run during bootstrap):**
- `tofu init && tofu validate && tofu plan` in `infrastructure/tofu/hetzner/`
- After `tofu apply`: `nmap -sU -p 41641 <vm_ipv4>` shows UDP/41641 open|filtered
- After `tofu apply`: `nmap -sT -p 22,80,443 <vm_ipv4>` shows all ports filtered/closed
- `ssh root@<vm_ipv4> -o ConnectTimeout=5` must fail (refused or timeout)
- `bash infrastructure/tofu/hetzner/tests/test_tofu_plan.sh` with real `HCLOUD_TOKEN` for full plan

**Note on OpenTofu CLI:** OpenTofu binary (`tofu`) was not available on the local machine at task execution time. `tofu init` and `tofu validate` are deferred to operator bootstrap. HCL2 formatting was applied manually following `tofu fmt` conventions (2-space indents, attribute alignment to longest key per block).

**Files produced:**
- infrastructure/tofu/hetzner/main.tf (186 lines)
- infrastructure/tofu/hetzner/variables.tf (51 lines)
- infrastructure/tofu/hetzner/outputs.tf (36 lines)
- infrastructure/tofu/hetzner/versions.tf (17 lines)
- infrastructure/tofu/hetzner/tests/test_tofu_plan.sh (143 lines)
- infrastructure/tofu/hetzner/.gitignore (29 lines)

---

**Round 2 — fix:** commit `1f2f2ac24e1cf5fffd9cad8fb5eefef63f69d82d`

Findings addressed:
- [security medium] firewall race — inline `firewall_ids = [hcloud_firewall.fabric.id]` on `hcloud_server.fabric`; `hcloud_firewall_attachment` kept as idempotent state-reconciliation safety net with updated comment
- [reliability high] key rotation destroy — `lifecycle { ignore_changes = [user_data, labels] }` on `hcloud_server.fabric`; comment documents that Ansible (Task 02) owns ongoing key management
- [reliability high] resize destroys VM — `lifecycle { create_before_destroy = true }` on `hcloud_server.fabric`; multi-line block comment warns operator of root-disk wipe and mandates: (a) verify volume detach/reattach, (b) re-run full Ansible playbook after replacement
- [reliability medium] missing lockfile — documented operator requirement in `versions.tf` with the exact `tofu providers lock -platform=...` command to generate and commit `.terraform.lock.hcl`; also added local state sensitivity warning
- [reliability medium] automount ambiguity — added clarifying comments in `main.tf` explaining that volume-level `automount` covers initial creation and attachment-level `automount` is canonical for re-attach operations (both remain `true`; functional parity preserved)
- [reliability medium] label drift — `lifecycle { ignore_changes = [labels] }` on `hcloud_firewall.fabric`; same guard added to `hcloud_server.fabric` lifecycle block (combined with existing ignore_changes list) to protect `role=fabric-node` Ansible inventory label
- [reliability medium] test step numbering — corrected header comment from "3 steps" to "4 steps" and aligned all `Step N/M` announcements to `N/4`
- [reliability medium] plan file in module dir — `PLAN_FILE="$(mktemp)"` + `trap 'rm -f "${PLAN_FILE}"' EXIT`; plan written to temp file instead of `MODULE_DIR/last.tfplan`
- [security low] YAML injection via pubkey — two `validation {}` blocks on `operator_ssh_pubkey`: regex asserting single-line OpenSSH key type prefix + base64 payload, and length <= 1024 guard
- [security low] docker group on op user — removed `docker` from cloud-init `groups:` list for `op` user; inline comment documents that Task 02 Ansible base role adds it after Docker is installed
- [reliability low] `create_before_destroy` on volume attachment — added `lifecycle { create_before_destroy = true }` to `hcloud_volume_attachment.fabric_data`

Findings deferred (with rationale):
- [info secrets] hcloud_token env-var duplication — both approaches (provider env-var vs variable passthrough) are valid; harmonising without a working `tofu validate` run risks introducing a subtle breakage; defer to Task 24 or a dedicated housekeeping commit with live validation
- [info state] local tfstate sensitivity — documented in `versions.tf` with a WARNING comment (code change not required per reviewer)
- [info lifecycle] prevent_destroy follow-up — existing inline comment updated to reference Task 06 Restic backup verification as the trigger; no structural change needed
- [info firewall] Tailscale DERP comment accuracy — DERP comment in `main.tf` already updated to match reviewer's recommended wording during the main.tf rewrite
- [info volume LUKS] — out of scope for Task 01; must be claimed by Task 02 base role
- [security low] egress hardening — out of scope for cloud-firewall layer; belongs in Task 02 Ansible base role (ufw/nftables on VM); egress comment added to `main.tf` noting the conscious open-egress decision and the Task 02 ownership
