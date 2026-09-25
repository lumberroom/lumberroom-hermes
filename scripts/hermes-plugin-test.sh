#!/usr/bin/env bash
# Proves the lumberroom memory provider for Hermes Agent against real engines, through Hermes's own
# plugin loader, MemoryManager and CLI: token mode first, then AUTH_MODE=oauth.
#
#   POSTGRES_PASSWORD=... ./scripts/hermes-plugin-test.sh
#   ./scripts/hermes-plugin-test.sh --hermes-src ~/work/open-source/hermes-agent --keep
#   ./scripts/hermes-plugin-test.sh --capture     rewrite the tools snapshot and the test transcript, then exit
#
# Two scratch servers, never 8787: token mode on 8796 against lumberroom_hermes_plugin_test, OAuth
# on 8797 against lumberroom_hermes_plugin_oauth_test. Both databases drop on exit unless --keep.
# The plugin venv is .venv-hermes-plugin, holding Hermes installed from --hermes-src.
#
# What this does NOT prove: that a model calls memory_write. Every step drives Hermes's
# MemoryManager, the object a real turn calls, with no model in the loop, because a model turn
# bills a provider and would measure the model. HERMES_PLUGIN_LIVE_TURN=1 adds one `hermes -z`
# turn at the end for the owner to run by hand; it needs a configured model provider and costs money.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_SRC="${HERMES_AGENT_SRC:-$HOME/work/open-source/hermes-agent}"
TOKEN_PORT=8796
OAUTH_PORT=8797
KEEP=0
CAPTURE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --hermes-src) HERMES_SRC="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --capture) CAPTURE=1; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1. See --help." >&2; exit 1 ;;
  esac
done

for bin in docker curl openssl; do
  command -v "$bin" >/dev/null 2>&1 || { echo "$bin is required" >&2; exit 1; }
done
PYTHON="${HERMES_PLUGIN_PYTHON:-python3.14}"
VENV="${HERMES_PLUGIN_VENV:-$REPO_DIR/.venv-hermes-plugin}"
PY="$VENV/bin/python"
HERMES="$VENV/bin/hermes"
DRIVE="$REPO_DIR/scripts/lib/hermes_plugin_drive.py"
PLUGIN_DIR="$REPO_DIR/client/hermes"
WORK="$(mktemp -d)"
NONCE="$(openssl rand -hex 4)"

FAILED=0
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILED=1; }
die() { fail "$*"; printf '\nhermes-plugin-test FAILED\n'; exit 1; }

ensure_venv() {
  if [ ! -x "$PY" ]; then
    [ -d "$HERMES_SRC" ] || die "no Hermes checkout at $HERMES_SRC (pass --hermes-src)"
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/pip" install --quiet "$HERMES_SRC[mcp]"
    "$VENV/bin/pip" install --quiet "mcp>=2.0.0,<3" "httpx2>=2.7.0,<3" "filelock>=3.12,<4" \
      "pytest==9.1.1" "pytest-asyncio==1.3.0"
  fi
  "$PY" -c "import agent.memory_provider, plugins.memory, mcp, httpx2, filelock" \
    || die "the plugin venv at $VENV cannot import Hermes or the plugin's dependencies"
  pass "plugin venv ready (Hermes at $(git -C "$HERMES_SRC" rev-parse --short HEAD 2>/dev/null || echo unknown))"
}

psql_q() {
  docker compose -f "$REPO_DIR/docker-compose.yml" exec -T db \
    psql -U "${POSTGRES_USER:-lumberroom}" -d "$1" -tAc "$2"
}

make_home() {
  # make_home DIR BASE_URL AUTH
  mkdir -p "$1/plugins" "$1/memories"
  ln -s "$PLUGIN_DIR" "$1/plugins/lumberroom"
  cat >"$1/config.yaml" <<YAML
memory:
  provider: lumberroom
  memory_enabled: false
  user_profile_enabled: false
  nudge_interval: 0
  lumberroom:
    base_url: $2
    auth: $3
YAML
}

wait_ready() {
  local i=0
  until curl -sf "$1/readyz" >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -ge 90 ] && return 1; sleep 2
  done
}

