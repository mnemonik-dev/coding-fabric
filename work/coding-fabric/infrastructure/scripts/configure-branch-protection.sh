#!/bin/bash
#
# configure-branch-protection.sh
# Idempotent script to enforce smoke-gate as a required status check on mnemonic-loop.
# Requires: gh CLI with repo write access, GITHUB_TOKEN env var
#
# Usage: bash infrastructure/scripts/configure-branch-protection.sh
#        GITHUB_REPO=owner/repo bash infrastructure/scripts/configure-branch-protection.sh
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
  echo -e "${GREEN}[INFO]${NC} $*" >&2
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*" >&2
}

REPO="${GITHUB_REPO:-.}"
BRANCH="mnemonic-loop"
REQUIRED_CHECKS=("smoke-gate / E2E smoke test (15min budget)")

# Verify gh is installed
if ! command -v gh &>/dev/null; then
  log_error "gh CLI not found; install from https://cli.github.com"
  exit 1
fi

# Verify authentication
if ! gh auth status &>/dev/null; then
  log_error "Not authenticated; run: gh auth login"
  exit 1
fi

# Get repo full name if not provided
if [[ "$REPO" == "." ]]; then
  REPO=$(gh repo view --json nameWithOwner -q .)
fi

log_info "Configuring branch protection for $REPO/$BRANCH"

# Fetch current protection rules
log_info "Fetching current protection rules..."
CURRENT=$(gh api repos/$REPO/branches/$BRANCH/protection --jq . 2>/dev/null || echo "{}")

# Extract existing required checks
EXISTING_CHECKS=$(echo "$CURRENT" | jq -r '.required_status_checks.checks[].context' 2>/dev/null | sort || echo "")

# Check if smoke-gate is already required
if echo "$EXISTING_CHECKS" | grep -q "smoke-gate"; then
  log_info "smoke-gate already required; no changes needed"
  exit 0
fi

# Build new required checks list (add smoke-gate if not present)
NEW_CHECKS=()
if [[ -n "$EXISTING_CHECKS" ]]; then
  while IFS= read -r check; do
    NEW_CHECKS+=("$check")
  done <<< "$EXISTING_CHECKS"
fi
NEW_CHECKS+=("${REQUIRED_CHECKS[@]}")

log_info "Adding smoke-gate to required checks..."

# Use gh api to update protection
PAYLOAD=$(jq -n \
  --argjson checks "$(jq -R -s -c 'split("\n") | map(select(length > 0)) | map({context: .})' <<< "$(printf '%s\n' "${NEW_CHECKS[@]}")" )" \
  '{
    required_status_checks: {
      strict: true,
      contexts: $checks
    },
    enforce_admins: true,
    required_pull_request_reviews: {
      dismiss_stale_reviews: true,
      require_code_owner_reviews: true,
      required_approving_review_count: 1
    },
    restrictions: null
  }')

if gh api repos/$REPO/branches/$BRANCH/protection \
  -X PUT \
  --input <(echo "$PAYLOAD") &>/dev/null; then
  log_info "Successfully configured branch protection for $REPO/$BRANCH"
  log_info "Required checks: ${NEW_CHECKS[@]}"
  exit 0
else
  log_error "Failed to update branch protection"
  exit 1
fi
