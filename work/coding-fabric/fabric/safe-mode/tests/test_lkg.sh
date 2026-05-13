#!/bin/bash
#
# test_lkg.sh — unit tests for last-known-good.sh
# Tests tag advancement, loop-skip, idempotency, and remote push logic.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LKG_SCRIPT="$SCRIPT_DIR/last-known-good.sh"

# Temp directories for test repos
TEST_DIR="/tmp/test-lkg-$$"
LOCK_DIR="$TEST_DIR/locks"
mkdir -p "$LOCK_DIR"
trap 'rm -rf "$TEST_DIR"' EXIT

log() {
  echo "[TEST] $*" >&2
}

test_lkg_advances_on_non_loop_task() {
  log "test_lkg_advances_on_non_loop_task"

  local repo="$TEST_DIR/repo-1"
  local lock="$LOCK_DIR/test1.lock"
  mkdir -p "$repo"
  cd "$repo"
  git init
  git config user.email "test@example.com"
  git config user.name "Test"

  echo "init" > file.txt
  git add file.txt
  git commit -m "init"
  local sha1=$(git rev-parse HEAD)

  LKG_LOCK_FILE="$lock" MNEMONIC_TASK_BRANCH="feature-foo" MNEMONIC_REPO_PATH="$repo" bash "$LKG_SCRIPT"

  if ! git rev-parse last-known-good &>/dev/null; then
    log "FAIL: last-known-good tag not created"
    return 1
  fi

  local tagged_sha=$(git rev-list -n 1 last-known-good)
  if [[ "$tagged_sha" != "$sha1" ]]; then
    log "FAIL: tag points to wrong commit: $tagged_sha != $sha1"
    return 1
  fi

  log "PASS: last-known-good advanced to $sha1"
}

test_lkg_does_not_advance_on_loop_task() {
  log "test_lkg_does_not_advance_on_loop_task"

  local repo="$TEST_DIR/repo-2"
  local lock="$LOCK_DIR/test2.lock"
  mkdir -p "$repo"
  cd "$repo"
  git init
  git config user.email "test@example.com"
  git config user.name "Test"

  echo "init" > file.txt
  git add file.txt
  git commit -m "init"

  git tag last-known-good HEAD
  local old_sha=$(git rev-parse last-known-good)

  echo "change" >> file.txt
  git add file.txt
  git commit -m "change"
  local new_sha=$(git rev-parse HEAD)

  LKG_LOCK_FILE="$lock" MNEMONIC_TASK_BRANCH="mnemonic-loop" MNEMONIC_REPO_PATH="$repo" bash "$LKG_SCRIPT"

  local current_sha=$(git rev-list -n 1 last-known-good)
  if [[ "$current_sha" != "$old_sha" ]]; then
    log "FAIL: tag advanced despite mnemonic-loop branch: $current_sha != $old_sha"
    return 1
  fi

  log "PASS: last-known-good skipped mnemonic-loop task"
}

test_lkg_idempotent() {
  log "test_lkg_idempotent_on_same_commit"

  local repo="$TEST_DIR/repo-3"
  local lock="$LOCK_DIR/test3.lock"
  mkdir -p "$repo"
  cd "$repo"
  git init
  git config user.email "test@example.com"
  git config user.name "Test"

  echo "init" > file.txt
  git add file.txt
  git commit -m "init"
  local sha=$(git rev-parse HEAD)

  LKG_LOCK_FILE="$lock" MNEMONIC_TASK_BRANCH="feature-x" MNEMONIC_REPO_PATH="$repo" bash "$LKG_SCRIPT"
  LKG_LOCK_FILE="$lock" MNEMONIC_TASK_BRANCH="feature-x" MNEMONIC_REPO_PATH="$repo" bash "$LKG_SCRIPT"

  local current_sha=$(git rev-list -n 1 last-known-good)
  if [[ "$current_sha" != "$sha" ]]; then
    log "FAIL: tag not idempotent: $current_sha != $sha"
    return 1
  fi

  log "PASS: last-known-good is idempotent"
}

test_lkg_replaces_stale_tag() {
  log "test_lkg_replaces_stale_tag"

  local repo="$TEST_DIR/repo-4"
  local lock="$LOCK_DIR/test4.lock"
  mkdir -p "$repo"
  cd "$repo"
  git init
  git config user.email "test@example.com"
  git config user.name "Test"

  echo "init" > file.txt
  git add file.txt
  git commit -m "init"
  git tag last-known-good HEAD
  local old_sha=$(git rev-parse HEAD)

  echo "change" >> file.txt
  git add file.txt
  git commit -m "change"
  local new_sha=$(git rev-parse HEAD)

  LKG_LOCK_FILE="$lock" MNEMONIC_TASK_BRANCH="feature-y" MNEMONIC_REPO_PATH="$repo" bash "$LKG_SCRIPT"

  local current_sha=$(git rev-list -n 1 last-known-good)
  if [[ "$current_sha" != "$new_sha" ]]; then
    log "FAIL: tag not updated: $current_sha != $new_sha"
    return 1
  fi

  if [[ "$current_sha" == "$old_sha" ]]; then
    log "FAIL: tag still points to old commit"
    return 1
  fi

  log "PASS: last-known-good replaced stale tag"
}

test_lkg_skips_on_no_branch() {
  log "test_lkg_skips_on_no_branch"

  local repo="$TEST_DIR/repo-5"
  mkdir -p "$repo"
  cd "$repo"
  git init
  git config user.email "test@example.com"
  git config user.name "Test"

  echo "init" > file.txt
  git add file.txt
  git commit -m "init"

  unset MNEMONIC_TASK_BRANCH || true
  MNEMONIC_REPO_PATH="$repo" bash "$LKG_SCRIPT"

  if git rev-parse last-known-good &>/dev/null; then
    log "FAIL: tag created despite missing MNEMONIC_TASK_BRANCH"
    return 1
  fi

  log "PASS: last-known-good skipped on missing branch"
}

main() {
  local failed=0

  test_lkg_advances_on_non_loop_task || ((failed++))
  test_lkg_does_not_advance_on_loop_task || ((failed++))
  test_lkg_idempotent || ((failed++))
  test_lkg_replaces_stale_tag || ((failed++))
  test_lkg_skips_on_no_branch || ((failed++))

  if [[ $failed -eq 0 ]]; then
    log "All tests passed"
    return 0
  else
    log "$failed test(s) failed"
    return 1
  fi
}

main "$@"
