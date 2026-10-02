# claude-flow / ruflo CLI

Reference for `npx @claude-flow/cli@latest` — the multi-agent orchestration and
code-intelligence toolkit referenced throughout the global CLAUDE.md and wired
into this repo as an MCP server. This doc covers **how to actually use it to
speed up development**, and — just as important — where it does and doesn't help
on *this* codebase.

## What it is (and naming)

- **Package:** `@claude-flow/cli` (currently `v3.16.3`). Invoked as
  `npx @claude-flow/cli@latest <command>`.
- **Binary/brand:** the installed binary is `ruflo`. The CLI's own `--help`
  examples say `claude-flow` — treat `claude-flow`, `ruflo`, and
  `npx @claude-flow/cli@latest` as the same tool.
- **In this repo:** declared in `.mcp.json` as the `ruflo` MCP server with
  `autoStart: false` (topology `hierarchical-mesh`, max 15 agents, hybrid
  memory). Runtime artifacts land in `.claude-flow/` (gitignored). The tool's
  own generated capability dump lives at `.claude-flow/CAPABILITIES.md`.

It is **not** this project's build system. The Python services under `fabric/*/`
build and test with plain `pytest`/`ruff`/`mypy` (see the "Development commands"
section of `CLAUDE.md`). claude-flow sits *beside* that as a coordination and
intelligence layer.

## First-run cost

The first `npx @claude-flow/cli@latest ...` of a session installs the package
(large, ~1–3 min, pulls native deps and an ONNX embedding model). Subsequent
calls in the same environment are fast because npx caches it. If you'll use it
repeatedly, warm the cache once up front.

## Reality check on this codebase

The **`analyze` code-intelligence suite is JS/TS-oriented**. Run against a
Python service it reports `Files analyzed: 0`:

```bash
npx @claude-flow/cli@latest analyze complexity fabric/workspace-manager
# -> Files analyzed: 0   (the .py files are not picked up)
```

So on coding-fabric the concrete *code-analysis* value is limited. What remains
genuinely useful and language-agnostic here: **diagnostics, task→agent routing,
the memory/pattern store, and swarm coordination**. Prioritize those.

## Commands that actually help dev velocity

### 1. Diagnostics — `doctor`

Fastest way to confirm the tool, Claude Code CLI, daemon, MCP, and API wiring are
healthy before you lean on any of it.

```bash
npx @claude-flow/cli@latest doctor            # full health check
npx @claude-flow/cli@latest doctor --fix      # PRINTS suggested fixes (does not auto-apply)
npx @claude-flow/cli@latest doctor -c mcp     # check one component (mcp, daemon, memory, api, git, ...)
```

Note `--fix` only prints commands for you to copy/paste; `--install` is the one
that actually installs missing deps (Claude Code CLI).

### 2. Task → agent routing — `route`

Q-Learning router that picks the best agent type for a task description. Useful
when you're unsure which specialist to spawn, and it learns from feedback.

```bash
npx @claude-flow/cli@latest route task "add retry to kaneo_client POST calls"
npx @claude-flow/cli@latest route list-agents      # all routable agent types
npx @claude-flow/cli@latest route stats            # router statistics
npx @claude-flow/cli@latest route --agent coder "fix bug"   # force an agent
npx @claude-flow/cli@latest route feedback ...     # tell it whether the pick was good (improves future routing)
```

### 3. Memory / pattern reuse — `memory`

Semantic (vector) store for "what worked". This is the backbone of the
"Memory & Learning" loop in CLAUDE.md — search before a task, store after a win,
so recurring patterns in the fabric (Kaneo REST shapes, worktree lifecycle,
dispatch quirks) don't get re-derived.

```bash
npx @claude-flow/cli@latest memory search --query "kaneo status transition"   # before starting
npx @claude-flow/cli@latest memory store --namespace patterns \
    --key "worktree-cleanup" --value "DELETE /worktree/{id} then assert .count"  # after a win
npx @claude-flow/cli@latest memory stats
npx @claude-flow/cli@latest memory cleanup     # drop stale/expired entries
```

### 4. Self-learning hooks — `hooks`

Pre/post hooks that feed the routing + memory systems. Two that pay off:

```bash
# Bootstrap intelligence from this repo (4-step pipeline + embeddings) — run once,
# then routing/memory suggestions are grounded in this codebase.
npx @claude-flow/cli@latest hooks pretrain

# Get context + agent suggestions before touching a file
npx @claude-flow/cli@latest hooks pre-edit -f fabric/workspace-manager/dispatch.py

# Dispatch a background worker (audit / optimize / testgaps / map / document)
npx @claude-flow/cli@latest hooks worker dispatch --trigger audit
```

There are also `model-route` / `model-stats` hooks that pick a Claude tier
(haiku/sonnet/opus) by task complexity — useful for cost/latency tuning.

### 5. Multi-agent swarm — `swarm`

For the 3+ file / cross-module work the CLAUDE.md "When to Swarm" table calls
out. Coordinates a team under one topology instead of you spawning agents by hand.

```bash
npx @claude-flow/cli@latest swarm init --topology hierarchical --max-agents 8 --strategy specialized
npx @claude-flow/cli@latest swarm status
npx @claude-flow/cli@latest swarm coordinate --agents 15    # full V3 hierarchical-mesh run
```

Strategy `specialized` = clear non-overlapping roles (anti-drift); `hierarchical`
topology = a queen agent controls workers. Match to the routing table in CLAUDE.md
(Feature → architect/coder/tester/reviewer, hierarchical; Bug → researcher/coder/tester).

### 6. `analyze` — use with the JS/TS caveat

Concrete and fast **on JS/TS trees** (tree-sitter based). Keep in the toolbox for
any JS/TS code you touch; skip for the Python services.

```bash
npx @claude-flow/cli@latest analyze complexity src/ --threshold 15   # flag hotspots
npx @claude-flow/cli@latest analyze circular src/                    # circular deps
npx @claude-flow/cli@latest analyze imports src/ --external          # dependency surface
npx @claude-flow/cli@latest analyze diff --risk                      # risk-classify current git diff
```

## A practical per-task loop

Mirrors the "Before/After Any Task" blocks in CLAUDE.md, using the commands above:

```bash
# 1. before: recall + route
npx @claude-flow/cli@latest memory search --query "<task keywords>"
npx @claude-flow/cli@latest route task "<task description>"

# 2. (optional) swarm for 3+ files
npx @claude-flow/cli@latest swarm init --topology hierarchical --max-agents 8 --strategy specialized

# 3. after: persist what worked
npx @claude-flow/cli@latest memory store --namespace patterns --key "<name>" --value "<what worked>"
```

## Advanced / experimental (know they exist)

`neural` (pattern training, Flash Attention), `performance` (profiling,
benchmarks), `hive-mind` (queen-led consensus), `embeddings`, `guidance`,
`autopilot`, `deployment`, `plugins`. These target the tool's ambitious V3
feature set; treat performance/speedup claims as aspirational and verify against
your own measurements before relying on them. `npx @claude-flow/cli@latest
<command> --help` gives the real subcommands for any of these.

## Gotchas

- **Binary says `ruflo`, help says `claude-flow`** — same tool, don't get confused.
- **`--fix` prints, it doesn't apply.** Copy/paste the commands yourself.
- **`--format table` is rejected** by `analyze` — only `text` and `json` are valid.
- **`analyze` ignores Python** — 0 files on `fabric/*` services.
- **Artifacts are gitignored** — `.claude-flow/`, `.swarm/`, `ruvector.db`,
  `.fastembed_cache/` are all runtime state, not source. Don't commit them.
