#!/usr/bin/env bash
# Server-side MCP deploy for the fabric VM, run over SSH by
# .github/workflows/deploy-mnemonic-mcp.yml (`ssh host "env ... bash -s" < remote.sh`).
#
# Layout it expects (verified 2026-10-10): REPO is a monorepo checkout whose
# docker-compose.yml takes the image tag from MCP_IMAGE_TAG and the Caddy
# network from CADDY_NETWORK_NAME, both in REPO/mcp.env. Compose runs with
# `--env-file mcp.env` under the project of the running mnemonik-mcp-1.
#
# MODE=plan      read-only: print current state and what apply would change
# MODE=apply     back up mcp.env, set keys, pull, recreate mcp, verify
# MODE=rollback  restore mcp.env; recreate mcp only if apply recreated it
#
# Inputs (env): MODE, REPO, IMAGE_TAG, FORCE_RESEED (true|false),
# CHAIN_STATS_WALLETS, SOLANA_RPC_URL. Test hooks: DOCKER, SUDO, SLEEP,
# HEALTH_TRIES, CADDY_CONTAINER, MCP_CONTAINER.
# Never prints secret values: only key names, the image tag, network names and
# the identity PUBLIC key.
set -euo pipefail

MODE="${MODE:?MODE required}"
REPO="${REPO:?REPO required}"
IMAGE_TAG="${IMAGE_TAG:-}"
FORCE_RESEED="${FORCE_RESEED:-false}"
SLEEP="${SLEEP:-5}"
HEALTH_TRIES="${HEALTH_TRIES:-72}"
CADDY_CONTAINER="${CADDY_CONTAINER:-vaultwarden-caddy-1}"
MCP_CONTAINER="${MCP_CONTAINER:-mnemonik-mcp-1}"
SUDO="${SUDO-sudo -n}"
if [ -z "${DOCKER:-}" ]; then
  if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo -n docker"; fi
fi
KEYPAIR_PATH="/keypair/identity.json"
MARKER="$REPO/.deploy-recreated"

fail() { echo "::error::$*"; exit 1; }

[ -f "$REPO/docker-compose.yml" ] && [ -f "$REPO/mcp.env" ] \
  || fail "$REPO lacks docker-compose.yml or mcp.env (run action=diagnose)"
cd "$REPO"

env_get() { grep -m1 -E "^$1=" mcp.env | cut -d= -f2- || true; }

# Replace or append KEY=VALUE in mcp.env, keeping the file's mode and owner.
env_set() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp "$REPO/.mcp.env.XXXXXX")"
  awk -v k="$key" -v v="$value" -F= '
    $1 == k { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }' mcp.env > "$tmp"
  chmod --reference=mcp.env "$tmp" 2>/dev/null || chmod 600 "$tmp"
  mv "$tmp" mcp.env
}

env_del() {
  local key="$1" tmp
  tmp="$(mktemp "$REPO/.mcp.env.XXXXXX")"
  grep -v -E "^$key=" mcp.env > "$tmp" || true
  chmod --reference=mcp.env "$tmp" 2>/dev/null || chmod 600 "$tmp"
  mv "$tmp" mcp.env
}

compose() { $DOCKER compose -p "$PROJ" --env-file mcp.env "$@"; }

networks_of() {
  $DOCKER inspect "$1" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || true
}

PROJ="$($DOCKER inspect "$MCP_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)"
PROJ="${PROJ:-mnemonik}"

if [ "$MODE" = rollback ]; then
  [ -f mcp.env.prev ] || { echo "no mcp.env.prev; nothing was changed"; exit 0; }
  mv mcp.env.prev mcp.env
  if [ -f "$MARKER" ]; then
    rm -f "$MARKER"
    compose up -d mcp
    echo "restored mcp.env and recreated mcp"
  else
    echo "restored mcp.env; mcp was not recreated by apply, so it is left running"
  fi
  exit 0
fi

# ---- state shared by plan and apply ----------------------------------------
grep -q 'MCP_IMAGE_TAG' docker-compose.yml \
  || fail "docker-compose.yml does not take the tag from MCP_IMAGE_TAG; layout changed"
[ -n "$IMAGE_TAG" ] || fail "IMAGE_TAG required"
case "$IMAGE_TAG" in v*|sha-*|latest) ;; *) fail "IMAGE_TAG must be v*, sha-* or latest" ;; esac