drive() { HERMES_HOME="$1" "$PY" "$DRIVE" "${@:2}"; }

trap 'status=$?; scratch_stop 2>/dev/null || true; rm -rf "$WORK"; exit $status' EXIT INT TERM

say "1/13 plugin venv"
ensure_venv

say "2/13 scratch engine in token mode"
TOKEN="$(openssl rand -hex 32)"
SCRATCH_DB=lumberroom_hermes_plugin_test
SCRATCH_NAME="${LUMBERROOM_HERMES_PLUGIN_TEST_SERVER:-lumberroom-hermes-plugin-test-server}"
SCRATCH_PORT=$TOKEN_PORT
SCRATCH_KEEP=$KEEP
SCRATCH_TOKENS="[{\"client\":\"hermes-plugin-test\",\"token\":\"$TOKEN\",\"read\":[{\"namespace\":\"*\",\"max\":\"private\"}],\"write\":[\"user:me\",\"global\",\"project:*\"],\"mayIngest\":true,\"mayDelete\":true}]"
export SCRATCH_DB SCRATCH_NAME SCRATCH_PORT SCRATCH_KEEP SCRATCH_TOKENS
# shellcheck source=lib/scratch-server.sh
. "$REPO_DIR/scripts/lib/scratch-server.sh"
scratch_start || die "the token-mode scratch engine did not start"
URL="$SCRATCH_URL"
pass "engine ready at $URL"

if [ "$CAPTURE" -eq 1 ]; then
  "$PY" "$REPO_DIR/scripts/lib/hermes_plugin_capture.py" --url "$URL" --token "$TOKEN" \
    --snapshot "$PLUGIN_DIR/tools_snapshot.json" \
    --transcript "$PLUGIN_DIR/tests/fixtures/engine_transcript.json"
  exit 0
fi

say "3/13 the checked-in tools snapshot matches the live engine"
"$PY" "$REPO_DIR/scripts/lib/hermes_plugin_capture.py" --url "$URL" --token "$TOKEN" \
  --snapshot "$WORK/snapshot.json" --transcript "$WORK/transcript.json" >/dev/null
if "$PY" - "$PLUGIN_DIR/tools_snapshot.json" "$WORK/snapshot.json" <<'PY'
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
key = lambda s: (s["instructions"], sorted(json.dumps(t, sort_keys=True) for t in s["tools"]))
sys.exit(0 if key(a) == key(b) else 1)
PY
then pass "snapshot matches"; else fail "snapshot drifted from the engine: rerun with --capture and review the diff"; fi

say "4/13 a throwaway HERMES_HOME with the plugin symlinked in"
HOME_T="$WORK/home-token"
make_home "$HOME_T" "$URL" token
printf 'LUMBERROOM_HERMES_TOKEN=%s\n' "$TOKEN" >"$HOME_T/.env"
chmod 600 "$HOME_T/.env"
pass "profile at $HOME_T"

say "5/13 hermes lumberroom status reaches the engine"
if HERMES_HOME="$HOME_T" "$HERMES" lumberroom status >"$WORK/status.out" 2>&1 \
   && grep -q memory_write "$WORK/status.out"; then
  pass "status lists memory_write"
else
  fail "status failed: $(tail -5 "$WORK/status.out")"
fi

say "6/13 a nonce written through MemoryManager.handle_tool_call"
drive "$HOME_T" write --nonce "$NONCE" --session "hpt-a-$NONCE" >"$WORK/write.out" 2>&1 \
  && pass "write accepted: $(cat "$WORK/write.out")" || fail "write failed: $(cat "$WORK/write.out")"

say "7/13 the engine restarts, and a fresh session recalls the nonce through prefetch_all"
docker restart "$SCRATCH_NAME" >/dev/null && wait_ready "$URL" || die "the engine did not come back after a restart"
drive "$HOME_T" recall --nonce "$NONCE" --session "hpt-b-$NONCE" >"$WORK/recall.out" 2>&1 \
  && pass "the <memory-context> block carries HERMESLARK-$NONCE" \
  || fail "recall missed the nonce: $(head -c 600 "$WORK/recall.out")"

