#!/usr/bin/env bash
# Refresh the vendored Molyanov skill bundle from the operator's
# ~/.claude/{skills,commands} so the next deploy ships current versions
# to every Telegram-spawned claude session.
#
# Curated subset only — third-party skills (agentdb-*, github-*, v3-*,
# swarm-*, etc.) are intentionally excluded.
#
# Usage:
#   scripts/sync-skills.sh          # copy local → vendored
#   scripts/sync-skills.sh --diff   # show diff without copying

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_SKILLS="$HOME/.claude/skills"
SRC_COMMANDS="$HOME/.claude/commands"
DEST="$ROOT/infrastructure/ansible/files/claude-skills"

# Operator's local skill names use hyphens; vendored copy uses underscores
# so Telegram's setMyCommands accepts them (Bot API allows /[a-z0-9_]+ only).
# Sync rewrites the name during copy and additionally creates a
# hyphenated symlink in the vendored bundle pointing at the underscored
# dir, so prior references that name a skill with hyphens keep working.
SKILLS_HYPHEN=(
  code-reviewing
  code-writing
  deploy-pipeline
  infrastructure-setup
  documentation-writing
  feature-execution
  methodology
  post-deploy-qa
  pre-deploy-qa
  project-planning
  user-spec-planning
  tech-spec-planning
  task-decomposition
  prompt-master
  skill-master
  skill-tester
  security-auditor
  test-master
)

COMMANDS_HYPHEN=(
  decompose-tech-spec.md
  do-feature.md
  do-task.md
  done.md
  init-project.md
  init-project-knowledge.md
  new-tech-spec.md
  new-user-spec.md
  write-code.md
)

# Translate a hyphenated name to underscored — POSIX bash, no parameter-
# expansion tricks (need to work on macOS bash 3.2).
_to_underscore() { echo "$1" | tr - _; }

mode="${1:-copy}"

run_rsync() {
  local src="$1" dst="$2"
  if [[ "$mode" == "--diff" ]]; then
    rsync -avn --delete "$src" "$dst" | grep -vE '^(sending|sent|total|$|\.d\.\.t)' || true
  else
    rsync -a --delete "$src" "$dst"
  fi
}

mkdir -p "$DEST/skills" "$DEST/commands"

for s in "${SKILLS_HYPHEN[@]}"; do
  if [[ ! -d "$SRC_SKILLS/$s" ]]; then
    echo "warn: $SRC_SKILLS/$s not found, skipping" >&2
    continue
  fi
  underscored=$(_to_underscore "$s")
  run_rsync "$SRC_SKILLS/$s/" "$DEST/skills/$underscored/"
  if [[ "$mode" != "--diff" && "$s" != "$underscored" ]]; then
    # hyphenated symlink → underscored real dir
    (cd "$DEST/skills" && ln -sfn "$underscored" "$s")
  fi
done

for c in "${COMMANDS_HYPHEN[@]}"; do
  if [[ ! -f "$SRC_COMMANDS/$c" ]]; then
    echo "warn: $SRC_COMMANDS/$c not found, skipping" >&2
    continue
  fi
  base="${c%.md}"
  underscored=$(_to_underscore "$base")
  if [[ "$mode" == "--diff" ]]; then
    diff -u "$DEST/commands/${underscored}.md" "$SRC_COMMANDS/$c" 2>/dev/null || true
  else
    cp "$SRC_COMMANDS/$c" "$DEST/commands/${underscored}.md"
    if [[ "$base" != "$underscored" ]]; then
      (cd "$DEST/commands" && ln -sfn "${underscored}.md" "${base}.md")
    fi
  fi
done

if [[ "$mode" != "--diff" ]]; then
  echo "synced $(ls "$DEST/skills" | wc -l | tr -d ' ') skill entries (incl. hyphenated symlinks), $(ls "$DEST/commands" | wc -l | tr -d ' ') command entries"
fi
