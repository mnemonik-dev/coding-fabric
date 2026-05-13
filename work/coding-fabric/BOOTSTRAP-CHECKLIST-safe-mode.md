# Safe-mode Rollback Bootstrap Checklist

Complete these steps before deploying fabric to production.

## A. Git & Signing Setup

- [ ] **Install gnupg or openssh-client** (for tag signing)
  ```bash
  # macOS: brew install gnupg
  # Ubuntu: sudo apt-get install gnupg
  ```

- [ ] **Configure git signing key** (local deployment host)
  ```bash
  git config --global user.signingkey <KEY_ID>
  # For SSH: git config --global gpg.format ssh
  ```

- [ ] **Test tag signing** in a test repo
  ```bash
  git tag -s -m "test" test-tag && git verify-tag test-tag && rm -f .git/refs/tags/test-tag
  ```

## B. Secrets Management (sops + age)

- [ ] **Generate or locate age key**
  ```bash
  age-keygen -o ~/.config/sops/age/keys.txt
  age-keygen -y ~/.config/sops/age/keys.txt  # Display public key
  ```

- [ ] **Create `.sops.yaml` in repo root**
  ```yaml
  creation_rules:
    - path_regex: infrastructure/ansible/inventory/group_vars/all/secrets\.sops\.yml
      key_groups:
        - age: age1YOUR_AGE_KEY_HERE
  ```

- [ ] **Encrypt secrets file**
  ```bash
  sops -e < /path/to/unencrypted/secrets.yml > infrastructure/ansible/inventory/group_vars/all/secrets.sops.yml
  ```

- [ ] **Test sops decryption**
  ```bash
  sops -d infrastructure/ansible/inventory/group_vars/all/secrets.sops.yml | grep telegram_bot_token
  ```

## C. Ansible & Collections

- [ ] **Install Ansible** (>= 2.12)
  ```bash
  pip install ansible>=2.12
  ```

- [ ] **Install required collections**
  ```bash
  ansible-galaxy install -r infrastructure/ansible/requirements.yml
  ```

- [ ] **Validate playbook syntax**
  ```bash
  ansible-lint infrastructure/ansible/playbooks/safe-mode-rollback.yml
  ```

## D. GitHub Branch Protection

- [ ] **Ensure gh CLI is authenticated**
  ```bash
  gh auth login
  ```

- [ ] **Run branch-protection script**
  ```bash
  bash infrastructure/scripts/configure-branch-protection.sh
  ```

- [ ] **Verify via GitHub UI**
  - Go to repo Settings > Branches > mnemonic-loop
  - Confirm "smoke-gate / E2E smoke test" is in required checks
  - Confirm "Require branches to be up to date before merging" is enabled

## E. Telegram Integration

- [ ] **Create Telegram bot** (via @BotFather)
  - Save bot token to sops secrets

- [ ] **Create Telegram ops channel**
  - Add bot to the channel
  - Get channel ID (e.g., -1001234567890)
  - Save to sops secrets as `telegram_ops_topic`

- [ ] **Test Telegram notification** (after sops is set up)
  ```bash
  bash infrastructure/scripts/test-telegram.sh
  ```

## F. Dry-run & Validation

- [ ] **Ansible syntax check (check mode)**
  ```bash
  ansible-playbook -i infrastructure/ansible/inventory/hosts.yml -C \
    infrastructure/ansible/playbooks/safe-mode-rollback.yml
  ```

- [ ] **Full dry-run on staging**
  - Trigger a manual rollback on a staging host
  - Verify logs and Telegram alert

- [ ] **Verify last-known-good tag workflow**
  - Merge a PR to mnemonic-loop
  - Check that `last-known-good` tag was created: `git describe --tags`
  - Verify tag is signed: `git verify-tag last-known-good`

## G. Documentation

- [ ] **Update team runbook** with rollback procedures
- [ ] **Distribute Telegram channel** access to incident response team
- [ ] **Archive this checklist** (mark completion date)

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `community.sops` not found | Run `ansible-galaxy install -r infrastructure/ansible/requirements.yml` |
| `git verify-tag` fails | Ensure signing key is configured and the tag was signed with `-s` flag |
| sops decrypt error | Verify `~/.config/sops/age/keys.txt` exists and contains your age key |
| Telegram message not received | Check bot token and channel ID in encrypted secrets; test with `test-telegram.sh` |
| Lock file stuck | SSH to target and run `rm -f /var/lock/fabric-safe-mode` |

---

**Completion Date**: ____________  
**Completed By**: ____________  
**Verified By**: ____________
