# restic-backups — Ansible role

Install Restic backup tool and configure nightly off-site backups of mnemonic-loop coding-fabric data with automatic retention policy enforcement.

## Overview

This role:
1. Installs Restic binary (from apt if version >= 0.15.0, else from GitHub release)
2. Renders `/etc/restic/repo.env` with credentials from sops-encrypted secrets
3. Creates `/usr/local/bin/fabric-backup.sh` — main backup script with retry logic and Telegram failure alerts
4. Creates `/usr/local/bin/backup-verify.sh` — test restore and checksum verification
5. Registers systemd timer `fabric-backup.timer` for nightly execution (02:30 UTC + 30min random jitter)
6. Enables automatic retention policy (keep-daily 7, keep-weekly 4, keep-monthly 6) after each backup

## Critical Security Notes

### Password Loss = Data Loss

**Your Restic password is the only key to unlock the encrypted repository.**

- Store the password in your operator password manager (Bitwarden, 1Password, etc.)
- The password is also encrypted in `infrastructure/secrets/secrets.sops.yml` (via sops/age)
- If you lose the password **and** all copies in sops, the repository is permanently unrecoverable
- Before rotating passwords, verify backups are working and restorable

### Repository Credentials

The role renders `/etc/restic/repo.env` with mode `0600` (root-only read). Backup scripts run as root via systemd service and source this file. The Telegram bot token and ops chat ID are also stored in this file (used for failure alerts).

## Configuration

### Defaults

- `restic_paths`: List of directories to back up. Default:
  - `/home/op/.fabric/` (operator workspace)
  - `/opt/vaultwarden/` (password manager data)
  - `/opt/kaneo/` (kanban board data)
  - `/home/op/code/mnemonic-masters/` (master clones and state)

- `restic_excludes`: Exclude patterns (`.pyc`, `__pycache__`, `node_modules`, `.terraform/`, `*.tfstate`)

- `restic_keep_daily: 7`, `restic_keep_weekly: 4`, `restic_keep_monthly: 6` — retention policy

- `restic_backup_schedule_oncalendar: "*-*-* 02:30:00"` — daily 02:30 UTC with 30min random jitter

### Backend Selection

Set via sops variables (see below):

#### Hetzner Storage Box (SFTP)

```yaml
restic_repository: "sftp:backups@u1234567.your-storagebox.de:/restic"
restic_password: "<your-strong-password>"
```

Pros:
- 1-2 TB quotas, affordable
- Native SFTP support in Restic
- Incremental sync friendly

#### S3-Compatible (AWS S3, Backblaze B2, Wasabi, etc.)

```yaml
restic_repository: "s3:s3.amazonaws.com/my-backup-bucket"
restic_s3_access_key: "AKIA..."
restic_s3_secret_key: "..."
restic_password: "<your-strong-password>"
```

Pros:
- Globally distributed
- Pay-as-you-go scaling
- Bandwidth included

## Sops Variables

Add to `infrastructure/secrets/secrets.sops.yml`:

```yaml
restic_repository: "sftp:backups@u1234567.your-storagebox.de:/restic"  # or s3:...
restic_password: "your-secure-password-here"
restic_s3_access_key: ""  # Only if using S3
restic_s3_secret_key: ""  # Only if using S3
telegram_bot_token: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
telegram_ops_chat_id: "123456789"
```

## Backup Verification

### First-Time Setup

After the first backup completes, the role will skip verification (no marker file yet). On second run, the marker file will be present and verification will proceed.

Create a marker file in the backed-up location to enable verification:

```bash
echo "mnemonic-protocol-backup-marker" | \
  sha256sum | awk '{print $1}' > /home/op/.fabric/backup-marker.txt
```

Note the SHA256 hash and update the script accordingly.

### Manual Verification

```bash
# Trigger backup
systemctl start fabric-backup.service

# Wait for completion
journalctl -u fabric-backup -f

# Run verification
bash /usr/local/bin/backup-verify.sh

# Check timer schedule
systemctl list-timers fabric-backup.timer
```

## Troubleshooting

### Backup Fails

Check logs:
```bash
journalctl -u fabric-backup.service -n 100 -e
```

Common issues:
- Network timeout to repository — retry with `systemctl start fabric-backup.service`
- Wrong password — verify `RESTIC_PASSWORD` in `infrastructure/secrets/secrets.sops.yml`
- Missing paths — verify paths exist and are readable by root

### Verify Fails

```bash
bash -x /usr/local/bin/backup-verify.sh
```

Check:
- Latest snapshot exists: `restic snapshots --latest=1`
- Marker file present in snapshot: `restic ls <snapshot-id> /home/op/.fabric/`

### Timer Not Running

```bash
systemctl status fabric-backup.timer
systemctl list-timers fabric-backup.timer
```

Enable if disabled:
```bash
systemctl enable fabric-backup.timer
systemctl start fabric-backup.timer
```

## Files

- `tasks/main.yml` — Install Restic, initialize repo, render scripts and systemd units
- `handlers/main.yml` — Systemd daemon reload
- `defaults/main.yml` — Configuration with documentation
- `templates/` — Jinja2 templates for scripts and systemd units
- `molecule/default/` — Molecule test harness (local Docker, mocked Telegram API)

## Testing

Run Molecule tests locally (requires Docker and Molecule):

```bash
molecule test
```

Smoke tests (post-apply on real VM):
```bash
# Timer is scheduled
systemctl list-timers fabric-backup.timer

# Manual backup succeeds
systemctl start fabric-backup.service
journalctl -u fabric-backup.service -n 50

# Verify script passes
bash /usr/local/bin/backup-verify.sh && echo "PASS" || echo "FAIL"

# Check snapshot exists
restic snapshots
```

## License

Part of the mnemonic-loop coding-fabric project. Same license as parent repository.
