# Mnemonic Protocol — Agent-Driven Development Loop

**Version:** 0.1.1 (decisions finalized)
**Audience:** coding agent (Claude Code or Codex) tasked with standing up the development fabric
**Format:** tech-spec, English, agent-readable
**Status:** decisions finalized, ready for `/decompose-tech-spec`

**Attestation:** `9253b6c0-78ea-4127-9397-8b7875f791b3`
**Solana tx:** `2bFxD4pWQZw87xnQcqVPqgTgfJ7VyLmTNiXbE8zCVpKPC61svZNcwYnTeNMiU3NtWheNdPV5fffHcF6iZkykBebf`
**Arweave tx:** `7wd7o88htpgkubidTv5iYi4CDE8xQaY9cnX6Qwa6EfK1`
**Created:** 2026-05-12

---

## 1. Goal

Stand up an autonomous coding fabric for the Mnemonic Protocol such that a single human, operating from Telegram, can:

- Issue intent (feature, bug fix, refactor, doc) once.
- Have it routed into the correct repository workspace.
- Have it specified, decomposed, implemented, tested, reviewed, and merged with explicit protocol-conformance proof.
- Have every meaningful decision attested via Mnemonic itself (dog-food).
- Have operational papercuts become tasks in the same fabric, not Slack messages.

Non-goals for v0.1: multi-operator concurrency, public-facing demo of the loop, replacing ruflo's memory layer with Mnemonic (separate future plugin — `ruflo-mnemonic` — explicitly out of scope here).

---

## 2. Architecture

Four cooperating layers plus one piece of glue to build. Everything runs on a single cloud VM.

- Transport: telegram-ai-agent (Telegram forum topic ↔ local Claude Code or Codex CLI)
- Methodology: molyanov-ai-dev (Spec → tech-spec → tasks → TDD → validators → QA)
- Substrate: ruflo full CLI install (AgentDB memory, SONA learning, swarm orchestration, hooks, plugin tools, observability)
- Attestation: Local Mnemonic MCP server (signs every meaningful decision, PR, QA outcome, deploy)
- Glue to build: workspace-manager (per-task git worktrees, dynamic Telegram topic routing, cleanup)

The human's only interface is Telegram. Everything else runs on the loop VM, accessed administratively over VPN.

## 3. Components, repos, topics

Six repos: mnemonic-core, mnemonic-mcp, mnemonic-wasm, mnemonic-demo-client, mnemonic-docs, mnemonic-loop. Eight Telegram topics (core, mcp, wasm, demo-client, docs, loop, protocol-qa, ops). protocol-qa runs in MNEMONIC_MODE=full against Solana devnet + Arweave testnet; all others local. Engine per topic (Claude Code default, Codex for demo-client). VM baseline: 8+ vCPU, 32 GB+ RAM, 200 GB+ disk. Backups nightly.

## 4. Workspace manager and /do-feature bridge

Per-task git worktrees under ~/code/mnemonic-workspaces/<TASK-ID>, sccache shared, cap 10 active. cwd:"DYNAMIC" sentinel in telegram-ai-agent resolves via workspace-manager HTTP API. /do-feature rewired to dispatch through ruflo's swarm MCP tools (swarm_init, agent_spawn) with per-task cwd injection. Molyanov validators delegate to ruflo plugins (skeptic→jujutsu, security-auditor→security-audit, post-deploy-qa→browser).

## 5. Mnemonic attestation trigger points

Five points: user-spec approved (memory), tech-spec approved (memory, parent=user-spec), task completion (receipt, parent=tech-spec), pre-deploy-qa pass (receipt, parent=task receipts), deploy+post-deploy verification (agent.state, parent=pre-deploy). Result: tamper-evident lineage DAG per feature.

## 6. QA gates by task type

Schema/CBOR changes require cross-platform byte-equivalence proof + human approval. Crypto primitive changes require third-party verification proof. MCP surface changes require client compatibility tests. WASM bindings require browser test + byte-equivalence. Full-mode integration requires devnet round-trip + independent verifier recovery. Crypto-crate dependency bumps require human approval gate. mnemonic-loop fabric changes require smoke-test gate (§11.4). PR template enforces four sections: what changed, automated validation, protocol conformance, mnemonic attestation. 500-line PR cap.

## 7. Cohabitation rules

ruflo-autopilot off for core/mcp/wasm/protocol-qa/loop; on for docs/demo-client. MEMORY_NAMESPACE pinned per topic. Molyanov Project Knowledge canonical. ruflo-aidefence disabled in core/mcp (false positives on hex keys). ruflo-rag-memory disabled in protocol-qa (fresh context per run).

## 8. Watchdogs

fabric-watchdog runs every 5 minutes. Checks: orphaned worktrees, hung tmux, expired Solana RPC, exhausted Irys, stale ruflo swarms, disk pressure (75/85/90% thresholds on 50 GB budget), master clone drift, stale PRs, failed Mnemonic attestation, stale last-known-good tag. All alerts post to ops topic; human can /turn-into-task to convert into Kaneo card.

## 9. Secrets and trust

All state on VM. Vaultwarden holds credentials; .env files generated per-worktree, deleted on cleanup. Solana devnet only, no mainnet keypair. Sanitized logging filter strips base58 keys, JWK fragments, sk-/api_/ANTHROPIC_ tokens, Telegram file URLs. Agents: broad local permissions inside worktree, no write outside, no direct Vaultwarden, no push to main, no production deploy.

## 10. Safe mode (recursive self-hosting recovery)

mnemonic-loop PRs merge themselves through the same fabric. Recovery:
- Auto-updating last-known-good git tag after every successful non-loop task completion
- Smoke gate before any loop PR merges (trivial docs task end-to-end with candidate fabric; 15 min budget)
- Manual rollback: SSH + fabric-safe-mode rollback script (stops services, checkout last-known-good, rebuild, restart, notify)
- ops topic runs independently of workspace-manager/ruflo/watchdog as always-available admin back-channel

## 11. Implementation phases (~21 days)

- Phase 0 (d1-3): VM, VPN, Vaultwarden, Kaneo, Telegram forum, master clones, sanitized logging, backups
- Phase 1 (d4-6): workspace-manager + sccache, telegram-ai-agent DYNAMIC sentinel patch
- Phase 2 (d7-9): ruflo + molyanov + Mnemonic in one session; test feature end-to-end through full pipeline
- Phase 3 (d10-13): /do-feature ↔ ruflo swarm bridge with per-task cwd injection
- Phase 4 (d14-16): task_type field, PR template enforcement, byte-equivalence + MCP compatibility harnesses
- Phase 5 (d17-21): fabric-watchdog, /turn-into-task, last-known-good auto-update, fabric-safe-mode tested via deliberate break, smoke gate, first recursive self-merge

## 12. Decisions finalized

- Task tracker: Kaneo self-hosted on the loop VM
- Loop host: single cloud VM, VPN-fronted
- Default coding agent: mixed per-topic, no global default (Claude Code most topics; Codex for demo-client)
- Disk budget: heavy — up to 10 parallel worktrees, 50 GB
- mnemonic-loop recursive self-hosting: yes, with §11.4 safe-mode recovery

## 13. Out of scope for v0.1

ruflo-mnemonic plugin (backlog), federation/multi-operator, mainnet Solana, public demo of the loop, OpenClaw-equivalent PM agent, VM HA.

---

End of attested spec.
