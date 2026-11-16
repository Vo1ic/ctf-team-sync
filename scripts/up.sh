#!/usr/bin/env bash
# Everything for the owner in one command; an agent runs it from a one-line prompt.
#   scripts/up.sh "CTF Lviv 2026" Vo1ic   server + project + web + agents on this machine
#   scripts/up.sh --stop "CTF Lviv 2026"  stop this machine's agents (server keeps running)
# Safe to re-run: an existing project, runner config and running runner are reused.
set -euo pipefail
cd "$(dirname "$0")/.."

slug() {
  python3 -c 'import re,sys; s=re.sub(r"[^0-9A-Za-zЀ-ӿ]+","-",sys.argv[1]).strip("-").lower(); print(s[:60] or "item")' "$1"
}

if [ "${1:-}" = "--stop" ]; then
  DIR=".team-sync/$(slug "${2:?usage: scripts/up.sh --stop \"CTF name\"}")"
  uv run team-sync-runner --config "$DIR/runner.toml" --stop
  exit 0
fi

NAME="${1:?usage: scripts/up.sh \"CTF name\" OwnerNickname}"
OWNER="${2:?usage: scripts/up.sh \"CTF name\" OwnerNickname}"
command -v uv >/dev/null 2>&1 || {
  echo "uv is required on this machine (https://docs.astral.sh/uv/)" >&2
  exit 2
}

scripts/quickstart.sh "$NAME" "$OWNER"
DIR=".team-sync/$(slug "$NAME")"
OWNER_FILE="$DIR/owner.json"
CONFIG="$DIR/runner.toml"

echo
if [ ! -f "$CONFIG" ]; then
  uv run team-sync-runner --config "$CONFIG" --init --detect --access-file "$OWNER_FILE" ||
    echo "No agent CLI found: install claude or opencode, then re-run this script."
fi
if [ -f "$CONFIG" ]; then
  uv run team-sync-runner --config "$CONFIG" --check && uv run team-sync-runner --config "$CONFIG" --background
fi

echo
echo "Web (this link logs you in as the owner; do not share it):"
uv run team-sync-agent --access-file "$OWNER_FILE" web-link
echo
echo "Teammates: send them $DIR/invite.json and this prompt for their agent:"
uv run team-sync-agent --access-file "$OWNER_FILE" prompts --id teammate | tail -n +4
echo "All prompts: uv run team-sync-agent --access-file $OWNER_FILE prompts (or the web page «Промпти»)"
