#!/bin/bash
# AINWA.command — macOS one-click launcher for the AINWA Review Console.
#
# Double-click in Finder (or run in Terminal) to:
#   1. Start the review server (server.py) if not already running
#   2. Wait until the console is reachable, then open it in the default browser
#   3. Run the sourcing pipeline (ingest → filter → generate) in the background,
#      if ANTHROPIC_API_KEY is set and no sourcing run is already in progress
#
# Server and sourcing run independently; killing one does not affect the other.
# API keys: environment takes priority; falls back to macOS Keychain (AINWA_ANTHROPIC_API_KEY).
# Logs: logs/server.log  logs/sourcing.log  (relative to this file)
# PIDs: logs/server.pid  logs/sourcing.pid

AINWA_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGS_DIR="$AINWA_DIR/logs"
SERVER_URL="http://127.0.0.1:8765"
SERVER_PID_FILE="$LOGS_DIR/server.pid"
SOURCING_PID_FILE="$LOGS_DIR/sourcing.pid"
SERVER_LOG="$LOGS_DIR/server.log"
SOURCING_LOG="$LOGS_DIR/sourcing.log"

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

sourcing_is_running() {
  local pid
  pid=$(cat "$SOURCING_PID_FILE" 2>/dev/null) || true
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

# ---------------------------------------------------------------------------
# 3. Start sourcing pipeline if not already running
# ---------------------------------------------------------------------------

if sourcing_is_running; then
  echo "[AINWA] Sourcing already in progress (PID $(cat "$SOURCING_PID_FILE")). Skipping."
elif [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "[AINWA] WARNING: ANTHROPIC_API_KEY is not set — sourcing pipeline will not run." >&2
  echo "[AINWA] The review console is open and fully usable without sourcing." >&2
else
  rm -f "$SOURCING_PID_FILE"
  echo "[AINWA] Starting sourcing pipeline in background… Log → $SOURCING_LOG"
  (
    trap 'rm -f "$SOURCING_PID_FILE"' EXIT
    {
      echo "=== AINWA sourcing started $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
      python3 "$AINWA_DIR/ingest.py"   && \
      python3 "$AINWA_DIR/filter.py"   && \
      python3 "$AINWA_DIR/generate.py" && \
      echo "=== Sourcing complete $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===" || \
      echo "=== Sourcing FAILED $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
    } >> "$SOURCING_LOG" 2>&1
  ) &
  SOURCING_PID=$!
  echo "$SOURCING_PID" > "$SOURCING_PID_FILE"
  echo "[AINWA] Sourcing started (PID $SOURCING_PID)."
fi

echo "[AINWA] Done. Review console: $SERVER_URL"
