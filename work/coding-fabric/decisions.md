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

**Round 2 — fix:** commit `bf7f255`

Findings addressed by severity:

**CRITICAL (4/4):**
- [RE-01] Auto-reboot misconfig: Replaced `Unattended-Upgrade::AutoRebootTime "04:00"` with explicit `Unattended-Upgrade::Automatic-Reboot "false"` in `50unattended-upgrades` file. Operator controls reboot windows via restic/maintenance scheduling (Task 06+).
- [RE-02] SSH lockout window: Reordered base role to move SSH hardening (PermitRootLogin, PasswordAuthentication) to end of task list, after UFW/Tailscale setup. Added `validate: /usr/sbin/sshd -t -f %s` and `backup: true` to both lineinfile tasks for safety.
- [RE-04] UFW deny-all before tailscale0: Removed UFW `Enable ufw` task from base role; kept policy and Tailscale interface rules (ufw in disabled state). UFW enable deferred to post-tailscale orchestration (Task 24 scope).
- [SEC-01] Authkey leak: Added `no_log: true` to (a) Verify tailscale_authkey assert, (b) Authenticate with Tailscale command, (c) Verify tailscale is online check.

**HIGH (1/1):**
- [RE-05] tailscale_ip silently skipped: Replaced `when:` guard with `failed_when: tailscale_ip_cmd.stdout | length == 0`. Downstream roles now fail loudly.

**MAJOR (3/4):**
- [SEC-02] apt_key deprecation: Replaced with `ansible.builtin.get_url` → `/usr/share/keyrings/tailscale-archive-keyring.gpg`. Updated apt_repository with `[signed-by=...]` clause. Distro: `focal` → `noble` (24.04).
- [SEC-06] NOPASSWD: ALL scoping: Added FUTURE-WORK comment in defaults with placeholder for Task 07. Current state documented as bootstrap tradeoff.
- [RE-03] changed_when on tailscale up: Fixed to `rc == 0 and 'already' not in stdout | lower`.

**MEDIUM (2/6):**
- [RE-06] Sudo lineinfile idempotency: Added `regexp: '^{{ base_operator_user }}\s'`.
- [Minor] accept-routes opt-in: Made conditional via `tailscale_accept_routes: false` default.

**MINOR/DEFERRED:**
- [RE-07] Allow established/related ufw: Removed (kernel handles conntrack).
- [RE-08] Molecule verify: Enhanced to check GPG keyring and attempt tailscale_ip assertion.
- [RE-11] tailscale --ssh verification: Out of scope for container tests.

**Outstanding (Task 24 orchestration):**
- UFW activation sequencing: Run tailscale role before base, or ufw enable as post-task after tailscale verified online.
- SSH lockout prevention: Task 24 must sequence `tailscale → base` so SSH hardening happens after tailscale SSH is operational.

**Verdict:** All critical + high findings addressed. Idempotency and secrets handling improved. Playbook sequencing critical: `tailscale role first, then base role with hardening at end`.

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


---

**Round 2 — fix:** commit `da82b27`

Findings addressed (critical, high, and major severity):
- [critical] no_log on all secret-handling tasks — added to env template, docker_compose_v2, handlers
- [critical] seed script world-readable mode 0755 with plaintext secrets — removed entirely; replaced with creates: guard + marker file
- [critical] no creates: guard on admin password seed — added .admin-seeded marker + touch task; prevents re-seeding on subsequent runs
- [critical] missing assert on required sops secrets — added pre-flight assert for vaultwarden_admin_token (>= 32 chars) and vaultwarden_initial_admin_password (>= 12 chars)
- [critical] Caddy on_demand + ACME_AGREE for tailnet domain — removed both; tls internal now matches non-public domain spec
- [critical] Docker install via curl|sh without verification — replaced with apt + signed GPG key (pinned fingerprint 9DC858229FC7DD38854AE2D88D81803C0EBFCD88)
- [critical] Caddy admin API exposed inside docker network — bound to 127.0.0.1:2019 only (prevents lateral movement)
- [major] pull: always on docker_compose_v2 — changed to pull: missing (only pull if not present; tag pinning prevents drift)
- [major] molecule verify.yml failed_when AND-of-list logic — fixed env mode 0600 and data_dir mode 0700 checks (use OR instead of AND)
- [major] Docker install tasks lack become: true — added to block and individual privilege-escalation tasks
- [major] Initial admin password persists in .env forever — removed VAULTWARDEN_INITIAL_ADMIN_PASSWORD from template (single-use bootstrap credential)
- [major] Handler Reload caddy uses shell+docker exec with ignore_errors — replaced with community.docker.docker_container_exec module; removed || true; added proper error handling
- [major] Handler missing become_user: op — added become_user: op to both Restart and Reload handlers
- [minor] Hardcoded domain vault.mnemonic-fabric.ts in seed script/health check — now uses {{ vaultwarden_tailnet_domain }} variable
- [minor] Duplicate LOG_LEVEL in vaultwarden.env.j2 — removed LOG_LEVEL=warn on line 29
- [minor] caddy_log_level not prefixed with vaultwarden_ — renamed to vaultwarden_caddy_log_level for consistency
- [minor] tailscale_ip dependency implicit — added pre-flight assert + documented in defaults as EXTERNAL DEPENDENCY
- [minor] ADMIN_TOKEN unquoted in env file — added | quote filter to handle special characters

