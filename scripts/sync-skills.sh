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

SKILLS=(
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

COMMANDS=(
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

for s in "${SKILLS[@]}"; do
  if [[ -d "$SRC_SKILLS/$s" ]]; then
    run_rsync "$SRC_SKILLS/$s/" "$DEST/skills/$s/"
  else
    echo "warn: $SRC_SKILLS/$s not found, skipping" >&2
  fi
done

for c in "${COMMANDS[@]}"; do
  if [[ -f "$SRC_COMMANDS/$c" ]]; then
    if [[ "$mode" == "--diff" ]]; then
      diff -u "$DEST/commands/$c" "$SRC_COMMANDS/$c" 2>/dev/null || true
    else
      cp "$SRC_COMMANDS/$c" "$DEST/commands/$c"
    fi
  else
    echo "warn: $SRC_COMMANDS/$c not found, skipping" >&2
  fi
done

if [[ "$mode" != "--diff" ]]; then
  echo "synced $(ls "$DEST/skills" | wc -l | tr -d ' ') skills, $(ls "$DEST/commands" | wc -l | tr -d ' ') commands"
fi
