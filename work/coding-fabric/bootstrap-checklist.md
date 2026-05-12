# Bootstrap Checklist — one-time operator steps

These are the irreducible manual steps that must happen ONCE before
`deploy-fabric.yml` (T24) can run successfully. Total time: ~30 minutes.

After this checklist completes, every subsequent deploy of coding-fabric (including
fabric updates) is fully automated via CI + recursive self-hosting.

---

## 1. Cloud / billing accounts

- [ ] **Hetzner Cloud**: create account, set up billing, create a project named
      `mnemonic-fabric`. Generate API token with R/W scope → save as
      GitHub Actions secret `HCLOUD_TOKEN`.
- [ ] **Restic off-site repo destination**: either Hetzner Storage Box (cheap,
      ~10 minute setup via Robot UI) or any S3-compatible (Backblaze B2, AWS S3,
      Wasabi). Capture credentials → save as GitHub Actions secrets
      `RESTIC_REPOSITORY` and `RESTIC_PASSWORD`. (`RESTIC_PASSWORD` is the encryption
      key — also save in your offline password manager. Loss = data loss.)

## 2. Tailscale

- [ ] Create a Tailscale account (free tier is fine for solo operator).
- [ ] Install Tailscale on your phone and laptop; join the tailnet.
- [ ] In Tailscale admin → Settings → Auth Keys, generate an **ephemeral, reusable,
      pre-approved auth key** with a short TTL (90 days). Save as GitHub Actions
      secret `TAILSCALE_AUTH_KEY`.
- [ ] (Optional but recommended) Set up tailnet ACLs so only your operator devices
      and ephemeral CI machines can reach `mnemonic-fabric` tagged nodes.

## 3. Telegram bot

- [ ] In Telegram, talk to `@BotFather`. Create a new bot
      (e.g., `MnemonicFabricBot`). Capture the bot token → save as GitHub Actions
      secret `TELEGRAM_BOT_TOKEN`.
- [ ] Create a new supergroup, enable topics (Forum mode). Add your bot as admin
      with permissions: Manage Topics, Send Messages, Pin, Delete.
- [ ] Capture the chat id (use `@RawDataBot` to inspect): save as
      `TELEGRAM_FORUM_CHAT_ID` GitHub Actions secret.
- [ ] (Topic creation itself is automated by Ansible role `telegram-init` (T05) — you
      don't need to create topics manually.)

## 4. Mnemonic protocol accounts

- [ ] **Solana devnet wallet**: generate a fresh keypair (e.g., `solana-keygen new
      --outfile mnemonic-devnet.json`). Fund it with at least 5 devnet SOL via
      `solana airdrop 5 --url devnet`. Encode keypair JSON as base64; save as
      GitHub Actions secret `SOLANA_DEVNET_KEYPAIR`.
- [ ] **Irys testnet**: create an Irys testnet account, fund it (small balance is
      enough for the receipt traffic). Capture private key → save as GitHub Actions
      secret `IRYS_TESTNET_PRIVATE_KEY`.
- [ ] **Mnemonic signing key**: generate the signing key the local Mnemonic MCP server
      will use (`mnemonic_prove_identity --new`, or equivalent CLI). Save as a sops-
      encrypted entry that ends up in Vaultwarden — but do NOT put the private key in
      GitHub secrets. Only the Vaultwarden initial admin password goes through GH.

## 5. AI / engine API keys

- [ ] **Anthropic API key**: generate at console.anthropic.com → save as
      `ANTHROPIC_API_KEY` GH secret.
- [ ] **OpenAI / Codex API key** (for `demo-client` topic): generate at platform.openai.com
      → save as `OPENAI_API_KEY` GH secret.

## 6. GitHub

- [ ] Create a fine-grained Personal Access Token with R/W on the six master repos
      and on `mnemonic-loop` (where this CI lives). Save as `GITHUB_DEPLOY_PAT`.

## 7. sops / age

- [ ] Install `age` and `sops` locally.
- [ ] Generate an age keypair: `age-keygen -o ~/.config/sops/age/keys.txt`.
- [ ] Capture the **private** key → save as GitHub Actions secret `SOPS_AGE_KEY`.
- [ ] Capture the **public** key → write to `infrastructure/secrets/.sops.yaml` as the
      sole recipient (committed in this repo).
- [ ] Encrypt `infrastructure/secrets/secrets.sops.yml` with sops, listing all secrets
      the Ansible roles consume (you can use the GH-secrets list above as the source of
      truth, copied locally into the sops file).
- [ ] Commit `secrets.sops.yml` and `.sops.yaml` to this repo. Do NOT commit
      `~/.config/sops/age/keys.txt` (the private key).

## 8. Vaultwarden initial admin password

- [ ] Generate a strong random password (32 chars). Save as GH Actions secret
      `VAULTWARDEN_INITIAL_ADMIN_PASSWORD` AND in your offline password manager.

## 9. Optional: validate locally before pushing the deploy tag

```sh
# Decrypt secrets locally to verify your sops setup
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops -d infrastructure/secrets/secrets.sops.yml

# Validate Tofu
cd infrastructure/tofu/hetzner && tofu init && tofu validate

# Validate Ansible
cd infrastructure/ansible && ansible-lint && ansible-playbook --syntax-check playbooks/deploy.yml
```

## 10. Push deploy tag

When all the above is done:

```sh
git tag fabric-v0.2.0 -m "first bootstrap deploy"
git push origin fabric-v0.2.0
```

GitHub Actions `deploy-fabric.yml` (T24) takes over. Watch the run in your phone
browser; once it reports green, send a `/start` to your bot in the `ops` topic.

---

## After bootstrap

- All subsequent fabric updates are PRs in `mnemonic-loop`. They go through the
  fabric's own smoke-gate (T19) before merging. The fabric updates itself.
- If something breaks the fabric badly: SSH over Tailscale to the VM and run
  `ansible-playbook infrastructure/ansible/playbooks/safe-mode-rollback.yml`. The
  fabric reverts to the last successful state and alerts the `ops` topic.
- This checklist is repeated only if you provision a fresh VM from scratch.

## What to do if you lose the operator laptop

- Hetzner: log into Cloud Console from a new device with 2FA.
- Tailscale: log in with your identity provider on a new device.
- Telegram, GitHub, Anthropic, OpenAI: standard password manager / 2FA recovery.
- sops/age private key: this is the master key. If you lose it AND your GH secret
  AND your offline backup, the secrets in this repo are unreadable. **Keep one
  printed-paper backup in a fire-safe location.**
- Vaultwarden initial admin password: same — printed-paper backup until you've rotated
  to a long-lived password.

## Cost expectation (rough)

- Hetzner CCX33 + 200 GB volume: ~€33/month
- Restic off-site (Storage Box 1 TB): ~€4/month
- Tailscale: free
- Solana devnet + Arweave testnet: free (testnets)
- Anthropic + OpenAI API: usage-based; budget €100/month for steady solo development

Total: ~€40/month fixed + AI API usage.