OLD_TAG="$(env_get MCP_IMAGE_TAG)"; OLD_TAG="${OLD_TAG:-latest (default)}"
WANT_NET="$(env_get CADDY_NETWORK_NAME)"
CADDY_NETS="$(networks_of "$CADDY_CONTAINER")"
MCP_NETS="$(networks_of "$MCP_CONTAINER")"
[ -n "$CADDY_NETS" ] || fail "cannot read the networks of $CADDY_CONTAINER"
case " $CADDY_NETS " in
  *" $WANT_NET "*) NET="$WANT_NET" ;;
  *" vaultwarden_vaultwarden "*) NET="vaultwarden_vaultwarden" ;;
  *) fail "Caddy networks ($CADDY_NETS) do not include CADDY_NETWORK_NAME=$WANT_NET or vaultwarden_vaultwarden" ;;
esac

KEYDIR="$($DOCKER inspect "$MCP_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/keypair"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
[ -n "$KEYDIR" ] || fail "cannot find the /keypair mount of $MCP_CONTAINER"
PUBKEY="$($SUDO python3 - "$KEYDIR/identity.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
secret = data if isinstance(data, list) else data.get("secret")
if not isinstance(secret, list) or len(secret) != 64:
    sys.exit(3)
alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
raw = bytes(secret[32:])
n, out = int.from_bytes(raw, "big"), ""
while n:
    n, r = divmod(n, 58)
    out = alphabet[r] + out
print("1" * (len(raw) - len(raw.lstrip(b"\0"))) + out)
PY
)" || fail "$KEYDIR/identity.json has no usable secret; refusing to change anything"

echo "compose project: $PROJ"
echo "image tag: $OLD_TAG -> $IMAGE_TAG"
echo "caddy networks: $CADDY_NETS"
echo "mcp networks:   $MCP_NETS"
echo "CADDY_NETWORK_NAME: ${WANT_NET:-unset} -> $NET"
echo "MNEMONIC_KEYPAIR_PATH: $( [ "$(env_get MNEMONIC_KEYPAIR_PATH)" = "$KEYPAIR_PATH" ] && echo unchanged || echo "-> $KEYPAIR_PATH")"
echo "EXPECTED_PUBKEY=$PUBKEY"

if [ "$MODE" = plan ]; then
  echo "plan only: nothing changed"
  exit 0
fi
[ "$MODE" = apply ] || fail "unknown MODE=$MODE"

# ---- apply -----------------------------------------------------------------
rm -f "$MARKER"
cp -p mcp.env mcp.env.prev
env_set MCP_IMAGE_TAG "$IMAGE_TAG"
env_set MNEMONIC_KEYPAIR_PATH "$KEYPAIR_PATH"
env_set CADDY_NETWORK_NAME "$NET"
[ -n "${CHAIN_STATS_WALLETS:-}" ] && env_set CHAIN_STATS_WALLETS "$CHAIN_STATS_WALLETS"
[ -n "${SOLANA_RPC_URL:-}" ] && env_set SOLANA_RPC_URL "$SOLANA_RPC_URL"
[ "$FORCE_RESEED" = true ] && env_set MNEMONIC_FORCE_RESEED 1

compose pull mcp
touch "$MARKER"
compose up -d mcp

status=starting
for _ in $(seq 1 "$HEALTH_TRIES"); do
  status="$($DOCKER inspect "$MCP_CONTAINER" --format '{{.State.Health.Status}}' 2>/dev/null || echo starting)"
  [ "$status" = healthy ] && break
  [ "$status" = unhealthy ] && break
  sleep "$SLEEP"
done
[ "$status" = healthy ] || fail "mcp container did not become healthy (status=$status)"

case " $(networks_of "$MCP_CONTAINER") " in
  *" $NET "*) ;;
  *) fail "mcp is not on Caddy's network $NET after recreate" ;;
esac
$DOCKER inspect "$MCP_CONTAINER" --format "{{(index .NetworkSettings.Networks \"$NET\").Aliases}}" \
  | grep -qw mnemonik-mcp || fail "mcp lacks the mnemonik-mcp alias on $NET"

[ "$FORCE_RESEED" = true ] && env_del MNEMONIC_FORCE_RESEED
echo "mcp healthy on $NET (tag $IMAGE_TAG)"