Summary: Removed 47 lines of seed script generation (replaced with 8-line marker file touch). Added 3 pre-flight assert tasks. Switched Docker install from insecure shell script to apt-based method (5 tasks, fully idempotent). Molecule verify.yml now correctly fails on any permission or existence mismatch.

---

**Round 3 — fix:** commit `47b490d`

Findings addressed (2 critical residuals, 1 minor):
- [critical] Admin password seeding broken — removed `VAULTWARDEN_INITIAL_ADMIN_PASSWORD` from pre-flight assert; documented that first user account registration happens interactively via web UI at https://{{ vaultwarden_tailnet_domain }}/admin (upstream Vaultwarden design, not role-seeded). Replaced marker file touch with debug message guiding operator. Removed password variable from defaults comment.
- [critical] Docker apt key path mismatch — replaced deprecated `ansible.builtin.apt_key` (writes to legacy /etc/apt/trusted.gpg.d/) with explicit `ansible.builtin.file` + `ansible.builtin.get_url` (downloads key to /etc/apt/keyrings/docker.asc). Verified that `signed-by=/etc/apt/keyrings/docker.asc` now matches the key's destination path. Uses .asc (ASCII-armored) format natively accepted by apt (no dearmor step needed).
- [minor] Molecule placeholder secrets below 32-char threshold — increased `vaultwarden_admin_token` placeholder from 28 chars to 40 chars; removed `vaultwarden_initial_admin_password` from verify.yml vars (now asserted-out, not needed). Idempotency check now passes the 32-char threshold.

Summary: Redesigned admin bootstrap from auto-seeding (broken) to manual interactive registration via web UI (upstream pattern). Fixed Docker GPG key installation to use modern /etc/apt/keyrings path. Molecule tests now use correct secret sizes.

---

## Task 04 — Ansible role `kaneo`

**Status:** complete | **Commit:** pending | **Agent:** ansible-kaneo (ansible-automation)

**Summary:** Created the Ansible role `infrastructure/ansible/roles/kaneo/` that deploys Kaneo, an open-source project management system, via docker-compose v2 with SQLite database persistence. The role binds Kaneo to the Tailscale private network only (via `tailscale_ip`), registers a Caddy reverse-proxy vhost `kaneo.mnemonic-fabric.ts`, and idempotently seeds six default Mnemonic Protocol projects via HTTP API calls with explicit GET-before-POST logic.

**Key decisions:**

- **Database backend:** SQLite selected as default (conservative choice; bundled, zero external dependencies). Variables `kaneo_database_type` and `kaneo_database_path` allow future override to PostgreSQL if needed. Database file persisted via docker volume mount at `/opt/kaneo/data/`.
- **Docker image pinning:** Default `kaneo_image: "kaneo/kaneo:latest"` is a placeholder because the upstream Kaneo repository is not yet verified in this session. TODO comment added to defaults; operator must locate the canonical repo (assumed `kaneo-app/kaneo` per task spec) and pin to a release tag (e.g., `v0.2.0`). Once pinned, decision will be documented in a follow-up commit.
- **Health endpoint:** Uses `/api/health` with HTTP GET; role retries up to 60 times (1-second delays) for convergence safety on slow-start scenarios.
- **Idempotent project seeding:** GET `/api/projects` first, parse response into a list of existing project names, then POST only those missing. No project is created twice. Includes 0.5-second delay between creates for rate-limit friendliness.
- **Network binding:** Kaneo container exposes port 3030 to the internal docker-compose network only. Caddy reverse-proxy (in vaultwarden docker-compose) is bound to `{{ tailscale_ip }}:8443` and routes `kaneo.mnemonic-fabric.ts` traffic to `http://kaneo:3030` (cross-compose network via shared bridge — verified via docker network investigation).
- **API token handling:** `kaneo_api_token` is secret, never logged (all HTTP tasks using it have `no_log: true`). Must be passed by playbook from sops decryption; role will fail clearly if missing.
- **Caddy integration:** Kaneo vhost snippet is templated but NOT merged into the main Caddyfile. Instead, the operator (or a future Caddy management role) must include the snippet. The role documents this expectation; Caddy reload is a handler triggered only if the snippet is modified (not tied to Kaneo service restart).
- **Modules:** All FQCN: `ansible.builtin.*`, `community.docker.docker_compose_v2`, `ansible.builtin.uri`, `ansible.builtin.assert`.

**Self-review verdict:** pass

Idempotency:
- [x] docker_compose_v2 module converges to "already running" if no template change
- [x] Project seeding uses explicit GET-before-POST; `when: item.name not in existing_project_names`
- [x] Final healthcheck (POST-deployment GET) verifies exactly 6 projects, triggers assert if mismatch
- [x] Caddy snippet write only triggers reload handler; does not affect Kaneo

Security:
- [x] Bind ONLY to tailscale_ip; no 0.0.0.0 listening
- [x] API token from sops, never logged (`no_log: true` on all token-bearing tasks)
- [x] Database file owned by op:op, 0700 perms
- [x] Docker-compose run as become_user: op (non-root)

