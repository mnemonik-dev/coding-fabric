# Ansible Role: base

OS hardening, firewall configuration, operator user creation, and base directory structure for the Mnemonic fabric.

## Purpose

This role prepares a freshly-provisioned Ubuntu 24.04 VM for use as the Mnemonic fabric infrastructure by:

- Setting hostname from variable `fabric_hostname`
- Configuring ufw firewall with deny-by-default policy and Tailscale interface allow-list
- Installing and enabling unattended-upgrades for automatic security updates
- Creating the operator user with sudo access and base directory structure
- Hardening SSH configuration: disabling root login and password authentication
- Creating required fabric directories: `/home/op/code/` and `/home/op/.fabric/{logs/sanitized}`

## Variables

### Required

None. All variables have defaults.

### Defaults (defaults/main.yml)

- `fabric_hostname` (string, default: `mnemonic-fabric`): Hostname to set on the VM
- `base_operator_user` (string, default: `op`): Username for the operator user

## Security Notes

- **NOPASSWD sudo:** The operator user is configured with `NOPASSWD: ALL` as a bootstrap convenience. This should be hardened in production to explicit commands as Ansible-managed tasks are identified.
- **SSH hardening:** Root login and password authentication are disabled. Access is restricted to public-key authentication over Tailscale VPN.
- **Firewall:** Default deny-inbound policy with explicit allow for Tailscale interface traffic. Established/related connections are permitted.

## Dependencies

- `community.general` (>=9.0.0)
- `ansible.posix` (>=1.5.0)

## Example Playbook

```yaml
---
- name: Configure VM base
  hosts: fabric_vms
  roles:
    - role: base
      vars:
        fabric_hostname: mnemonic-fabric-prod
        base_operator_user: op
```

## Handlers

- `reload sshd`: Reloads SSH daemon configuration
- `reload ufw`: Reloads firewall rules
- `restart unattended-upgrades`: Restarts automatic update service

## Idempotency

This role is fully idempotent. Running it multiple times produces no changes on the second and subsequent runs (verified by Molecule test suite).

## Testing

Run molecule tests with:

```bash
molecule test -s base
```

This will:
1. Create an ephemeral Docker container with Ubuntu 24.04
2. Apply the role
3. Verify state (hostname, user, directories, firewall, SSH config)
4. Run the role again to assert idempotency (0 changed)
5. Clean up the container
