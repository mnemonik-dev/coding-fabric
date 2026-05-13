#!/bin/bash
#
# last-known-good.sh — post-success hook for do-task
# Tags mnemonic-loop HEAD as `last-known-good` and pushes to remote.
# Skips if task completed was in mnemonic-loop branch (loop progress is not a stable point).
# Idempotent: silently skips if HEAD already tagged.
#
# Usage: fabric/safe-mode/last-known-good.sh [--branch BRANCH_NAME] [--repo REPO_PATH]
# Env: MNEMONIC_TASK_BRANCH (set by do-task), MNEMONIC_REPO_PATH (set by do-task)
#

set -euo pipefail

BRANCH="${MNEMONIC_TASK_BRANCH:-}"
REPO_PATH="${MNEMONIC_REPO_PATH:-.}"
TAG_NAME="last-known-good"
LOCK_FILE="${LKG_LOCK_FILE:-/var/lock/fabric-lkg.lock}"

log() {
  echo "[LKG] $(date +'%Y-%m-%d %H:%M:%S') — $*" >&2
}

acquire_lock() {
  local timeout=30
  local elapsed=0
  local lock_fd=9

  # Atomic lock acquisition using noclobber + redirect (POSIX-portable)
  while ! ( set -o noclobber; exec {lock_fd}>>"$LOCK_FILE" ) 2>/dev/null; do
    if [[ $elapsed -ge $timeout ]]; then
      log "ERROR: lock held for >$timeout sec; aborting"
      return 1
    fi
    sleep 1
    ((elapsed++))
  done

  # Successfully acquired; log PID for debugging
  echo "$$:$(date +'%Y-%m-%dT%H:%M:%SZ')" >&$lock_fd
}

release_lock() {
  rm -f "$LOCK_FILE"
}

main() {
  local args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --branch)
        BRANCH="$2"
        shift 2
        ;;
      --repo)
        REPO_PATH="$2"
        shift 2
        ;;
      *)
        shift
        ;;
    esac
  done

  if [[ -z "$BRANCH" ]]; then
    log "SKIP: no branch name (not called from do-task?)"
    return 0
  fi

  if [[ "$BRANCH" == "mnemonic-loop" ]]; then
    log "SKIP: task in mnemonic-loop; loop progress is not a stable point"
    return 0
  fi

  cd "$REPO_PATH"

  if ! git rev-parse HEAD &>/dev/null; then
    log "ERROR: not a git repository"
    return 1
  fi

  acquire_lock || return 1
  trap release_lock EXIT

  local head_sha
  head_sha=$(git rev-parse HEAD)

  if git rev-parse --verify "$TAG_NAME" &>/dev/null; then
    local existing_commit
    existing_commit=$(git rev-list -n 1 "$TAG_NAME")
    if [[ "$existing_commit" == "$head_sha" ]]; then
      log "SKIP: HEAD already tagged as $TAG_NAME"
      return 0
    fi
    log "Updating $TAG_NAME from $existing_commit to $head_sha"
    git tag -d "$TAG_NAME" || true
  fi

  # Create signed tag (requires git config user.signingkey to be set)
  if git config --get-all user.signingkey &>/dev/null; then
    git tag -s -m "Last known good — $BRANCH at $(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$TAG_NAME" HEAD || {
      log "WARN: failed to sign tag (check gpg/ssh key config); falling back to annotated"
      git tag -a -m "Last known good — $BRANCH at $(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$TAG_NAME" HEAD
    }
  else
    log "WARN: user.signingkey not configured; creating annotated (unsigned) tag"
    git tag -a -m "Last known good — $BRANCH at $(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$TAG_NAME" HEAD
  fi

  if git config --get-all remote.origin.url &>/dev/null; then
    # Use --force-with-lease instead of --force to prevent history rewrite
    if git push --force-with-lease origin "$TAG_NAME" 2>&1; then
      log "OK: pushed $TAG_NAME to origin (with lease protection)"
    else
      log "WARN: failed to push $TAG_NAME; may not have remote permission"
    fi
  else
    log "SKIP: no remote origin configured"
  fi

  log "OK: $TAG_NAME advanced to $head_sha"
}

main "$@"