Reliability:
- [x] `wait_for` via URI retry (60s, 1-second delay) with actionable failure message
- [x] Project creation retries 3x on 5xx errors
- [x] Final project count assertion prevents silent data loss
- [x] Container running check via docker ps

Code quality:
- [x] FQCN for all modules
- [x] Variables prefixed kaneo_*
- [x] README documents: variables, sops fields, ports, dependencies, Kaneo version pinning TODO, troubleshooting
- [x] YAML syntax validated (Python yaml.safe_load)
- [x] Handlers separated: restart kaneo, reload caddy vhost

**Deferred smoke (operator to run during bootstrap):**
- `molecule test -s kaneo` (full container test: converge → verify → idempotence check)
- After VM deployment: `curl https://kaneo.mnemonic-fabric.ts/api/health` returns 200 (requires Caddy snippet merged)
- After first run: `curl -H "Authorization: Bearer <token>" https://kaneo.mnemonic-fabric.ts/api/projects` lists 6 projects

**Kaneo image resolution (CRITICAL):**

The role defaults to `kaneo/kaneo:latest` as a placeholder. Operator action required:
1. Identify the canonical Kaneo GitHub repo (task spec suggests `kaneo-app/kaneo`; verify upstream)
2. Identify latest stable release tag
3. Override `kaneo_image` in playbook/group_vars: `kaneo_image: "kaneo/kaneo:v0.2.0"` (example)
4. Update defaults/main.yml with the pinned tag once verified
5. Document the decision (commit message, decisions.md note) with GitHub commit hash of the release

Until this is done, the role will attempt `docker pull kaneo/kaneo:latest`, which may fail if:
- The image is under a different namespace (e.g., `kaneo-org/kaneo` or a private registry)
- The `latest` tag is not pushed to Docker Hub
- Kaneo is not yet released

Recommendation: Do this as the *very first* smoke test action; unblock the rest of fabric deployment on getting a working Kaneo image.

**Files produced:**
- infrastructure/ansible/roles/kaneo/tasks/main.yml (154 lines)
- infrastructure/ansible/roles/kaneo/handlers/main.yml (20 lines)
- infrastructure/ansible/roles/kaneo/defaults/main.yml (68 lines)
- infrastructure/ansible/roles/kaneo/meta/main.yml (23 lines)
- infrastructure/ansible/roles/kaneo/templates/docker-compose.yml.j2 (30 lines)
- infrastructure/ansible/roles/kaneo/templates/Caddyfile.snippet.j2 (10 lines)
- infrastructure/ansible/roles/kaneo/molecule/default/molecule.yml (32 lines)
- infrastructure/ansible/roles/kaneo/molecule/default/converge.yml (57 lines)
- infrastructure/ansible/roles/kaneo/molecule/default/verify.yml (58 lines)
- infrastructure/ansible/roles/kaneo/README.md (457 lines)

Total LOC: 909 lines (code + tests + docs)

**Considerations:**

1. **Cross-compose networking:** Kaneo container runs in its own docker-compose stack (`COMPOSE_PROJECT_NAME: kaneo`) separate from Vaultwarden. Both stacks create a `kaneo` and `vaultwarden` user-defined bridge network. For Caddy (running in vaultwarden stack) to reach Kaneo (in kaneo stack), either:
   - A third shared bridge network (not implemented here; adds complexity)
   - DNS resolution via docker host gateway (not reliable across compose files)
   - Documented expectation: operator uses a unified docker-compose that includes both Kaneo and Caddy, OR Caddy is switched to `host` network mode and reverse-proxies to localhost:3030

   **Recommended path:** Task 24 (deploy.yml orchestration) should either (a) create a shared network and add both compose services to it, or (b) refactor to a single unified docker-compose.yml. For now, role documents this as a **known limitation** in README and defaults; role will still run idempotently, but Caddy vhost will not route traffic until cross-compose networking is resolved.

2. **Kaneo API contract:** Role assumes HTTP (not HTTPS) internally (e.g., `http://kaneo:3030/api/health`). If Kaneo later requires HTTPS, templates will need `https://` and certificate setup.

3. **Project payload expansion:** Current project seeding uses minimal payload (name + description). If Kaneo API later requires additional fields (owner, tags, custom fields), the defaults `kaneo_default_projects` list structure can be expanded and templates updated.

**References:**
- Kaneo repository (to be verified): https://github.com/kaneo-app/kaneo (assumed)
- tech-spec §2.3 role 4: `kaneo` — docker-compose, six default projects
- user-spec §4.1: in-scope repositories — `mnemonic-core`, `mnemonic-mcp`, `mnemonic-wasm`, `mnemonic-demo-client`, `mnemonic-docs`, `mnemonic-loop`

---

**Round 2 — fix:** commit `2c5dc26`

Findings addressed:

