#!/usr/bin/env bash
# Run Team Sync on a remote server and work with it from this machine.
#
#   scripts/remote.sh deploy  user@host "CTF Lviv 2026" Vo1ic            # private: SSH tunnel
#   scripts/remote.sh deploy  user@host "CTF Lviv 2026" Vo1ic --domain sync.example.org
#   scripts/remote.sh tunnel  user@host        # private mode: web + API on 127.0.0.1:18000
#   scripts/remote.sh fetch   user@host        # copy owner.json / invite.json here
#   scripts/remote.sh update  user@host        # ship new code, keep data
#   scripts/remote.sh logs    user@host
#
# Private mode publishes nothing to the Internet: the server listens on the remote
# loopback and you reach it through `ssh -L`. Access files then point at
# http://127.0.0.1:<local port>, so agents on this laptop use the tunnel transparently.
# Public mode puts Caddy with automatic HTTPS in front (ports 80/443, DNS required).
set -euo pipefail
cd "$(dirname "$0")/.."

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
[ $# -ge 2 ] || usage
ACTION="$1"; HOST="$2"; shift 2
DIR="ctf-team-sync"
LOCAL_PORT="${TEAM_SYNC_LOCAL_PORT:-18000}"
DOMAIN=""
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --local-port) LOCAL_PORT="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
SLUG="$(printf '%s' "$HOST" | tr -c 'A-Za-z0-9.-' '-')"
LOCAL_DIR=".team-sync/remote-$SLUG"

remote() { ssh -o BatchMode=yes "$HOST" "$@"; }
quote() { printf '%q' "$1"; }

ship() {
  remote "command -v docker >/dev/null && docker compose version >/dev/null" || {
    echo "Docker with the compose plugin is required on $HOST" >&2; exit 1; }
  remote "mkdir -p $(quote "$DIR")"
  rsync -az --delete \
    --exclude .git --exclude .venv --exclude node_modules --exclude .team-sync \
    --exclude data --exclude .env --exclude '.env.*' --exclude test-results \
    --exclude playwright-report --exclude compose.local.yaml --exclude __pycache__ \
    ./ "$HOST:$DIR/"
}

# shellcheck disable=SC2016  # expanded on the remote host
compose_env='set -a; . ./.env; set +a; export COMPOSE_FILE=compose.yaml${TEAM_SYNC_DOMAIN:+:deploy/compose.public.yaml}'

case "$ACTION" in
  deploy)
    [ ${#POSITIONAL[@]} -eq 2 ] || usage
    NAME="${POSITIONAL[0]}"; OWNER="${POSITIONAL[1]}"
    ship
    if [ -n "$DOMAIN" ]; then PUBLIC="https://$DOMAIN"; else PUBLIC="http://127.0.0.1:$LOCAL_PORT"; fi
    remote "cd $(quote "$DIR") && TEAM_SYNC_DOMAIN=$(quote "$DOMAIN") \
      TEAM_SYNC_PUBLIC_URL=$(quote "$PUBLIC") scripts/quickstart.sh $(quote "$NAME") $(quote "$OWNER")"
    "$0" fetch "$HOST" --dir "$DIR"
    echo
    if [ -n "$DOMAIN" ]; then
      echo "Open https://$DOMAIN and connect with the owner access file above."
    else
      echo "Private mode. Start the tunnel and keep it open while you work:"
      echo "  scripts/remote.sh tunnel $HOST --local-port $LOCAL_PORT"
      echo "then open http://127.0.0.1:$LOCAL_PORT"
    fi
    ;;
  update)
    ship
    remote "cd $(quote "$DIR") && $compose_env && docker compose up -d --build"
    ;;
  fetch)
    mkdir -p "$LOCAL_DIR" && chmod 700 "$LOCAL_DIR"
    rsync -az --chmod=Du=rwx,Dgo=,Fu=rw,Fgo= "$HOST:$DIR/.team-sync/" "$LOCAL_DIR/"
    echo "Access files copied to $LOCAL_DIR:"
    find "$LOCAL_DIR" -name '*.json' -maxdepth 2 | sed 's/^/  /'
    echo "MCP for this laptop: uv run team-sync-agent --access-file $LOCAL_DIR/<ctf>/owner.json mcp-config --host opencode"
    ;;
  tunnel)
    PORT="$(remote "grep -E '^TEAM_SYNC_HOST_PORT=' $(quote "$DIR")/.env | tail -1 | cut -d= -f2")"
    PORT="${PORT:-8000}"
    echo "Tunnel: http://127.0.0.1:$LOCAL_PORT -> $HOST:127.0.0.1:$PORT (Ctrl+C to stop)"
    exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
      -L "127.0.0.1:$LOCAL_PORT:127.0.0.1:$PORT" "$HOST"
    ;;
  logs)
    remote "cd $(quote "$DIR") && $compose_env && docker compose logs --tail=200 -f server"
    ;;
  *) usage ;;
esac
