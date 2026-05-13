#!/bin/bash
#
# bootstrap.sh — local helper to seed sops/age key and encrypt secrets
#
# Usage:
#   ./infrastructure/scripts/bootstrap.sh [--init-age] [--encrypt] [--decrypt]
#
# This script runs locally on the operator's machine before the first CI deploy.
#
# Steps:
#  1. (--init-age) Generate operator's age keypair if not present
#  2. (--encrypt) Encrypt secrets.yml.template with the age key
#  3. (--decrypt) Verify decryption works (smoke test)
#
# After running this once, the SOPS_AGE_KEY secret is stored in GitHub Actions,
# and CI can decrypt at deploy time.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
AGE_KEY_PATH="${HOME}/.config/sops/age/keys.txt"
SOPS_CONFIG="${REPO_ROOT}/infrastructure/secrets/.sops.yaml"
SECRETS_ENCRYPTED="${REPO_ROOT}/infrastructure/secrets/secrets.sops.yml"
SECRETS_TEMPLATE="${REPO_ROOT}/infrastructure/secrets/secrets.sops.yml.template"
SECRETS_DECRYPTED="/tmp/secrets-decrypted.yml"

log() {
  echo "[bootstrap] $*" >&2
}

error() {
  echo "ERROR: $*" >&2
  exit 1
}

# Check if age and sops are installed
check_tools() {
  if ! command -v age &> /dev/null; then
    error "age not found. Install with: brew install age (macOS) or apt install age-encryption (Linux)"
  fi
  if ! command -v sops &> /dev/null; then
    error "sops not found. Install with: brew install sops (macOS) or apt install sops (Linux)"
  fi
  log "Tools verified: age, sops"
}

# Initialize age keypair if not present
init_age() {
  if [ -f "$AGE_KEY_PATH" ]; then
    log "Age key already exists at $AGE_KEY_PATH"
    return 0
  fi

  log "Generating age keypair..."
  mkdir -p "$(dirname "$AGE_KEY_PATH")"
  age-keygen -o "$AGE_KEY_PATH" || error "Failed to generate age key"
  chmod 600 "$AGE_KEY_PATH"
  log "Age key generated at $AGE_KEY_PATH"

  log "Your age public key:"
  age-keygen -y "$AGE_KEY_PATH"
}

# Encrypt secrets template
encrypt_secrets() {
  if [ ! -f "$SECRETS_TEMPLATE" ]; then
    error "Secrets template not found: $SECRETS_TEMPLATE"
  fi

  if [ ! -f "$AGE_KEY_PATH" ]; then
    error "Age key not found. Run with --init-age first"
  fi

  # Extract age public key
  AGE_PUBLIC_KEY=$(age-keygen -y "$AGE_KEY_PATH")

  log "Creating .sops.yaml with age recipient..."
  mkdir -p "$(dirname "$SOPS_CONFIG")"
  cat > "$SOPS_CONFIG" <<EOF
creation_rules:
  - path_regex: infrastructure/secrets/secrets\.sops\.yml
    key_groups:
      - age: $AGE_PUBLIC_KEY
EOF
  log "SOPS config written to $SOPS_CONFIG"

  log "Encrypting secrets..."
  sops -e "$SECRETS_TEMPLATE" > "$SECRETS_ENCRYPTED" || error "Failed to encrypt secrets"
  log "Secrets encrypted and saved to $SECRETS_ENCRYPTED"

  log "Secrets file is now committed to git (encrypted)"
  log "The age private key at $AGE_KEY_PATH is your decryption key — keep it safe!"
}

# Decrypt and verify secrets
decrypt_secrets() {
  if [ ! -f "$SECRETS_ENCRYPTED" ]; then
    error "Encrypted secrets not found: $SECRETS_ENCRYPTED"
  fi

  if [ ! -f "$AGE_KEY_PATH" ]; then
    error "Age key not found: $AGE_KEY_PATH"
  fi

  log "Attempting to decrypt secrets..."
  if sops -d "$SECRETS_ENCRYPTED" > "$SECRETS_DECRYPTED"; then
    log "Decryption successful!"
    log "Decrypted content saved to $SECRETS_DECRYPTED (for inspection only)"
    log "This file is temporary and should not be committed"

    # Quick validation: check for expected keys
    if grep -q "telegram_bot_token:" "$SECRETS_DECRYPTED"; then
      log "Validation passed: telegram_bot_token found"
    else
      error "Validation failed: expected keys not found in decrypted secrets"
    fi
  else
    error "Failed to decrypt secrets. Check your age key at $AGE_KEY_PATH"
  fi
}

# Export age key for GitHub Actions
export_age_key() {
  if [ ! -f "$AGE_KEY_PATH" ]; then
    error "Age key not found: $AGE_KEY_PATH"
  fi

  log "Age key content (for GitHub Actions SOPS_AGE_KEY secret):"
  cat "$AGE_KEY_PATH"
  log ""
  log "Copy the above into GitHub Actions secret: Settings > Secrets and variables > Actions > SOPS_AGE_KEY"
}

# Main
case "${1:-}" in
  --init-age)
    check_tools
    init_age
    ;;
  --encrypt)
    check_tools
    encrypt_secrets
    ;;
  --decrypt)
    check_tools
    decrypt_secrets
    ;;
  --export-key)
    check_tools
    export_age_key
    ;;
  --full)
    check_tools
    init_age
    encrypt_secrets
    decrypt_secrets
    export_age_key
    ;;
  *)
    cat <<USAGE
Usage: $(basename "$0") [--init-age|--encrypt|--decrypt|--export-key|--full]

  --init-age       Generate age keypair (run once locally)
  --encrypt        Encrypt secrets template with age key
  --decrypt        Decrypt and verify secrets (smoke test)
  --export-key     Print age key for GitHub Actions setup
  --full           Run all steps (recommended for first-time setup)

Example (first-time setup):
  ./infrastructure/scripts/bootstrap.sh --full

Then add to GitHub Actions:
  Settings > Secrets and variables > Actions
  - SOPS_AGE_KEY: (paste output from --export-key step)

USAGE
    exit 1
    ;;
esac