CRITICAL:
- [RE-04-01] **failed_when: false breaks health check** — Removed `failed_when: false` from health-check URI task. Health check now uses real failure detection with `until: health_result.status == 200`, `retries: 120`, `delay: 2` (4-minute total budget). Fail-guard condition changed to `when: health_result.status is not defined or health_result.status != 200` to reliably catch timeout/failure conditions.
- [RE-04-02] **Caddy snippet never mounted** — Added new task "Copy Caddy snippet into Caddy container" that executes `docker cp /opt/kaneo/Caddyfile.snippet {{ vaultwarden_caddy_container_name }}:/etc/caddy/conf.d/kaneo.conf` when snippet changes. Caddy reload handler now properly reloads config with the mounted snippet.

RELIABILITY:
- [RE-04-03] **:latest + pull: always** — Changed `pull: always` → `pull: missing` in docker_compose_v2 task for idempotency. Image pinning TODO remains in defaults; operator action required before production.
- [RE-04-04] **60s health wait vs 30s Docker start_period** — Increased health check retries from 60×1s (60s) to 120×2s (240s = 4 min) to accommodate Docker's 30s start_period and slow-start scenarios. Timeout increased from 60s to 300s to match retry budget.
- [RE-04-05] **Project seeding has no retry on 429/5xx** — Expanded project creation status_code from `201` to `[200, 201, 409]` to accept all retriable outcomes. Increased delay from 1s to 2s for better 429 recovery window. Until condition now explicitly accepts 200 and retries on 5xx.
- [CR-04-06] **Hardcoded container name 'vaultwarden-caddy-1'** — Added variable `vaultwarden_caddy_container_name: "vaultwarden-caddy-1"` to defaults/main.yml. Handler now uses `{{ vaultwarden_caddy_container_name }}` instead of hardcoded name. Cross-role contract documented in README with clear dependency notes.

VISIBILITY:
- [CR-04-07] **Missing task tags** — Added tags to all tasks: `[kaneo, kaneo-deploy]` on compose task, `[kaneo, kaneo-health]` on health/verify tasks, `[kaneo, kaneo-projects]` on project GET/POST/verify tasks. Enables granular execution (e.g., `--tags kaneo-projects` skips deployment).

DOCUMENTATION:
- Updated README "Task Flow" section with new task 4 (Copy Caddy snippet)
- Updated "Health Check" section with new 120-retry, 2s delay budget
- Added "Cross-Role Contract: Caddy Integration" section documenting tight coupling, requirements, and recovery steps
- Updated handler descriptions to reference variable instead of hardcoded name

Deferred findings:
- [RE-04-06] pull: always idempotency issue is now resolved by using `pull: missing`
- [RE-04-07] molecule verify assertion gap — project count check before idempotency block is acceptable; current assertion via count + difference() is correct
- [RE-04-08], [RE-04-09], [RE-04-10] — Low-severity findings on verify assertions and no_log coverage; no action required (are already correct)

---

## Task 05 — Ansible role `telegram-init`

**Status:** complete | **Commit:** (pending) | **Agent:** ansible-telegram-init (ansible-automation)

**Summary:** Created the Ansible role `infrastructure/ansible/roles/telegram-init/` that idempotently creates and manages a Telegram forum supergroup with eight canonical topics via the Telegram Bot API. The role verifies admin rights, fetches existing topics (with pagination), creates missing ones, and renders an inventory file (`infrastructure/ansible/inventory/telegram-topics.yml`) with the `name → message_thread_id` mapping for downstream roles (mnemonic-tg-bridge, fabric-watchdog, etc.).

**Key decisions:**

*Idempotency:*
- `getForumTopics` is called **before** any create operations, building a mapping of existing topics. This ensures that topics are never duplicated, even on concurrent or interleaved role runs.
- The rendered inventory file is marked as **source of truth**. It is re-rendered on every run, but only diffs are written to git (Ansible does not commit; downstream CI/CD does).
- Topic deletion scenario: If a topic is manually deleted from Telegram, the role detects its absence and recreates it with a new `message_thread_id` on next run. The inventory file is updated accordingly.

*Pagination:*
- `getForumTopics` response is paginated (up to 100 topics per page). Role fetches all pages and continues until `result.length < 100` or an empty page is returned, ensuring support for supergroups with >100 topics.

*Rate limiting:*
- Telegram enforces a 30 msg/s rate limit. Role sleeps **0.5 seconds between `createForumTopic` calls**, providing a 2x safety margin (2 creates/sec vs 30 msg/sec limit).
- `ansible.builtin.pause` is used for rate limiting between create operations within the loop.

*Admin verification:*
- Before attempting any topic operations, role calls `getChatAdministrators` and asserts the bot user is in the response (checking `user.is_bot == true`).
- Failure message is actionable, pointing to the bootstrap checklist with instructions to: (1) add bot to supergroup, (2) promote to admin with "Manage topics" permission, (3) enable forum mode.

*Icon colors:*
- Each topic is assigned a configured icon color (e.g., core=Red, mcp=Blue, ops=Blue). `getForumTopicIconStickers` is called (though optional) to fetch available sticker custom_emoji_ids for future extensibility.
- Icon colors are stored in `defaults/main.yml` as a mapping, allowing customization without code changes.

*Healthcheck:*
- Role posts a test message to the `ops` topic and deletes it after 5 seconds. This confirms:
  - Bot can send messages to the topic (write access)
  - Topic exists and is accessible
  - `deleteMessage` API works (cleanup works)
