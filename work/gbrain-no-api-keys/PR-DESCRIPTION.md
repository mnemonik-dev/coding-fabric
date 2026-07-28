# Upstream PR description for garrytan/gbrain

Ready to paste when opening the PR from a fork (apply
`0001-claude-code-keyless-mode.patch` on a branch off `master`).

---

**Title:** feat(init): `--mode claude-code` — keyless mode for Claude Code subscribers (#94)

**Body:**

Closes #94.

## What

`gbrain init --mode claude-code` gives a Claude Code subscriber a fully working
brain with **zero provider API keys** — no `OPENAI_API_KEY`, no
`ANTHROPIC_API_KEY`:

```bash
gbrain init --mode claude-code   # keyless: PGLite, keyword + graph search
gbrain import ~/notes/
gbrain search "who works at Acme"
```

Search runs on the existing keyword + graph + title arms; chat commands route
through the existing `claude-cli` recipe (Claude Code OAuth session, billed to
the subscription).

## Why this is a small diff

Most of the machinery already existed — this PR wires the seams:

- the `claude-cli` chat recipe (#334) already runs keyless and supports the
  subagent loop;
- hybrid search already degrades gracefully to keyword + graph + title when no
  embedding provider is available (Codex C3 / D1 paths);
- `gbrain sync` already honors the `embedding_disabled` sentinel.

What was missing: `init` fail-louded without keys, `import` refused outright on
a no-embedding brain, and chat defaulted to `anthropic:*`.

## Changes

- **init**: new `--mode claude-code` flag. Implies `--pglite`; writes
  `claude_code_mode: true` alongside `embedding_disabled: true` (every existing
  skip-embed callsite keeps working); defaults `chat_model` to
  `claude-cli:claude-sonnet-4-6`; seeds `search.mode = conservative` (the one
  bundle with no reranker / LLM-expansion spend); best-effort check that the
  `claude` binary is on PATH. Re-inits stay keyless without re-passing the flag.
- **import**: a claude-code brain imports without vectors (implicit
  `--no-embed`) instead of exiting 1 with the deferred-setup error.
- **embed**: refusal message tailored to keyless mode, with the vector upgrade
  recipe.
- **advisor**: `embedding_disabled` is not a setup smell when keyless by design.
- **Bug fix**: explicit `--embedding-model` / `--model` now clears a persisted
  deferred/keyless sentinel. Previously the documented upgrade path
  (`gbrain init --force --embedding-model …` — the exact command
  `assertEmbeddingEnabled` prints) was silently ignored because the seeded
  `noEmbedding` won in `initPGLite`. A resolved model also removes stale
  sentinels from `config.json` (the "one or the other, never both" invariant).
- **Bug fix**: the post-init "subagent features require ANTHROPIC_API_KEY"
  caveat no longer fires for `claude-cli:*` chat models — that recipe declares
  `supports_subagent_loop` and drives Minions through the OAuth session.
- Docs: README quick-start subsection, CHANGELOG, `init --help`, and the
  fail-loud no-provider hint now advertises the keyless path.

## Tests

- New `test/init-claude-code-mode.test.ts` (12 tests): keyless predicate,
  tailored refusal messages, re-init seeding, and the claude-cli recipe's
  keyless contract (no required env vars, subagent loop supported).
- All touched-area suites green (init-env-detection, init-embed-check,
  init-mode-picker, init-provider-picker, sync-no-embed-sentinel, advisor-core,
  import-file, embed-preflight, embedding-dim-check, config);
  `bun run typecheck` clean.
- End-to-end smoke with all provider keys unset:
  init → import → keyword search returns the right page → `embed --stale`
  refuses with the keyless message → `init --force --pglite --embedding-model
  zeroentropyai:zembed-1 --embedding-dimensions 1280` clears the sentinels and
  configures the model.

## Out of scope (deliberate)

- Query expansion / reranking via Claude Code skills (the issue's stretch
  ideas): expansion is already gated behind `isAvailable('expansion')` and off
  in the conservative bundle; the reranker fails open. Nothing breaks keylessly;
  wiring them through `claude-cli` can be a follow-up.
- Postgres/Supabase keyless installs: the flag targets the local PGLite path
  per the issue; the runtime behavior (import/search) is engine-agnostic, so
  extending later is trivial.

## Unrelated heads-up found while working on this

`master` (at least since faf5cdb) has an accidentally **committed
`node_modules` symlink** pointing at `/tmp/fleet/repo/node_modules` — a path
from someone's build environment. It's tracked despite `node_modules/` being in
`.gitignore` (gitignore doesn't apply to already-tracked paths), so every fresh
clone gets a dangling symlink and `bun install` behaves oddly until you
`rm node_modules && bun install`. Worth a one-line cleanup commit:
`git rm --cached node_modules`. Not fixed in this PR to keep it scoped to #94.
