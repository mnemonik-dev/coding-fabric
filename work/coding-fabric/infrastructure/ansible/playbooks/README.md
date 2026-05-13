# Safe-mode Rollback Playbook

**Purpose**: Incident recovery for fabric services. Reverts mnemonic-loop to `last-known-good` tag and restarts services.

**Scope**: Fabric services only (`fabric-services` Ansible tag). Does NOT touch base, tofu, vaultwarden, or kaneo infrastructure.

## Prerequisites

### 1. Collections Installation
Install required Ansible collections:
```bash
ansible-galaxy install -r infrastructure/ansible/requirements.yml
```

### 2. Secrets Configuration (sops + age)
The playbook loads Telegram credentials from an encrypted sops file.

#### Step 1: Generate or locate your age key
```bash
# First time only: generate a new age key
age-keygen -o ~/.config/sops/age/keys.txt

# Display your public key (for .sops.yaml)
age-keygen -y ~/.config/sops/age/keys.txt
```

#### Step 2: Create `.sops.yaml` in repository root
```yaml
creation_rules:
  - path_regex: infrastructure/ansible/inventory/group_vars/all/secrets\.sops\.yml
    key_groups:
      - age: age1YOUR_PUBLIC_KEY_HERE  # Replace with output from age-keygen -y
```

#### Step 3: Encrypt the secrets file
```bash
# Example .sops.yml (unencrypted)
cat > /tmp/secrets-example.yml <<EOF
telegram_bot_token: "123456789:ABCdefGHIjklmnoPQRstuvWXYZ-1a2b3c4d5e"
telegram_ops_topic: "-1001234567890"
EOF

# Encrypt and save
sops -e /tmp/secrets-example.yml > infrastructure/ansible/inventory/group_vars/all/secrets.sops.yml
```

#### Step 4: Verify decryption (sops will use your age key from ~/.config/sops/age/keys.txt)
```bash
sops -d infrastructure/ansible/inventory/group_vars/all/secrets.sops.yml
```

### 3. Git Configuration
Ensure the deployment host has GPG/SSH signing key configured:
```bash
git config --global user.signingkey <KEY_ID>
# For SSH signing (recommended for CI):
git config --global gpg.format ssh
```

## Usage

### Deploy playbook
```bash
# Ad-hoc rollback
ansible-playbook -i infrastructure/ansible/inventory/hosts.yml \
  infrastructure/ansible/playbooks/safe-mode-rollback.yml

# With verbose logging
ansible-playbook -i infrastructure/ansible/inventory/hosts.yml -vv \
  infrastructure/ansible/playbooks/safe-mode-rollback.yml
```

### Dry-run (check mode)
```bash
ansible-playbook -i infrastructure/ansible/inventory/hosts.yml -C \
  infrastructure/ansible/playbooks/safe-mode-rollback.yml
```

## What the Playbook Does

1. **Acquire lock**: Blocks concurrent rollbacks (prevents partial state)
2. **Stop services**: Gracefully stops fabric services
3. **Verify tag**: Ensures `last-known-good` tag is signed and valid
4. **Checkout**: Reverts mnemonic-loop to the tagged commit
5. **Deploy**: Re-runs `ansible-playbook deploy.yml --tags fabric-services`
6. **Start services**: Restarts fabric services
7. **Alert ops**: Posts completion status to Telegram (HIGH severity)

## Security Features

- **Signed tags**: `last-known-good` is GPG/SSH-signed; playbook verifies signature before checkout
- **No force-push**: Uses `--force-with-lease` to prevent accidental history rewrite
- **Encrypted secrets**: Telegram token stored in sops, decrypted at runtime, marked `no_log`
- **JSON injection protection**: All user-controlled content (error messages) sanitized before Telegram
- **Lock atomicity**: Prevents concurrent rollbacks via file locking
- **no_log on credentials**: All tasks handling tokens marked `no_log: true`

## Failures and Recovery

### Lock timeout
If a previous rollback is stuck, remove the lock file:
```bash
ssh <target_host> rm -f /var/lock/fabric-safe-mode
```

### Tag verification fails
If `git verify-tag` fails:
1. Check the signer's GPG key is in the allow-list
2. Verify the tag was created with signing enabled: `git verify-tag -v last-known-good`
3. Manually audit the commit: `git log -1 last-known-good --oneline`

### Telegram alerts not received
- Verify `telegram_bot_token` and `telegram_ops_topic` are correctly encrypted in `secrets.sops.yml`
- Check that sops can decrypt: `sops -d infrastructure/ansible/inventory/group_vars/all/secrets.sops.yml`
- Verify Telegram bot has permission to send to the ops topic

## Testing

Run ansible-lint to validate syntax:
```bash
ansible-lint infrastructure/ansible/playbooks/safe-mode-rollback.yml
```

(Requires community.sops collection installed; see Prerequisites)

## Scope Clarification: Fabric-only Rollback

This playbook is **fabric-critical** only. It does not touch:
- Base infrastructure (Kubernetes, networking)
- ToFu state (Terraform)
- Vaultwarden (secrets vault)
- Kaneo (observability)

The reason: fabric is the deployable unit for the Mnemonic loop; other services are preconditions that do not change during loop iterations. A malicious/broken loop task affects only fabric services, not the entire cluster.

If broader rollback is needed (e.g., base infrastructure), invoke the full `deploy.yml` without the `--tags fabric-services` restriction.
