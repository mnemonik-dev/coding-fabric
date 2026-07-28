# gbrain: Claude Code-native keyless mode (fix for garrytan/gbrain#94)

Prepared fix for [garrytan/gbrain#94](https://github.com/garrytan/gbrain/issues/94) —
"Plans for a Claude Code-native mode (no API keys, like gstack)?"

The patch adds `gbrain init --mode claude-code`: a Claude Code subscriber gets a
fully working brain with **zero provider API keys** (no `OPENAI_API_KEY`, no
`ANTHROPIC_API_KEY`).

## Applying

```bash
git clone https://github.com/garrytan/gbrain && cd gbrain
git checkout ddd66e1   # base commit the patch was built against (master, 2026-07-27)
git am path/to/0001-claude-code-keyless-mode.patch
```

## What was already there vs. what was missing

gbrain already had most of the building blocks; the blockers were at the seams:

| Piece | Status before the patch |
|---|---|
| `claude-cli` chat recipe (OAuth session, no key, subagent loop) | ✅ existed (their #334) |
| Hybrid search degrades to keyword + graph + title when no embedding provider | ✅ existed (Codex C3 / D1 paths) |
| `gbrain sync` skips embedding on the `embedding_disabled` sentinel | ✅ existed |
| `gbrain init` without keys | ❌ exits 1 unless you know `--no-embedding` |
| `gbrain import` on a no-embedding brain | ❌ refuses outright (deferred-setup error) |
| Chat/expansion defaults | ❌ point at `anthropic:*`, which needs a key |
| Upgrade path `init --force --embedding-model …` out of deferred mode | ❌ latent bug: persisted sentinel silently beat the explicit flag |

## What the patch does

- **`gbrain init --mode claude-code`** — implies `--pglite`; writes
  `claude_code_mode: true` alongside `embedding_disabled: true` (so every
  existing skip-embed callsite keeps working); defaults `chat_model` to
  `claude-cli:claude-sonnet-4-6`; seeds `search.mode = conservative` (the only
  mode bundle with no reranker / LLM-expansion spend); warns if the `claude`
  binary isn't on PATH.
- **`gbrain import`** treats a claude-code brain as implicit `--no-embed` —
  chunks land keyword-searchable instead of the command exiting 1.
- **`gbrain embed`** refuses with a keyless-specific message including the
  vector upgrade recipe.
- **advisor** no longer flags the missing embedding provider as a setup smell
  on keyless brains.
- **Bug fix**: explicit `--embedding-model` / `--model` now clears a persisted
  deferred/keyless sentinel, making the documented upgrade path work; a
  resolved model also removes stale sentinels from `config.json`.
- **Bug fix**: the post-init "subagent features require ANTHROPIC_API_KEY"
  caveat is suppressed for `claude-cli:*` chat models (that recipe drives the
  subagent loop through the Claude Code OAuth session).
- Docs: README quick-start section, CHANGELOG entry, `init --help` text, and
  the fail-loud no-provider hint now advertises the keyless path.
- Tests: `test/init-claude-code-mode.test.ts` (12 tests) covering the keyless
  predicate, tailored refusal messages, re-init seeding, and the claude-cli
  recipe's keyless contract (no required env vars, subagent loop supported).

## Verification performed

- `bun test` on the new suite plus all touched-area suites
  (init-env-detection, init-embed-check, init-mode-picker, init-provider-picker,
  sync-no-embed-sentinel, advisor-core, import-file, embed-preflight,
  embedding-dim-check, config): **all green**.
- `bun run typecheck` (tsc --noEmit): clean.
- End-to-end smoke with **all provider keys unset**:
  `init --mode claude-code` → config sentinels + conservative search mode
  written → `import` of markdown succeeds without vectors → `search` returns
  keyword-arm results → `embed --stale` refuses with the keyless message →
  `init --force --pglite --embedding-model zeroentropyai:zembed-1
  --embedding-dimensions 1280` clears the sentinels and configures the model.

## Not in scope (deliberate)

- Query expansion / reranking via Claude Code skills (the issue's stretch
  ideas): expansion is already gracefully gated behind
  `isAvailable('expansion')` and off in the conservative bundle; the reranker
  fails open. Nothing breaks keylessly; wiring them through `claude-cli` can be
  a follow-up.
- Postgres/Supabase keyless installs: `--mode claude-code` targets the local
  PGLite path per the issue; the runtime behavior (import/search) is
  engine-agnostic, so extending the flag later is trivial.

## Submitting upstream

This session couldn't open a PR against `garrytan/gbrain` (repo outside the
session's GitHub scope), so the change ships here as a `git am`-ready patch.
Apply on a fork branch and open the PR referencing issue #94.
