#!/usr/bin/env bash
# AINWA-010 publish gate
#
# Default (no flags): preflight only — builds, validates, populates dist/.
#   No Cloudflare credentials required. Wrangler is never called.
#
# --deploy: all preflight gates must pass, then deploys dist/ to the Cloudflare Worker.
#   Requires CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in environment.
#
# Usage:
#   bash publish.sh              # preflight only
#   bash publish.sh --deploy     # preflight + deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST="$SCRIPT_DIR/dist"
BUILD="$SCRIPT_DIR/build.py"
INDEX="$REPO_ROOT/index.html"
ARCHIVE="$REPO_ROOT/archive.html"

DEPLOY=0
for arg in "$@"; do
  [ "$arg" = "--deploy" ] && DEPLOY=1
done

# Allowlist: only these filenames may exist in dist/.
# All must be present in the dev repo; none sourced from production.
ALLOWLIST=(
  index.html
  archive.html
  favicon.ico
  favicon-32x32.png
  favicon-16x16.png
  CypherFavicon.jpg
  cypher-transparent-safari.mov
  cypher-transparent-final.webm
)

# ---------------------------------------------------------------------------
# Gate 1: run build.py
# build.py hard-exits non-zero if any approved record is not locked.
# ---------------------------------------------------------------------------
echo "[AINWA] Running build.py..."
python3 "$BUILD"

# ---------------------------------------------------------------------------
# Gate 2: validate generated output files
# ---------------------------------------------------------------------------
for f in "$INDEX" "$ARCHIVE"; do
  if [ ! -f "$f" ] || [ ! -s "$f" ]; then
    echo "[AINWA] ERROR: $f is missing or empty. Aborting." >&2
    exit 1
  fi
  if ! grep -q '<main class="wrap">' "$f"; then
    echo "[AINWA] ERROR: $f is missing expected structure marker. Aborting." >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Gate 3: populate dist/ from allowlist only
# Wipe any prior dist/ contents before populating.
# ---------------------------------------------------------------------------
rm -rf "$DIST"
mkdir -p "$DIST"

cp "$INDEX"   "$DIST/index.html"
cp "$ARCHIVE" "$DIST/archive.html"

for asset in favicon.ico favicon-32x32.png favicon-16x16.png \
             CypherFavicon.jpg cypher-transparent-safari.mov cypher-transparent-final.webm; do
  src="$REPO_ROOT/$asset"
  if [ ! -f "$src" ]; then
    echo "[AINWA] ERROR: required asset missing from dev repo: $asset" >&2
    exit 1
  fi
  cp "$src" "$DIST/$asset"
done

# ---------------------------------------------------------------------------
# Gate 4: enumerate every file in dist/ — reject anything not on the allowlist
# ---------------------------------------------------------------------------
while IFS= read -r -d '' filepath; do
  name="$(basename "$filepath")"
  found=0
  for allowed in "${ALLOWLIST[@]}"; do
    [ "$name" = "$allowed" ] && found=1 && break
  done
  if [ "$found" -eq 0 ]; then
    echo "[AINWA] ERROR: unexpected file in dist/: $name — aborting." >&2
    exit 1
  fi
done < <(find "$DIST" -maxdepth 1 -type f -print0)

file_count="$(find "$DIST" -maxdepth 1 -type f | wc -l | tr -d ' ')"
echo "[AINWA] PREFLIGHT OK — dist/ contains $file_count files, all on allowlist."

if [ "$DEPLOY" -eq 0 ]; then
  echo "[AINWA] Dry run complete. Re-run with --deploy to publish."
  exit 0
fi

# ---------------------------------------------------------------------------
# Gate 5 (deploy path only): verify credentials are set — never print values
# ---------------------------------------------------------------------------
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "[AINWA] ERROR: CLOUDFLARE_API_TOKEN is not set. Aborting." >&2
  exit 1
fi
if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  echo "[AINWA] ERROR: CLOUDFLARE_ACCOUNT_ID is not set. Aborting." >&2
  exit 1
fi

echo "[AINWA] Credentials present. Deploying dist/ to Cloudflare Worker..."
cd "$SCRIPT_DIR"
npx wrangler deploy --config wrangler.jsonc