say "8/13 tool_calls: the write counts as the model's, recall as the hook's, each with its session"
ROWS="$(psql_q "$SCRATCH_DB" "SELECT tool || ':' || coalesce(unprompted::text, 'null') || ':' || coalesce(session_id, '') FROM tool_calls WHERE client = 'hermes-plugin-test'")"
for want in "memory_write:true:hpt-a-$NONCE" "memory_search:false:hpt-b-$NONCE" "context_bootstrap:false:hpt-b-$NONCE"; do
  printf '%s\n' "$ROWS" | grep -qx "$want" && pass "row $want" || fail "no row $want in: $(printf '%s' "$ROWS" | tr '\n' ' ')"
done

say "9/13 a gateway stranger gets nothing, and nothing reaches the engine"
drive "$HOME_T" gated --nonce "$NONCE" --session "hpt-g-$NONCE" >"$WORK/gated.out" 2>&1 \
  && pass "gated session refused" || fail "gate leaked: $(cat "$WORK/gated.out")"
N="$(psql_q "$SCRATCH_DB" "SELECT count(*) FROM tool_calls WHERE session_id = 'hpt-g-$NONCE'")"
[ "$N" = 0 ] && pass "no tool call recorded for the gated session" || fail "$N tool calls reached the engine from a gated session"

say "10/13 import-builtin fills the queue and never the store, and a rerun adds nothing"
printf 'The gate import fact IMPORTLARK-%s one.\n§\nThe gate import fact IMPORTLARK-%s two.\n' "$NONCE" "$NONCE" >"$HOME_T/memories/MEMORY.md"
printf 'The owner gate import fact IMPORTLARK-%s three.\n' "$NONCE" >"$HOME_T/memories/USER.md"
HERMES_HOME="$HOME_T" "$HERMES" lumberroom import-builtin >"$WORK/import.out" 2>&1 || fail "import-builtin failed: $(cat "$WORK/import.out")"
HERMES_HOME="$HOME_T" "$HERMES" lumberroom import-builtin >>"$WORK/import.out" 2>&1 || fail "the second import-builtin failed"
P="$(psql_q "$SCRATCH_DB" "SELECT count(*) FROM ingest_proposal WHERE content LIKE '%IMPORTLARK-$NONCE%' AND speaker = 'main_model'")"
M="$(psql_q "$SCRATCH_DB" "SELECT count(*) FROM memory WHERE content LIKE '%IMPORTLARK-$NONCE%'")"
[ "$P" = 3 ] && pass "three proposals, speaker main_model" || fail "expected 3 proposals, found $P"
[ "$M" = 0 ] && pass "no live memory holds an imported entry" || fail "$M imported entries reached the live store"

say "11/13 the wheel carries the package and loads through the entry point"
"$VENV/bin/pip" wheel --quiet --no-deps -w "$WORK/dist" "$PLUGIN_DIR"
WHEEL="$(ls "$WORK"/dist/lumberroom_hermes-*.whl)"
"$PY" -m zipfile -l "$WHEEL" >"$WORK/wheel.txt"
if grep -q 'lumberroom_hermes/__init__.py' "$WORK/wheel.txt" && grep -q 'lumberroom_hermes/cli.py' "$WORK/wheel.txt" \
   && grep -q 'lumberroom_hermes/tools_snapshot.json' "$WORK/wheel.txt" && ! grep -q 'tests/' "$WORK/wheel.txt"; then
  pass "wheel layout"
else
  fail "wheel layout: $(cat "$WORK/wheel.txt")"
fi
"$VENV/bin/pip" install --quiet --no-deps --target "$WORK/wheelsite" "$WHEEL"
HOME_E="$WORK/home-entry"
make_home "$HOME_E" "$URL" token
rm "$HOME_E/plugins/lumberroom"
cp "$HOME_T/.env" "$HOME_E/.env"
PYTHONPATH="$WORK/wheelsite" drive "$HOME_E" entry-point >"$WORK/ep.out" 2>&1 \
  && pass "$(cat "$WORK/ep.out")" || fail "entry point: $(cat "$WORK/ep.out")"
scratch_stop

