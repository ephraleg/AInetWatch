#!/bin/bash
# AINWA.command — macOS one-click launcher for the AINWA Review Console.
#
# Double-click in Finder (or run in Terminal) to:
#   1. Start the review server (server.py) if not already running
#   2. Wait until the console is reachable, then open it in the default browser
#   3. Leave sourcing under explicit human control in the web interface
#
# API keys: environment takes priority; falls back to macOS Keychain (AINWA_ANTHROPIC_API_KEY).
# Logs: logs/server.log (relative to this file)
# PIDs: logs/server.pid

AINWA_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGS_DIR="$AINWA_DIR/logs"
export AINWA_DATA_DIR="${AINWA_DATA_DIR:-$HOME/DevOps/AINWAdata}"
SERVER_URL="http://127.0.0.1:8765"
SERVER_PID_FILE="$LOGS_DIR/server.pid"
SERVER_LOG="$LOGS_DIR/server.log"

mkdir -p "$LOGS_DIR"

# ---------------------------------------------------------------------------
# Credentials — never printed to stdout or logs
# ---------------------------------------------------------------------------

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  _k=$(security find-generic-password -s "AINWA_ANTHROPIC_API_KEY" -a "$USER" -w 2>/dev/null) || true
  if [[ -n "$_k" ]]; then
    export ANTHROPIC_API_KEY="$_k"
    echo "[AINWA] Loaded ANTHROPIC_API_KEY from Keychain."
  fi
  unset _k
fi

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  _cf_token=$(security find-generic-password -s "AINWA_CLOUDFLARE_API_TOKEN" -a "$USER" -w 2>/dev/null) || true
  [[ -n "$_cf_token" ]] && export CLOUDFLARE_API_TOKEN="$_cf_token" && echo "[AINWA] Loaded Cloudflare API token from Keychain."
  unset _cf_token
fi
if [[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  _cf_account=$(security find-generic-password -s "AINWA_CLOUDFLARE_ACCOUNT_ID" -a "$USER" -w 2>/dev/null) || true
  [[ -n "$_cf_account" ]] && export CLOUDFLARE_ACCOUNT_ID="$_cf_account" && echo "[AINWA] Loaded Cloudflare account ID from Keychain."
  unset _cf_account
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pid_is_alive() {
  # Returns 0 (true) if the PID is running, 1 otherwise.
  kill -0 "$1" 2>/dev/null
}

server_is_running() {
  local pid
  pid=$(cat "$SERVER_PID_FILE" 2>/dev/null) || true
  [[ -n "$pid" ]] && pid_is_alive "$pid"
}

# ---------------------------------------------------------------------------
# 1. Start review server if not already running
# ---------------------------------------------------------------------------

if server_is_running; then
  echo "[AINWA] Review server already running (PID $(cat "$SERVER_PID_FILE"))."
else
  rm -f "$SERVER_PID_FILE"
  echo "[AINWA] Starting review server…"
  python3 "$AINWA_DIR/server.py" --no-open >> "$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
  echo "$SERVER_PID" > "$SERVER_PID_FILE"
  echo "[AINWA] Server started (PID $SERVER_PID). Log → $SERVER_LOG"
fi

# ---------------------------------------------------------------------------
# 2. Wait until the server is reachable (up to 20 s), then open browser
# ---------------------------------------------------------------------------

echo "[AINWA] Waiting for review console to be reachable…"
MAX_WAIT=20
i=0
while [[ $i -lt $MAX_WAIT ]]; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$SERVER_URL" 2>/dev/null) || true
  if [[ "$HTTP_CODE" =~ ^[23] ]]; then
    break
  fi
  sleep 1
  i=$((i + 1))
done

if [[ $i -ge $MAX_WAIT ]]; then
  echo "[AINWA] ERROR: Review console did not respond after ${MAX_WAIT}s." >&2
  echo "[AINWA] Check $SERVER_LOG for details." >&2
  exit 1
fi

echo "[AINWA] Review console ready. Opening browser…"
open "$SERVER_URL"

echo "[AINWA] Done. Use the Source button to start sourcing: $SERVER_URL"