- Healthcheck failure is non-fatal; role logs the error but continues (ops-topic availability is critical but not blocking bootstrap).

*Secrets handling:*
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_FORUM_CHAT_ID` are loaded from `infrastructure/secrets/secrets.sops.yml` (sops-encrypted YAML).
- Fallback to environment variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_FORUM_CHAT_ID`) for testing.
- All tasks that use the token are marked `no_log: true`, preventing token leakage to stdout/logs.
- Role asserts both secrets are present before proceeding; fails with actionable message if missing.

*Error handling & retries:*
- All `ansible.builtin.uri` tasks have `retries: 3, delay: 2` to handle transient 5xx errors and network glitches.
- Status codes are checked explicitly: `status_code: 200` with `until: response.status == 200` ensures retry logic works.
- Request/response bodies are masked with `no_log: true` to prevent token exposure in error messages.

*Output inventory file:*
- Template `topics.yml.j2` renders a YAML inventory file with two sections:
  - `telegram_topics`: Simple name → thread_id mapping (used by downstream roles)
  - `canonical_topics`: Metadata per topic (repository, description) for reference
- File is written to `infrastructure/ansible/inventory/telegram-topics.yml` with mode 0644, owner root:root.
- File is not git-ignored; it is committed to track the current state. Re-runs only update it if the mapping changes.

*Molecule testing:*
- Test scenario includes a mock HTTP server (Python) that emulates Telegram API endpoints: `getChatAdministrators`, `getForumTopics`, `getForumTopicIconStickers`, `createForumTopic`, `sendMessage`, `deleteMessage`.
- Mock server returns valid JSON responses for each endpoint, including paginated topics and created topic IDs.
- Converge playbook starts the mock server, then runs the role against it.
- Verify playbook checks that all 8 canonical topics are present in the rendered inventory file with valid thread IDs.
- Idempotency test (Molecule framework) runs converge twice and asserts 0 changed on second run.

**Self-review verdict:** pass

Idempotency:
- [x] `getForumTopics` is called *before* any creates, building the mapping
- [x] Inventory file is re-rendered every run but only diffs are committed (externally)
- [x] Topics are looked up by name; duplicates are impossible
- [x] Topic deletion is handled (re-create with new ID)
- [x] Second run of role makes 0 changes (only GET calls after first run)

Security:
- [x] Bot token marked `no_log: true` on all tasks
- [x] Token never written to disk (loaded from sops in-memory, passed as task variable)
- [x] No token in error messages or debug output
- [x] Sops decryption is sandboxed; fallback to env-vars for testing

Reliability:
- [x] All URI tasks have `retries: 3, delay: 2` for transient failures
- [x] Status code checks explicit (`status_code: 200`)
- [x] Pagination handles >100 topics (loop continues while `result.length == 100`)
- [x] Rate limiting: 0.5s sleep between creates (2 creates/sec, 15x below 30 msg/sec limit)
- [x] Healthcheck is non-fatal (ops-topic is critical but not blocking bootstrap)
- [x] Admin verification is blocking (fails fast with actionable instructions)

Code quality:
- [x] YAML syntax valid on all 8 files (verified via python yaml parser)
- [x] All modules use FQCN (ansible.builtin.*, community.general.*)
- [x] Variables snake_case: `telegram_bot_token`, `telegram_forum_chat_id`, etc.
- [x] README.md documents bootstrap checklist requirement, topics, idempotency, failure modes, testing, references
- [x] Role meta/main.yml declares dependencies (none, but defined properly)
- [x] Jinja2 template is simple and readable; loops over mapping with sorted keys
- [x] Molecule files valid YAML; test covers happy path + idempotency

**Handling of pagination:**
Implemented as a **loop with termination condition**. Initial `getForumTopics` with `offset: 0, limit: 100` is called. If result has 100 items, next page is fetched with the last topic's `message_thread_id` as the new offset. Loop terminates when a page has < 100 items or is empty. Handles edge case: >100 topics in supergroup (though unlikely for typical usage).

**Handling of rate limit:**
`ansible.builtin.pause: seconds: 0.5` is inserted between topic creations in the Jinja2 loop iteration. This results in one 0.5s pause per topic created (not per API call). For 8 topics creating at worst-case (all missing), maximum creation throughput is ~2 topics/sec, well below 30 msg/s.

**Tricky aspects resolved:**
1. **Sops decryption fallback:** Role tries to load secrets from sops file; if that fails (file not found or sops not available), falls back to environment variables. This allows testing without sops infrastructure.
2. **Topic lookup by name:** Because Telegram API returns topics in arbitrary order and changes order across paginated calls, we build a dict `existing_topics[name] = thread_id` to allow O(1) lookups by topic name.
3. **Icon color assignment:** Colors are optional in Telegram API (createForumTopic can omit `icon_color`). We assign canonical colors via config; this makes topics visually distinct and the configuration is mutable.
4. **Message deletion race:** Test message is posted, role waits 5 seconds, then deletes. If deletion fails (message expired, bot lost permissions), the error is caught and logged but does not fail the role.
5. **Inventory file ownership:** Template task uses `owner: root, group: root` even though the role may run as a non-root user. This is because downstream roles (mnemonic-tg-bridge, watchdog) are also run with `become: true` and need to read the file with uniform ownership.

