# Universal Paywall staging stack

This directory is the deployment contract for the Phase-1, exact-payment
facilitator used by Mnemonic anchoring. It is deliberately an isolated stack:

- directory: `/opt/universal-paywall-staging`
- Compose project: `universal-paywall-staging`
- network: Base Sepolia (`CHAIN_ID=84532`) only
- listener: `127.0.0.1:8403` only
- durable state: the stack's own `facilitator-payment-store` volume

It must not reuse `/opt/mnemonik-server`, its environment file, or any of its
volumes. This stack deploys only the Paywall facilitator. Wiring the separately
staged MCP and approval UI to it remains part of Task 07a.

## One-time operator bootstrap

Before the first `apply`, provision the following files through the repository
SOPS/Ansible secret path, never by committing plaintext:

```text
/opt/universal-paywall-staging/.env                         mode 0600
/opt/universal-paywall-staging/secrets/receipt-private-key.pem  mode 0600, uid 10001
```

Use [`.env.example`](.env.example) as a key-only guide. The deploy workflow
requires `CHAIN_ID=84532`, `NETWORK=base-sepolia`, and
`EXACT_PAYMENTS_ENABLED=1`; it rejects Base mainnet and mutable image tags.

Also create the GitHub Environment `paywall-staging`, configure required
reviewers, and scope `TAILSCALE_AUTH_KEY` and `CI_SSH_PRIVATE_KEY` to it.
This prevents a normal repository workflow from silently reaching the VPS.

## Release procedure

1. Publish a facilitator image from Universal Paywall. Use its immutable SHA
   tag, never `latest` or `staging`.
2. Run `deploy-paywall-staging` with `action=diagnose` to check the remote
   secret file modes, container state, and image without changing anything.
3. After environment approval, run `action=apply` with the immutable image
   reference. The workflow installs only `docker-compose.yml`, preserves the
   secret `.env`, checks the Base Sepolia/exact-only invariants, and waits for
   `/health` inside the container.
4. A rollback is a fresh approved dispatch using the previous immutable image
   reference. It does not delete the payment-store volume; preserving it is
   required for exact-payment receipt and retry safety.

The CI job does not create testnet credentials, fund wallets, or migrate this
stack to Base mainnet. Those are separate, reviewed operational actions.
