# Ansible Role: tailscale

Installs tailscaled, authenticates to the Tailscale tailnet, and registers the tailnet IP for use by subsequent roles.

## Purpose

This role joins a freshly-provisioned Ubuntu 24.04 VM to a Tailscale tailnet by:

- Adding Tailscale's official GPG key and APT repository
- Installing the `tailscale` package (which includes `tailscaled`)
- Starting and enabling the `tailscaled` systemd service
- Running `tailscale up` with authentication key, hostname, and SSH support
- Querying the assigned IPv4 address and registering it as `tailscale_ip` for use by later roles (e.g., vaultwarden, workspace-manager)

## Variables

### Required

- `tailscale_authkey` (string, no default): Tailscale authentication key. Must be provided via sops-encrypted secrets file or playbook variable. The role asserts its presence before proceeding.

### Defaults (defaults/main.yml)

- `fabric_hostname` (string, default: `mnemonic-fabric`): Hostname to register in Tailscale. Coordinated with the `base` role.

## Registered Facts

- `tailscale_ip` (string): The IPv4 address assigned by Tailscale, captured from `tailscale ip -4`. Available to subsequent roles via `{{ hostvars[inventory_hostname].tailscale_ip }}`.

## Security Notes

- **Auth-key handling:** The auth-key is sourced from sops-encrypted secrets and never stored on disk after authentication. It is a single-use key with short TTL.
- **Tailscale SSH:** The role enables Tailscale SSH with `--ssh` flag, allowing secure SSH access over the tailnet without exposing SSH ports to the public internet.
- **Route acceptance:** The role accepts subnets with `--accept-routes`, allowing the fabric to receive advertised routes from other tailnet devices.

## Dependencies

- `community.general` (>=9.0.0)
- `ansible.posix` (>=1.5.0)

## Example Playbook

```yaml
---
- name: Configure Tailscale
  hosts: fabric_vms
  vars:
    tailscale_authkey: "{{ vault_tailscale_authkey }}"  # From sops
    fabric_hostname: mnemonic-fabric-prod
  roles:
    - role: tailscale
```

## Handlers

- `restart tailscaled`: Restarts the tailscaled systemd service

## Idempotency

This role is designed to be idempotent with regard to package installation and service management. However, `tailscale up` re-runs on each invocation to ensure the device is connected. The `changed_when` condition distinguishes between first-time connection and re-runs.

**Note:** If the auth-key expires between runs, the role will fail. A replacement key must be provided.

## Testing

Run molecule tests with:

```bash
molecule test -s tailscale
```

**Note:** Molecule tests run in an isolated Docker container without actual Tailscale network access. The test verifies:
1. Package installation and repository configuration
2. Service enablement
3. Command availability
4. Second run idempotency (0 changed)

Real Tailscale authentication requires a valid auth-key and network connectivity to Tailscale's infrastructure.

## Post-deployment Verification

After role execution on a real VM:

```bash
# Check online status
tailscale status

# Verify hostname registration
tailscale status | grep 'Host name:'

# Confirm IPv4 address
tailscale ip -4

# Test SSH over tailnet from another tailnet device
tailscale ssh op@mnemonic-fabric
```