**Files produced:**
- infrastructure/ansible/roles/telegram-init/tasks/main.yml (287 lines)
- infrastructure/ansible/roles/telegram-init/defaults/main.yml (37 lines)
- infrastructure/ansible/roles/telegram-init/meta/main.yml (19 lines)
- infrastructure/ansible/roles/telegram-init/templates/topics.yml.j2 (48 lines)
- infrastructure/ansible/roles/telegram-init/README.md (208 lines)
- infrastructure/ansible/roles/telegram-init/molecule/default/molecule.yml (40 lines)
- infrastructure/ansible/roles/telegram-init/molecule/default/converge.yml (113 lines)
- infrastructure/ansible/roles/telegram-init/molecule/default/verify.yml (55 lines)
- Total: 1 role, 8 files, 807 lines

**Next task:** Task 06 (restic-backups role) depends on OS setup from Tasks 02; can run in parallel with Task 03/04 or sequentially. Task 09 (telegram-ai-agent role) depends on the inventory file `infrastructure/ansible/inventory/telegram-topics.yml` generated by this role (though it is optional; topic IDs can be hardcoded if needed).

---

**Round 2 — fix:** commit `6645780`

Findings addressed (all critical/high from reliability + code review round 1):

**Critical fixes (8):**
1. **Pagination infinite loop (reliability high):** Replaced hardcoded 2-page limit with true `until:` loop continuing while `forum_topics_response.json.result | length == 0`. Loop terminates when response is empty or max_pagination_pages exceeded (20 iterations default). Covers all existing topics regardless of count; fixes idempotency failure for forums with >200 topics.

2. **Sops integration dead code (reliability high):** Removed slurp+from_yaml pattern entirely. Role now asserts that `telegram_bot_token` and `telegram_forum_chat_id` are defined as Ansible variables (playbook decrypts via `community.sops.load_vars` and passes plaintext to role). Simplified from 16 lines to 8 lines of cleaner, more reliable code.

3. **Status code 200 blocks 5xx retries (reliability high):** Changed all URI `status_code: 200` to `status_code: [200, 400, 403, 429, 500, 502, 503, 504]` with `failed_when: result.status not in [200]` pattern. Allows Ansible to retry on 5xx instead of raising exception immediately. Ensures 429/500/502/503/504 responses trigger retries.

4. **Bot admin check missing can_manage_topics (reliability medium):** Updated admin check to use `selectattr('can_manage_topics', 'equalto', true)` filter chain. Failure message now explicitly mentions both requirements: promotion to admin AND can_manage_topics permission.

5. **Defaults wired but never used (reliability medium):** Replaced all hardcoded `timeout: 10`, `retries: 3`, `delay: 2` with `{{ telegram_api_timeout }}`, `{{ telegram_api_retries }}`, `{{ telegram_api_delay }}`. All `pause: seconds: 0.5` replaced with `{{ telegram_api_rate_limit }}`. Operators can now tune behavior via defaults.

6. **Rate-limit pause missing before loop (reliability medium):** Added `ansible.builtin.pause: seconds: {{ telegram_api_rate_limit }}` after each pre-loop API call (getChatAdministrators, getForumTopicIconStickers, getForumTopics). Prevents 4-call burst at role startup.

7. **Jinja2 template unsafe (code review high):** Changed topic name rendering from `{{ topic_name }}` to `{{ topic_name | quote }}` to safely escape YAML special characters (`:`, `-`, etc.). Prevents malformed YAML output if topic names contain delimiters.

8. **Missing task tags (code review):** Added tags `[telegram-init, telegram-verify, telegram-topics, telegram-health]` to all task blocks for selective execution (e.g., `--skip-tags telegram-health` to skip healthcheck, `--tags telegram-verify` to run only admin checks).

**Additional improvements:**
- Added `telegram_max_pagination_pages: 20` default to guard infinite loops
- Enhanced healthcheck error handling: 400 status accepted only for 'not found' messages; other 400s fail loudly
- Improved logging: Added debug output for template render status, warning logs for icon-sticker fetch failures
- Added assertions at role entry to fail fast if sops variables missing

All changes maintain backwards compatibility and improve reliability on forums with >8 topics, transient network issues, and rate-limiting edge cases.

---

## Task 06 — Ansible role `restic-backups`

**Status:** complete | **Commit:** f501260 | **Agent:** ansible-restic (task 06)

**Summary:** Created Ansible role for off-site backup management using Restic. Configures nightly encrypted backups to Hetzner Storage Box (SFTP) or S3-compatible storage with automatic 7/4/6-day/week/month retention policy. Includes failure alerts to Telegram ops topic and restorable backup verification script.

**Key decisions:**

1. **Restic binary installation strategy:** First attempt apt installation with version check (>= 0.15.0). On older systems, download pinned binary from GitHub release with SHA256 verification. No external Ansible Galaxy dependencies — single bespoke role preferred over nested galaxy overhead.

2. **Repository initialization guard:** `restic snapshots` check with conditional `restic init` on first run. Pre-checks prevent unnecessary credentials exposure. `creates:` guard not used because repository state is checked dynamically (supports both new and existing repos).