say "12/13 scratch engine in AUTH_MODE=oauth; login through the CLI's paste path"
PASSWORD="$(openssl rand -hex 20)"
SCRATCH_DB=lumberroom_hermes_plugin_oauth_test
SCRATCH_NAME="${LUMBERROOM_HERMES_PLUGIN_OAUTH_SERVER:-lumberroom-hermes-plugin-oauth-server}"
SCRATCH_PORT=$OAUTH_PORT
SCRATCH_TOKENS='[]'
export SCRATCH_DB SCRATCH_NAME SCRATCH_PORT SCRATCH_TOKENS
# Copied from scripts/oauth-flow-test.sh scratch_start_oauth, with one addition: a 75-second access
# token, so step 13 can reach the refresh window without waiting an hour.
scratch_start_oauth() {
  SCRATCH_REPO_DIR="${SCRATCH_REPO_DIR:-$REPO_DIR}"
  SCRATCH_NETWORK="${LUMBERROOM_DOCKER_NETWORK:-lumberroom_default}"
  SCRATCH_PG_USER="${POSTGRES_USER:-lumberroom}"
  scratch_require || return 1
  scratch_compose up -d db >/dev/null
  scratch_compose exec -T -e PGOPTIONS="-c client_min_messages=warning" db \
    psql -U "$SCRATCH_PG_USER" -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null
  scratch_compose exec -T db psql -U "$SCRATCH_PG_USER" -d postgres -c "CREATE DATABASE $SCRATCH_DB" >/dev/null
  local hash
  hash="$(printf '%s\n' "$PASSWORD" | docker run --rm -i lumberroom-server:0.4.0 lumberroom-server hash-password)" || return 1
  docker rm -f "$SCRATCH_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$SCRATCH_NAME" --network "$SCRATCH_NETWORK" \
    -p "127.0.0.1:${SCRATCH_PORT}:${SCRATCH_PORT}" \
    -e PORT="$SCRATCH_PORT" -e HOST=0.0.0.0 -e TENANT_ID=scratch \
    -e DATABASE_URL="postgres://${SCRATCH_PG_USER}:${POSTGRES_PASSWORD}@db:5432/${SCRATCH_DB}" \
    -e PUBLIC_URL="http://127.0.0.1:${SCRATCH_PORT}" \
    -e AUTH_MODE=oauth -e OWNER_PASSWORD_HASH="$hash" -e OAUTH_COOKIE_SECRET="$(openssl rand -hex 32)" \
    -e OAUTH_ACCESS_TTL_SECS=75 \
    -e EMBED_PROVIDER=hash -e EMBED_DIM=768 -e KEK_PROVIDER=none \
    lumberroom-server:0.4.0 >/dev/null
  SCRATCH_URL="http://127.0.0.1:${SCRATCH_PORT}"
  wait_ready "$SCRATCH_URL"
}
scratch_start_oauth || die "the OAuth scratch engine did not start"
URL="$SCRATCH_URL"
HOME_O="$WORK/home-oauth"
make_home "$HOME_O" "$URL" oauth

mkfifo "$WORK/paste"
HERMES_HOME="$HOME_O" "$HERMES" lumberroom login --no-browser <"$WORK/paste" >"$WORK/login.out" 2>&1 &
LOGIN_PID=$!
exec 7>"$WORK/paste"
AUTH_URL=""
for _ in $(seq 1 30); do
  AUTH_URL="$(grep -o "http://127.0.0.1:${OAUTH_PORT}/oauth/authorize?[^[:space:]]*" "$WORK/login.out" | head -1 || true)"
  [ -n "$AUTH_URL" ] && break
  sleep 1
done
[ -n "$AUTH_URL" ] || die "login printed no authorize URL: $(cat "$WORK/login.out")"
param() { "$PY" -c 'import sys, urllib.parse as u; print(u.parse_qs(u.urlsplit(sys.argv[1]).query).get(sys.argv[2], [""])[0])' "$AUTH_URL" "$1"; }
FORM=()
for k in client_id redirect_uri code_challenge code_challenge_method response_type state resource scope; do
  v="$(param "$k")"; [ -n "$v" ] && FORM+=(--data-urlencode "$k=$v")
