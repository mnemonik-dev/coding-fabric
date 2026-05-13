---
feature: mnemonic-attestation-integration
work_type: feature
size: M
status: backlog
created: 2026-05-12
last_updated: 2026-05-12
parent_feature: coding-fabric
related_features:
  - coding-fabric (parent; descoped attestation in Round 2 / 2026-05)
trigger: Mnemonic MCP server reaches v1.0+ production-ready release
---

# User Spec — Mnemonic Attestation Integration (backlog)

## 1. Goal

Re-introduce the Mnemonic MCP attestation integration into `coding-fabric`
once the local Mnemonic MCP server is production-ready. Specifically, restore
the 5-node per-feature attestation DAG (user-spec → tech-spec → tasks →
pre-deploy → deploy/post-deploy) that was descoped from `coding-fabric` in
Round 2 (2026-05) because the local MCP server is not yet stable enough to be
a hard deploy dependency.

## 2. Context (why this is a separate backlog feature)

The original `coding-fabric` spec made the Mnemonic MCP server a load-bearing
dependency: every meaningful artefact (user-spec, tech-spec, task completion,
pre-deploy QA, deploy verification) was supposed to be signed by the local
MCP server, producing a tamper-evident DAG per feature. During Wave 8
pre-deploy review, the user decided that the MCP server was not yet
production-ready, and the entire attestation concern was moved out of
`coding-fabric` scope so that the fabric can deploy without it.

The artefacts left in `coding-fabric` after the descope:

- `infrastructure/ansible/roles/mnemonic-mcp/` — exists but is gated behind
  `mnemonic_mcp_enabled: false`. When disabled (default) it installs 5 no-op
  stub hooks that exit 0 silently and log `attestation skipped (mnemonic-mcp
  disabled — see backlog)` to journald.
- `.github/workflows/e2e-smoke.yml` — DAG poll + sign step are gated behind
  an `mnemonic_mcp_enabled` workflow input (default false).
- `.github/workflows/post-deploy-avp.yml` — `sign-attestation` job is gated
  behind the same input; AVP step 2 verdict simplified from "5-node DAG
  verify" to "PR end-to-end completes".

This backlog feature flips all of those gates back on and adds whatever
operational glue is needed once the MCP server is ready.

## 3. Scope

When this feature is started, the following work must happen:

1. **Re-enable the `mnemonic-mcp` Ansible role**
   - Set `mnemonic_mcp_enabled: true` in production inventory.
   - Drop the no-op stub hooks block from `roles/mnemonic-mcp/tasks/main.yml`.
   - Confirm the real signing-key materialisation path (Vaultwarden →
     systemd LoadCredential) still works on Ubuntu 24.04 with the
     production-ready MCP binary.

2. **Wire the 5 hooks back into molyanov phase success paths**
   - `user-spec.sh`, `tech-spec.sh`, `task-complete.sh`, `pre-deploy.sh`,
     `post-deploy.sh` — each invokes `mnemonic_sign_memory` with the right
     parent and emits an attestation_id.
   - molyanov hook templates already exist; verify they still match the
     production-ready MCP server's CLI surface and adjust if signatures drift.

3. **Restore CI workflows to full attestation mode**
   - `e2e-smoke.yml`: set `mnemonic_mcp_enabled: true` by default, restore
     the DAG poll job dependency, re-enable the sign-smoke step.
   - `post-deploy-avp.yml`: restore the "5-node DAG verify" verdict in AVP
     step 2 (via `scripts/avp/steps/step-2.sh` — flip the
     `MNEMONIC_MCP_ENABLED=1` default), restore the `sign-attestation` job.

4. **Spec re-alignment**
   - Flip the DEFERRED markers on user-spec AC11–AC16 in
     `work/coding-fabric/user-spec.md` back to active.
   - Remove the DEFERRED note from tech-spec D14 and §2.6.
   - Document the operational policy for the production-mode MCP (key
     rotation cadence, on-chain anchoring schedule, retry / backoff on Solana
     / Arweave outage).

## 4. Dependencies (blocking)

- **Mnemonic MCP server v1.0+ release.** Must be a tagged, signed binary
  release suitable for production VM deployment (not just a dev preview).
  The current `mnemonic_mcp_version: 0.1.0` placeholder in
  `roles/mnemonic-mcp/defaults/main.yml` must be updated to the real version.
- **Signing-key generation procedure.** A documented, auditable procedure
  for generating the per-VM signing key, storing it in Vaultwarden, and
  rotating it on a defined cadence.
- **devnet / full-mode operational policy.** When `MNEMONIC_MODE=full` (only
  the `protocol-qa` topic today), the MCP server anchors to Solana devnet +
  Arweave testnet. Policy must cover: RPC outage behaviour (block? queue?),
  on-chain transaction cost budget, key rotation impact on existing anchored
  attestations.

## 5. Acceptance criteria

When this feature ships, all of the following must hold:

- AC1. `mnemonic_mcp_enabled: true` is the default in production inventory.
- AC2. coding-fabric AC11–AC16 (the original `work/coding-fabric/user-spec.md`
  §5.4 block) are all re-active and verified by automated tests.
- AC3. coding-fabric tech-spec D14 + §2.6 DAG note are restored without the
  DEFERRED marker.
- AC4. `e2e-smoke.yml` produces a verified 5-node DAG for every smoke run.
- AC5. `post-deploy-avp.yml` `sign-attestation` job runs and emits an
  attestation_id on every successful AVP run.
- AC6. Signing key rotation procedure is documented and tested on a non-
  production VM at least once.
- AC7. Operational runbook for full-mode RPC outage (Solana / Arweave) is
  documented, with at least one manual drill recorded.

## 6. Out of scope

- Federation / multi-operator key management (orthogonal; tracked separately).
- Solana mainnet anchoring (still devnet only — covered by the parent
  `coding-fabric` AC39 and the Mnemonic Protocol roadmap, not this feature).
- ruflo-mnemonic plugin (separate backlog feature).

## 7. Open questions

- **Persistent attestation storage location.** The descoped role wrote
  attestations under `/var/lib/mnemonic-mcp/features/<feature>/attestations.yml`.
  Confirm this path survives VM rebuilds (Restic backup covers it?) and that
  the path is the same the MCP server expects in v1.0+.
- **Rotation procedure for the signing key.** What cadence? Who initiates
  it? How are previously anchored attestations affected (presumably they
  remain verifiable against the historical pubkey)?
- **On-chain costs.** What is the per-attestation cost on Solana devnet (it
  should be effectively zero) and the budget cap for Arweave testnet
  uploads? At what point does cost become a deploy-blocker?

## 8. Initial implementation notes (for whoever picks this up)

- Most of the role logic is already in place — the descope was primarily a
  feature flag flip plus a no-op stub hook installer. The substantive work
  is the production-mode operational policy + verification, not the role
  code itself.
- Wave breakdown when this is picked up: probably (1) MCP binary version
  bump + role un-gating, (2) hook re-wiring + integration tests on a real
  VM, (3) CI workflow flips, (4) spec sync + AVP re-baseline.
