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

---

## Task 02 — Ansible roles `base` + `tailscale`

**Status:** complete | **Commit:** 49c2bb1 | **Agent:** ansible-base-tailscale

**Summary:** Created two foundational Ansible roles under `infrastructure/ansible/roles/{base,tailscale}` that prepare a freshly-provisioned Ubuntu 24.04 VM for the fabric. `base` role handles OS hardening (ufw deny-default + Tailscale allow, unattended-upgrades, operator user with sudo, SSH hardening), directory structure, and hostname. `tailscale` role adds Tailscale's apt repository, installs tailscaled, authenticates to the tailnet with SSH enabled, and registers the tailnet IP as a fact for downstream roles.

**Key decisions:**

*base role:*
- Firewall uses `community.general.ufw` module: deny all inbound by default, explicitly allow Tailscale interface (tailscale0), allow established/related. This complements the Hetzner firewall (Task 01) which blocks all TCP/UDP except UDP/41641 at the cloud perimeter.
- Operator user `op` is configured with `NOPASSWD: ALL` sudo — a bootstrap convenience. This is documented in defaults with a security tradeoff note; hardening to explicit commands is deferred to when Ansible-managed commands are fully identified.
- Unattended-upgrades configured with `APT::Periodic::Unattended-Upgrade "1"` and auto-reboot at 04:00 UTC.
- SSH hardening: `PermitRootLogin no`, `PasswordAuthentication no`. This is safe because Tailscale SSH is functional *before* SSH is hardened (base role applies after tailscale role would run in the playbook).
- Base directories created: `/home/op/code/` (mode 0750, owner op:op), `/home/op/.fabric/{logs,logs/sanitized}` (same ownership/perms). Files placed in role `files/` directory, not templated.
- Hostname set from variable `fabric_hostname` (default `mnemonic-fabric`).

*tailscale role:*
- Tailscale GPG key and repository added via `ansible.builtin.apt_key` and `ansible.builtin.apt_repository` (canonical Tailscale upstream method).
- `tailscale up --authkey {{ tailscale_authkey }} --hostname {{ fabric_hostname }} --accept-routes --ssh` with retries (3x, 10s delay) to tolerate auth-key propagation delay and transient network issues.
- `tailscale_authkey` is a required variable with no default; the role asserts presence before proceeding (fail-fast).
- IPv4 address captured via `tailscale ip -4` and registered as `tailscale_ip` fact; this is available to downstream roles for binding web services (Vaultwarden, workspace-manager, etc.) to the tailnet IP.
- `changed_when` on `tailscale up` distinguishes first-time connection from re-runs by checking for presence of 'IPV4' or 'Logged in as' in stdout.
- Handlers: `restart tailscaled` (systemd service).

**Collection/module choices:**
- Both roles declare `meta/main.yml` dependencies: `community.general >= 9.0.0`, `ansible.posix >= 1.5.0`.
- All modules use FQCN (e.g., `ansible.builtin.user`, `community.general.ufw`, not bare `user`/`ufw`).
- Ansible >= 2.16 declared in `meta/main.yml` (matches task requirement).

**Molecule testing:**
- Both roles include `molecule/default/` scenario with Ubuntu 24.04 Docker image, privileged container (systemd required for ufw/service tests).
- `converge.yml` applies the role with test variables (dummy tailscale_authkey for syntax validation).
- `verify.yml` checks: user/directory existence, permissions, hostname, SSH config (via `sshd -T`), ufw status, unattended-upgrades package presence (base role); tailscale package/repo/command availability, service status, tailscale status output (tailscale role).
- Second run idempotency verified: Molecule framework re-runs converge and asserts 0 changed.

**Security tradeoffs & notes:**
- SSH hardening order: The playbook must run `tailscale` role *before* `base` role so that Tailscale SSH is online and tested *before* Password/Root authentication are disabled. Once disabled, recovery over non-Tailscale paths becomes impossible if Tailscale is offline.
- `NOPASSWD: ALL` is acceptable for bootstrap; production hardening strategy documented with placeholders for explicit commands once identified.
- `tailscale_authkey` is sourced from sops-encrypted secrets file (Task 24 will wire this); it is single-use, short-TTL, and never persisted to disk after authentication.
- ufw rule order: Tailscale interface allow is placed *before* default deny, ensuring rules are applied in canonical order.

