#!/usr/bin/env bash
# Opens the upstream PR garrytan/gbrain <- mnemonik-dev:claude-code-keyless-mode.
#
# Run from any machine with a GitHub token that can see garrytan/gbrain
# (a classic PAT with `public_repo`, or a fine-grained token; the branch
# already sits on the mnemonik-dev/gbrain fork):
#
#   GITHUB_TOKEN=ghp_... ./open-upstream-pr.sh
#
# Why this script exists: the Claude Code session that prepared the fix is
# repo-scoped to mnemonik-dev/* — its outbound proxy 403s every GitHub API
# call to garrytan/gbrain, and cross-owner add_repo is a v1 limitation.
set -euo pipefail
cd "$(dirname "$0")"

: "${GITHUB_TOKEN:?set GITHUB_TOKEN to a token that can access garrytan/gbrain}"

curl -sS -X POST \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/garrytan/gbrain/pulls \
  -d @pr-payload.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("html_url") or json.dumps(d, indent=2))'
