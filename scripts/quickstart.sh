#!/usr/bin/env bash
# One command for the team owner (or an agent acting for them):
#   scripts/quickstart.sh "CTF Lviv 2026" Vo1ic
# 1) creates .env with random secrets if missing (never printed),
# 2) picks a free loopback port (or reuses a running Team Sync),
# 3) starts PostgreSQL + server with docker compose and waits for /health,
# 4) creates the project, the owner access file and a team invite file.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:?usage: scripts/quickstart.sh \"CTF name\" OwnerNickname}"
OWNER="${2:?usage: scripts/quickstart.sh \"CTF name\" OwnerNickname}"

random_hex() { python3 -c 'import secrets; print(secrets.token_hex(32))'; }
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

if [ ! -f .env ]; then
  (umask 077 && printf 'TEAM_SYNC_BOOTSTRAP_TOKEN=%s\nTEAM_SYNC_DB_PASSWORD=%s\n' \
    "$(random_hex)" "$(random_hex)" > .env)
  echo "created .env with random secrets"
fi

PORT="${TEAM_SYNC_HOST_PORT:-$(env_value TEAM_SYNC_HOST_PORT)}"
if [ -z "$PORT" ]; then
  PORT="$(python3 - <<'EOF'
import json, socket, urllib.request
for port in range(8000, 8100):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            if json.load(r).get("service") == "ctf-team-sync":
                print(port); break
            continue
    except Exception:
        pass
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            continue
    print(port); break
EOF
)"
  echo "TEAM_SYNC_HOST_PORT=$PORT" >> .env
fi
URL="http://127.0.0.1:$PORT"
echo "Team Sync URL: $URL"

DOMAIN="${TEAM_SYNC_DOMAIN:-$(env_value TEAM_SYNC_DOMAIN)}"
if [ -n "$DOMAIN" ] && [ -z "$(env_value TEAM_SYNC_DOMAIN)" ]; then
  echo "TEAM_SYNC_DOMAIN=$DOMAIN" >> .env  # remembered for updates
fi
export COMPOSE_FILE=compose.yaml
if [ -n "$DOMAIN" ]; then
  # Public HTTPS through Caddy; every later docker compose call uses both files.
  export COMPOSE_FILE=compose.yaml:deploy/compose.public.yaml
  export TEAM_SYNC_DOMAIN="$DOMAIN"
  export TEAM_SYNC_PUBLIC_URL="${TEAM_SYNC_PUBLIC_URL:-https://$DOMAIN}"
fi
TEAM_SYNC_HOST_PORT="$PORT" docker compose up -d --build

python3 - "$URL" <<'EOF'
import json, sys, time, urllib.request
url = sys.argv[1]
deadline = time.time() + 120
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url + "/health", timeout=2) as response:
            body = json.load(response)
        if body.get("service") == "ctf-team-sync":
            print("server is healthy"); sys.exit(0)
        sys.exit(f"{url} is another application (health: {body}); set TEAM_SYNC_HOST_PORT")
    except (OSError, ValueError):
        time.sleep(1)
sys.exit(f"Team Sync did not become healthy at {url}; see: docker compose logs server")
EOF

PUBLIC_URL="${TEAM_SYNC_PUBLIC_URL:-$URL}"
SLUG="$(python3 -c 'import re,sys; s=re.sub(r"[^0-9A-Za-z\u0400-\u04ff]+","-",sys.argv[1]).strip("-").lower(); print(s[:60] or "item")' "$NAME")"
if [ -f ".team-sync/$SLUG/owner.json" ] && command -v uv >/dev/null 2>&1 \
  && uv run team-sync-agent --access-file ".team-sync/$SLUG/owner.json" whoami >/dev/null 2>&1; then
  # Safe to re-run (also by an agent): the existing project is kept, never duplicated.
  echo "project already exists: .team-sync/$SLUG/owner.json"
elif command -v uv >/dev/null 2>&1; then
  uv run team-sync-agent init-project --url "$URL" --public-url "$PUBLIC_URL" \
    --name "$NAME" --owner "$OWNER" --out .team-sync --env-file .env
else
  # No uv on this host (typical server): run the CLI inside the server container.
  docker compose exec -T server sh -c 'rm -rf /tmp/team-sync-init'
  docker compose exec -T server uv run --no-sync team-sync-agent init-project \
    --url http://127.0.0.1:8000 --public-url "$PUBLIC_URL" \
    --name "$NAME" --owner "$OWNER" --out /tmp/team-sync-init
  mkdir -p .team-sync && chmod 700 .team-sync
  docker compose cp server:/tmp/team-sync-init/. .team-sync/
  docker compose exec -T server sh -c 'rm -rf /tmp/team-sync-init'
  chmod -R go-rwx .team-sync
fi
echo
echo "Next: open $URL in a browser, connect with owner.json, and give teammates"
echo "invite.json plus: team-sync-agent --access-file invite.json mcp-config --host <host>"