done
curl -sS -o "$WORK/login.html" -D "$WORK/login.h" -X POST "$URL/oauth/login" "${FORM[@]}" --data-urlencode "password=$PASSWORD"
COOKIE="$(sed -n 's/^[Ss]et-[Cc]ookie: \(lumberroom_owner=[^;]*\).*/\1/p' "$WORK/login.h" | head -1)"
CSRF="$(grep -o 'name="csrf" value="[^"]*"' "$WORK/login.html" | sed 's/.*value="//; s/"$//' || true)"
if [ -z "$CSRF" ]; then
  curl -sS -o "$WORK/consent.html" -H "Cookie: $COOKIE" "$AUTH_URL"
  CSRF="$(grep -o 'name="csrf" value="[^"]*"' "$WORK/consent.html" | sed 's/.*value="//; s/"$//' || true)"
fi
[ -n "$CSRF" ] || die "no consent screen after owner login"
curl -sS -o /dev/null -D "$WORK/consent.h" -X POST "$URL/oauth/consent" -H "Cookie: $COOKIE" \
  "${FORM[@]}" --data-urlencode "csrf=$CSRF" --data-urlencode "profile=full" --data-urlencode "action=allow"
LOCATION="$(sed -n 's/^[Ll]ocation: \(.*\)\r$/\1/p' "$WORK/consent.h" | head -1)"
[ -n "$LOCATION" ] || die "consent returned no redirect"
printf '%s\n' "$LOCATION" >&7
exec 7>&-
if wait "$LOGIN_PID"; then pass "hermes lumberroom login stored a token pair"; else fail "login failed: $(tail -5 "$WORK/login.out")"; fi
HERMES_HOME="$HOME_O" "$HERMES" lumberroom status >"$WORK/ostatus.out" 2>&1 && grep -q memory_write "$WORK/ostatus.out" \
  && pass "status over OAuth lists memory_write" || fail "status over OAuth: $(tail -5 "$WORK/ostatus.out")"
drive "$HOME_O" write --nonce "$NONCE" --session "hpt-oa-$NONCE" >"$WORK/owrite.out" 2>&1 \
  && pass "OAuth write accepted" || fail "OAuth write: $(cat "$WORK/owrite.out")"
drive "$HOME_O" recall --nonce "$NONCE" --session "hpt-ob-$NONCE" >"$WORK/orecall.out" 2>&1 \
  && pass "OAuth recall carries the nonce" || fail "OAuth recall: $(head -c 600 "$WORK/orecall.out")"

say "13/13 two processes inside the refresh window refresh once, and nobody is logged out"
sleep 20
( drive "$HOME_O" recall --nonce "$NONCE" --session "hpt-r1-$NONCE" >"$WORK/r1.out" 2>&1; echo $? >"$WORK/r1.rc" ) &
( drive "$HOME_O" recall --nonce "$NONCE" --session "hpt-r2-$NONCE" >"$WORK/r2.out" 2>&1; echo $? >"$WORK/r2.rc" ) &
wait
[ "$(cat "$WORK/r1.rc")" = 0 ] && [ "$(cat "$WORK/r2.rc")" = 0 ] \
  && pass "both processes recalled after the refresh" || fail "a process failed across the refresh: $(cat "$WORK/r1.out" "$WORK/r2.out" | head -c 800)"
REPLAYS="$(docker logs "$SCRATCH_NAME" 2>&1 | grep -c 'refresh token replayed' || true)"
[ "$REPLAYS" = 0 ] && pass "no refresh token was replayed" || fail "the engine saw $REPLAYS refresh replays and revoked the family"

if [ "${HERMES_PLUGIN_LIVE_TURN:-0}" = 1 ]; then
  say "live turn (owner opt-in; bills the model provider in this shell's environment)"
  HERMES_HOME="$HOME_O" "$HERMES" -z "What is the Hermes plugin gate nickname? Answer with the nickname only." \
    >"$WORK/live.out" 2>&1 || true
  grep -q "HERMESLARK-$NONCE" "$WORK/live.out" && pass "a real turn answered from memory" || fail "live turn: $(tail -5 "$WORK/live.out")"
fi

printf '\n'
if [ "$FAILED" -eq 0 ]; then printf 'hermes-plugin-test PASSED\n'; else printf 'hermes-plugin-test FAILED\n'; exit 1; fi