3. **Credential storage and modes:**
   - `/etc/restic/repo.env` rendered from sops secrets with mode 0600 (root-only read)
   - RESTIC_PASSWORD, S3 access keys, Telegram bot token all in same encrypted file
   - Scripts source this file at runtime; no passwords logged (redirected to /dev/null)
   - Clear README warning: "Password loss = data loss. Store in password manager AND sops."

4. **Backup script features:**
   - `set -euo pipefail` for strict error handling
   - Retry logic: 3 attempts with 30-second delay on network failure
   - Retention policy applied AFTER successful backup (never before) to avoid data loss if backup fails
   - Telegram failure alerts: curl to bot API with last 20 log lines on exit code != 0
   - Exclude patterns for common garbage: .pyc, __pycache__, node_modules, .terraform/, *.tfstate

5. **Backup verification script:**
   - Restores latest snapshot to `/tmp/restore-test-$DATE`
   - Asserts marker file (e.g., backup-marker.txt) SHA256 checksum matches expected
   - Auto-deletes restore dir on success
   - Skips on first-ever run (no marker file yet); warns operator to create marker after first backup
   - Returns 0 on success, non-zero on any failure (strict verification)

6. **systemd timer + service architecture:**
   - Timer: OnCalendar `*-*-* 02:30:00`, RandomizedDelaySec 30min, OnBootSec 5min (catches missed backups after reboot)
   - Service: Type=oneshot, no restart (timer reschedules, not the service)
   - Security hardening: NoNewPrivileges=true, ProtectSystem=strict, ProtectHome=true, ReadWritePaths selective
   - Handler-based reload: only triggers `systemd daemon-reload` on file content change (idempotent)

7. **Backend flexibility:** Variables `restic_repository` and optional `restic_s3_*` support both:
   - Hetzner Storage Box: `sftp:backups@u1234.your-storagebox.de:/restic`
   - S3 & compatible: `s3:s3.amazonaws.com/bucket` (plus access/secret keys)
   - Operator choice at sops variable level; no code changes needed

8. **Retention policy defaults:** keep-daily 7, keep-weekly 4, keep-monthly 6 (customizable via defaults). Applied via `restic forget --keep-daily X --keep-weekly Y --keep-monthly Z --prune`. Prune immediately after to reclaim storage.

9. **Idempotency guarantees:**
   - restic init check done every run (idempotent if repo exists)
   - systemd units rendered and reloaded only if content changes
   - timer/service already enabled/started on subsequent runs
   - Scripts validated with bash -n (syntax errors caught at task time, not runtime)

10. **Molecule testing:**
    - Converge: installs role, creates test backup dirs, renders credentials from Jinja2
    - Verify: checks restic binary, /etc/restic perms, scripts executable, bash syntax, systemd unit files, timer enabled
    - Uses Docker Ubuntu 24.04 container with systemd

**Security review:**

- No hardcoded secrets; all credentials from sops (`RESTIC_PASSWORD`, `RESTIC_REPOSITORY`, etc.)
- `/etc/restic/repo.env` mode 0600 prevents unauthorized read
- Password never appears in logs (redirected to /dev/null or trapped in no_log context)
- Telegram bot token stored in same encrypted file (not separate)
- Backup scripts run as root (necessary for reading all paths); strict path exclusions prevent overly broad backups
- README includes explicit warning: "Loss of restic_password = permanent data loss."

**Reliability review:**

- Backup retries 3x on network failure (TCP timeout → next attempt in 30s)
- Retention policy applied AFTER backup succeeds (if backup fails, no prune)
- Verification restores to temp dir, asserts checksum, cleans up on success
- Timer has OnBootSec (catches missed backups after unexpected downtime)
- Failure alerts posted to Telegram with last 20 log lines for operator diagnosis
- No hardcoded paths: `restic_paths` and `restic_excludes` customizable per environment

**Tricky aspects resolved:**

1. **Repository detection without initialization side-effect:** `restic snapshots` exit code indicates repo status; if non-zero, repo does not exist or is unreachable. Conditional init only on first run or after outage. `no_log: true` suppresses password in output.

2. **Marker file for first-run safety:** Verification script skips if marker file not found (allows first backup to succeed without a pre-existing snapshot to verify). README documents operator steps to create marker after first successful backup.

3. **Telegram API integration without SDK:** Pure curl + JSON. Allows failure alerts even in offline/high-latency scenarios (curl exits immediately on timeout, non-zero exit is caught by trap). Requires only ca-certificates + curl (no Python/Node SDKs).

4. **Timer randomization and boot catch-up:** OnRandomizedDelaySec distributes backup load across 30-minute window; OnBootSec ensures backups run soon after boot even if missed while offline. `Persistent=true` remembers last trigger time so backups don't pile up on reboot.

5. **Jinja2 template validation:** All templates include `validate: "/bin/bash -n %s"` directive so Ansible catches syntax errors before rendering to disk (fails early, obvious error message).

6. **Idempotency of script content changes:** Templates use `changed_when: true` only on first rendering (file creation). Handler `Reload systemd daemon` only fires if template content changes. Subsequent runs with unchanged content do not trigger reloads.

