#!/usr/bin/env bash
# tests/test_tofu_plan.sh
#
# Validates the OpenTofu module without touching real Hetzner infrastructure.
#
# What this script checks (4 steps):
#   1. tofu init    — downloads provider plugins; verifies .terraform.lock.hcl
#   2. tofu validate — static HCL/schema validation; no credentials needed
#   3. tofu fmt -check — formatting lint (advisory; treat as error in CI)
#   4. tofu plan -detailed-exitcode — generates execution plan with dummy creds
#                     Exit code 2 = changes pending (expected on clean state)
#                     Exit code 0 = no changes (expected after apply)
#                     Exit code 1 = error (fail)
#
# Post-apply smoke checks (requires a real Hetzner token and an applied state):
#   These are documented here but NOT executed — they require external services.
#   Run them manually after `tofu apply` during bootstrap (see bootstrap-checklist.md).
#
#   nmap -sU -p 41641 <vm_ipv4>
#     Expected output: 41641/udp open|filtered (Tailscale port)
#
#   nmap -sT -p 22,80,443 <vm_ipv4>
#     Expected output: all ports filtered/closed (no public inbound)
#
#   ssh root@<vm_ipv4> -o ConnectTimeout=5
#     Expected result: connection refused or timeout (no public SSH)
#
# Usage:
#   # Validate only (no real credentials needed):
#   bash tests/test_tofu_plan.sh
#
#   # Plan against a real state (requires HCLOUD_TOKEN in env):
#   HCLOUD_TOKEN=your_real_token \
#   TF_VAR_operator_ssh_pubkey="$(cat ~/.ssh/id_ed25519.pub)" \
#   bash tests/test_tofu_plan.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Write the plan artifact to a temp file so the module source directory stays
# clean across CI runs, and to avoid leaving sensitive user_data values in a
# workspace-level last.tfplan that could persist between runs.
PLAN_FILE="$(mktemp)"
trap 'rm -f "${PLAN_FILE}"' EXIT

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

# ── Prerequisite check ────────────────────────────────────────────────────────
if ! command -v tofu &>/dev/null; then
  error "OpenTofu CLI (tofu) not found on PATH."
  error "Install from https://opentofu.org/docs/intro/install/ and re-run."
  error "Validation deferred — no local tofu binary available on this machine."
  exit 1
fi

TOFU_VERSION="$(tofu version -json 2>/dev/null | grep -o '"terraform_version":"[^"]*"' | cut -d'"' -f4 || tofu version | head -1)"
info "OpenTofu version: ${TOFU_VERSION}"

# ── Dummy credentials for validate/plan (not real; plan will fail API calls) ─
# tofu validate does not need a real token.
# tofu plan needs the variable set to proceed past validation but will fail
# the Hetzner API call — that is expected and acceptable for local CI.
export HCLOUD_TOKEN="${HCLOUD_TOKEN:-dummy-token-for-local-validate}"
export TF_VAR_hcloud_token="${HCLOUD_TOKEN}"
export TF_VAR_operator_ssh_pubkey="${TF_VAR_operator_ssh_pubkey:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderKeyForLocalValidationOnly test@localhost}"

# ── Step 1: tofu init ─────────────────────────────────────────────────────────
info "Step 1/4: tofu init (downloads provider plugins)"
if ! tofu -chdir="${MODULE_DIR}" init -input=false; then
  error "tofu init failed. Check network connectivity and provider version constraints."
  exit 1
fi
info "tofu init: OK"

# ── Step 2: tofu validate ─────────────────────────────────────────────────────
info "Step 2/4: tofu validate (static schema validation)"
if ! tofu -chdir="${MODULE_DIR}" validate; then
  error "tofu validate failed. Fix HCL errors above and re-run."
  exit 1
fi
info "tofu validate: OK"

# ── Step 3: tofu fmt check ────────────────────────────────────────────────────
info "Step 3/4: tofu fmt -check (formatting lint)"
if ! tofu -chdir="${MODULE_DIR}" fmt -check -recursive; then
  warn "tofu fmt -check found formatting issues."
  warn "Run: tofu fmt -recursive ${MODULE_DIR}"
  warn "Continuing (fmt is advisory in this script; treat as error in CI)."
fi

# ── Step 4: tofu plan ─────────────────────────────────────────────────────────
info "Step 4/4: tofu plan -detailed-exitcode"
info "  NOTE: plan will attempt to contact Hetzner API."
info "  With a dummy token, the plan phase itself may error at provider init."
info "  This is expected for local validate runs. Use a real HCLOUD_TOKEN for full plan."

set +e
tofu -chdir="${MODULE_DIR}" plan \
  -detailed-exitcode \
  -input=false \
  -var "hcloud_token=${HCLOUD_TOKEN}" \
  -var "operator_ssh_pubkey=${TF_VAR_operator_ssh_pubkey}" \
  -out="${PLAN_FILE}" \
  2>&1
PLAN_EXIT=$?
set -e

case "${PLAN_EXIT}" in
  0)
    info "tofu plan exit 0: no changes pending (state is up to date)."
    ;;
  2)
    info "tofu plan exit 2: changes pending — expected on a clean/new state."
    info "Expected resources in plan:"
    info "  + hcloud_firewall.fabric"
    info "  + hcloud_firewall_attachment.fabric"
    info "  + hcloud_server.fabric"
    info "  + hcloud_volume.fabric_data"
    info "  + hcloud_volume_attachment.fabric_data"
    info "Total: 5 resources"
    ;;
  1)
    if [[ "${HCLOUD_TOKEN}" == "dummy-token-for-local-validate" ]]; then
      warn "tofu plan exit 1 with dummy token — API authentication failure is expected."
      warn "This run verified: init + validate passed. Plan requires a real HCLOUD_TOKEN."
      warn "Deferred smoke: run with HCLOUD_TOKEN set for full plan verification."
      exit 0
    else
      error "tofu plan exit 1: plan failed with a real token. See output above."
      exit 1
    fi
    ;;
  *)
    error "tofu plan unexpected exit code: ${PLAN_EXIT}"
    exit 1
    ;;
esac

info "All checks passed."