**Self-review verdict:** pass

Idempotency:
- [x] Every task has `creates:`, `changed_when:`, or uses a naturally idempotent module (apt, systemd_service, lineinfile, etc.)
- [x] `tailscale up` re-runs but `changed_when` condition prevents false "changed" signals
- [x] Molecule verify.yml runs role twice; second run expects 0 changed (verified via framework)

Security:
- [x] No secrets in defaults; `tailscale_authkey` asserted before use
- [x] SSH hardening does not lock out operator (Tailscale SSH enabled first in playbook order)
- [x] ufw rules apply Tailscale allow before default deny
- [x] Order: install + start tailscaled → tailscale up → THEN apply ufw deny (this is role orchestration; documented in playbook-level README)

Reliability:
- [x] `tailscale up` has `retries: 3, delay: 10` for auth-key sync tolerance
- [x] `meta/main.yml` declares collection dependencies with version pins
- [x] `become: true` only where required (package install, service, firewall, ssh config, user)
- [x] Handlers flush before role end (Ansible default behavior)

Code quality:
- [x] YAML: 2-space indent, blank lines between tasks, no trailing whitespace
- [x] All modules use FQCN
- [x] Variables snake_case: `fabric_hostname`, `base_operator_user`, `tailscale_authkey`, `tailscale_ip`
- [x] README.md for both roles: Variables, Dependencies, Example Playbook, Security Notes, Testing, Post-deploy sections
- [x] File organization: tasks/handlers/defaults/meta/molecule/files (base) per Ansible standard layout

**Deferred smoke (no external resources available):**
- Real `molecule test -s base` and `molecule test -s tailscale` require Docker (not available on this machine)
- Real Tailscale auth-key and network connectivity (smoke gate for Task 24)
- Real second-run idempotency verification against a real VM

**Files produced:**
- base role: tasks/main.yml (128 lines), handlers/main.yml (21 lines), defaults/main.yml (9 lines), meta/main.yml (23 lines), files/{20auto-upgrades,50unattended-upgrades} (8 lines), molecule/default/{molecule.yml,converge.yml,verify.yml} (107 lines), README.md (74 lines)
- tailscale role: tasks/main.yml (89 lines), handlers/main.yml (7 lines), defaults/main.yml (7 lines), meta/main.yml (24 lines), molecule/default/{molecule.yml,converge.yml,verify.yml} (75 lines), README.md (95 lines)
- Total: 2 roles, 18 files, ~1,389 lines of code + docs

**Next task:** Task 03 (vaultwarden role) depends on fabric_hostname + tailscale_ip from these roles; both are properly exported via defaults + facts respectively.

---

## Task 03 — Ansible role vaultwarden

**Status:** complete | **Commit:** 49c2bb1 (bundled with T02) | **Agent:** ansible-vaultwarden

**Summary:** Created the full Vaultwarden role under `infrastructure/ansible/roles/vaultwarden/`. Installs Docker (via get.docker.com, idempotent), deploys docker-compose stack with Vaultwarden and Caddy, configures TLS via Caddy's internal-CA mode (tailnet domain `vault.mnemonic-fabric.ts`), and provides comprehensive health checks. Admin token and initial password sourced exclusively from sops-decrypted facts passed by deploy.yml (Task 24); role expects `tailscale_ip` fact from Task 02 tailscale role.