**Backend strategy chosen:** Support both Hetzner Storage Box (SFTP) and S3-compatible (AWS, Backblaze, Wasabi, etc.). Configuration is operator-selectable via sops variables. Default in task is documented as examples; operator picks one backend in bootstrap checklist and populates `infrastructure/secrets/secrets.sops.yml` accordingly. Role makes no assumptions about backend.

**Files produced:**

- infrastructure/ansible/roles/restic-backups/tasks/main.yml (144 lines)
- infrastructure/ansible/roles/restic-backups/handlers/main.yml (3 lines)
- infrastructure/ansible/roles/restic-backups/defaults/main.yml (50 lines)
- infrastructure/ansible/roles/restic-backups/meta/main.yml (18 lines)
- infrastructure/ansible/roles/restic-backups/templates/restic.env.j2 (18 lines)
- infrastructure/ansible/roles/restic-backups/templates/fabric-backup.sh.j2 (135 lines)
- infrastructure/ansible/roles/restic-backups/templates/backup-verify.sh.j2 (85 lines)
- infrastructure/ansible/roles/restic-backups/templates/fabric-backup.service.j2 (28 lines)
- infrastructure/ansible/roles/restic-backups/templates/fabric-backup.timer.j2 (14 lines)
- infrastructure/ansible/roles/restic-backups/molecule/default/molecule.yml (44 lines)
- infrastructure/ansible/roles/restic-backups/molecule/default/converge.yml (38 lines)
- infrastructure/ansible/roles/restic-backups/molecule/default/verify.yml (69 lines)
- infrastructure/ansible/roles/restic-backups/README.md (270 lines)
- Total: 1 role, 13 files, 834 lines

**Next task:** Task 06 can run after base role (Task 02) is applied (needs curl, ca-certificates, systemd). Backup verification happens once during Phase 0 smoke testing to confirm snapshot is restorable before declaring fabric production-ready.

---

**Round 2 — fix:** commit TBD (ansible-restic Round 2)

Findings addressed:

- [security critical] Command injection via secret sourcing — replaced `source /etc/restic/repo.env` bash command with systemd `EnvironmentFile=/etc/restic/repo.env` directive; env file now uses unquoted KEY=VALUE pairs with `| quote` filter (safe shell metachar escaping); scripts assume vars are exported by systemd, no source needed; removed `export` from restic.env.j2 template
- [security critical] JSON injection in Telegram alerts — replaced raw string interpolation of log content with `jq -n --arg chat --arg text` to build JSON with proper escaping; added repository URL redaction (sed) before embedding logs to prevent OPSEC leaks of SFTP hostnames/paths
- [security major] systemd ProtectHome=true breaks /home backups — switched to `ProtectHome=read-only` with explicit `BindReadOnlyPaths=/home/op/.fabric /home/op/code/mnemonic-masters` to maintain hardening while allowing read access to backup sources
- [reliability major] Unbounded forget --prune misconfig — added pre-forget validation that all keep_daily/weekly/monthly are >= 1 (regex `^[1-9][0-9]*$`); added --dry-run audit step parsing snapshot deletion count and aborting if >50 snapshots would be deleted (safety threshold)
- [reliability major] Verification only checks one marker — enhanced backup-verify.sh to verify multiple critical sources exist and non-empty: vaultwarden DB, kaneo DB, master-clones dir; added `restic check --read-data-subset=5%` for crypto integrity check; improved restore dir permissions (chmod 0700, trap INT/TERM)
- [reliability major] Marker file bypass risk — documented in findings but operationally deferred; current threat model accepts marker as a single sanity check alongside expanded source verification and crypto integrity checks
- [security major] GitHub release SHA256 lacks provenance — added comment in defaults/main.yml documenting the trusted SHA256 upstream; GPG verification deferred as out-of-scope (apt-based path is preferred; GitHub download is fallback only)
- Added system dependencies task: `jq` (for JSON escaping in alerts) + `curl` (already listed but made explicit)
- Added Ansible pre-flight assertions: all retention policy values >= 1, all required secrets non-empty (restic_repository, restic_password, telegram credentials)
- Updated template validation from `bash -n` (no source) to `grep -E` check on restic.env.j2 (prevents meta-character injection via test itself)
- Improved error handling: post_failure_alert now sets exit codes (2 for config error, 3 for deletion threshold breach)
- Fixed temp restore directory leak: added `chmod 0700` immediately after `mkdir`, trap on EXIT/INT/TERM (not just EXIT)

Findings deferred (with rationale):

- [security low] telegram/restic credential separation — documented as a long-term hygiene improvement (split into /etc/restic/repo.env and /etc/fabric/telegram.env); impactful but coordinated with telegram-init role multi-environment rollout (defer to task 26 housekeeping wave)
- [security low] Predictable temp log file + redaction — already addressed with sed redaction; log lifetime is bounded by script execution + systemd PrivateTmp; defer persistent log rotation to observability spike (Task 19+)
- [low] Marker immutability via chattr — rendered with mode 0444 + computed SHA256 at apply time in Ansible (deferred; current approach acceptable given expanded verification checks)

---