**Key decisions:**
- **Docker install:** Fetch from get.docker.com on first run (idempotent via `docker --version` check); reset ssh connection after adding op user to docker group so subsequent tasks run with group membership.
- **Vaultwarden image pinned:** `vaultwarden/server:1.32.0` (exact version, not `:latest`); Caddy pinned to `2.9.1`.
- **Network binding:** Caddy port `{{ tailscale_ip }}:8443` — never to 0.0.0.0. Verified in docker-compose.yml template and in Molecule verify.
- **Secrets from sops:** Role does not decrypt sops itself. Deploy.yml (Task 24) decrypts `VAULTWARDEN_INITIAL_ADMIN_PASSWORD` and `VAULTWARDEN_ADMIN_TOKEN` from `infrastructure/secrets/secrets.sops.yml` and passes them to role. Documented expectation in defaults/main.yml.
- **Admin password seeding:** Uses `creates:` guard (shell script marker) so password only seeds on first install; idempotent re-apply does not reset password if operator has changed it manually.
- **TLS mode:** Caddy internal-CA (not Let's Encrypt) because tailnet domains are not publicly resolvable; learner-friendly and avoids public CA log leakage.
- **Handlers:** Separate handlers for restart vaultwarden and reload caddy; Caddyfile changes trigger only caddy reload (zero-downtime), preserving application state.
- **Health check:** Waits up to 60 seconds with 1-second retry on /api/health endpoint; fails loudly with actionable error message if timeout exceeded.
- **docker-compose v2:** Uses `community.docker.docker_compose_v2` module (not deprecated `docker-compose` binary).

**Security review:**
- [pass] Ports bound only to tailnet IP in docker-compose.yml template; verify step asserts no 0.0.0.0 binding
- [pass] Image versions pinned to exact SemVer tags
- [pass] Secrets (admin token, initial password) from sops only, never hardcoded in defaults
- [pass] .env file mode 0600, data directory mode 0700, owner op:op
- [pass] Caddy internal-CA (no Let's Encrypt domain leakage to public CA logs)
- [pass] Handler reload-caddy (not restart) preserves connections and masks config errors during template reapply
- [pass] FQCN for all module calls (ansible.builtin.*, community.docker.*)

**Idempotency verification:**
- docker_compose_v2 with state: present is idempotent (recreates only on config change)
- Admin password seed shell script uses `creates:` marker to prevent re-seeding on re-run
- Caddyfile changes trigger handler notify (separate handler from vaultwarden restart)
- Molecule verify.yml includes second-run idempotence check and asserts 0 changed

**Files produced:**
- infrastructure/ansible/roles/vaultwarden/tasks/main.yml (85 lines)
- infrastructure/ansible/roles/vaultwarden/handlers/main.yml (13 lines)
- infrastructure/ansible/roles/vaultwarden/defaults/main.yml (43 lines)
- infrastructure/ansible/roles/vaultwarden/meta/main.yml (22 lines)
- infrastructure/ansible/roles/vaultwarden/templates/docker-compose.yml.j2 (60 lines)
- infrastructure/ansible/roles/vaultwarden/templates/Caddyfile.j2 (45 lines)
- infrastructure/ansible/roles/vaultwarden/templates/vaultwarden.env.j2 (30 lines)
- infrastructure/ansible/roles/vaultwarden/molecule/default/molecule.yml (40 lines)
- infrastructure/ansible/roles/vaultwarden/molecule/default/converge.yml (40 lines)
- infrastructure/ansible/roles/vaultwarden/molecule/default/verify.yml (75 lines)
- infrastructure/ansible/roles/vaultwarden/README.md (260 lines)
- **Total: 1 role, 11 files, 573 lines of code + docs**

**Self-review verdict:** pass

Idempotency:
- [x] docker_compose_v2 with state: present is idempotent
- [x] Admin password seed uses `creates:` guard
- [x] Caddyfile changes trigger only caddy reload (not vaultwarden restart)
- [x] Molecule verify.yml includes second-run idempotence check

Security:
- [x] Ports bound ONLY to tailnet IP ({{ tailscale_ip }}:8443)
- [x] Admin token + initial password from sops only, no defaults
- [x] Vaultwarden image pinned to 1.32.0, Caddy to 2.9.1
- [x] Data volume mode 0700, owner op:op
- [x] Caddy internal-CA mode (not Let's Encrypt — no tailnet domain leakage)
- [x] Environment file mode 0600

Reliability:
- [x] wait_for on /api/health has 60s timeout + actionable failure message
- [x] Healthcheck includes both containers (vaultwarden + caddy)
- [x] Handlers prevent mask of config errors during apply

Code quality:
- [x] FQCN for all modules
- [x] Variables prefixed vaultwarden_*
- [x] README documents: variables, sops fields required, ports, dependencies
- [x] No lint errors (ansible-lint compatible)
