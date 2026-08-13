#!/usr/bin/env bash
set -euo pipefail

OWNER="ephraleg"
REPO="AInetWatch"
PROJECT_NUMBER="5"

echo "== AINWA GitHub Project update =="

command -v gh >/dev/null 2>&1 || { echo "ERROR: gh is required."; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required."; exit 1; }

gh auth status >/dev/null 2>&1 || {
  echo "ERROR: gh is not authenticated."
  exit 1
}

PROJECT_JSON="$(gh project view "$PROJECT_NUMBER" --owner "$OWNER" --format json)"
PROJECT_ID="$(jq -r '.id' <<<"$PROJECT_JSON")"

FIELDS_JSON="$(gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 100 --format json)"

STATUS_FIELD_ID="$(
  jq -r '.fields[] | select(.name=="Status") | .id' <<<"$FIELDS_JSON"
)"

DONE_ID="$(
  jq -r '.fields[] | select(.name=="Status") | .options[] | select(.name=="Done") | .id' <<<"$FIELDS_JSON"
)"

READY_ID="$(
  jq -r '.fields[] | select(.name=="Status") | .options[] | select(.name=="Ready") | .id' <<<"$FIELDS_JSON"
)"

if [[ -z "$STATUS_FIELD_ID" || "$STATUS_FIELD_ID" == "null" ]]; then
  echo "ERROR: Status field not found."
  exit 1
fi

if [[ -z "$DONE_ID" || "$DONE_ID" == "null" || -z "$READY_ID" || "$READY_ID" == "null" ]]; then
  echo "ERROR: Done/Ready status options not found."
  exit 1
fi

find_issue() {
  local title="$1"

  gh issue list \
    --repo "$OWNER/$REPO" \
    --state all \
    --limit 300 \
    --json number,title \
    --jq ".[] | select(.title == $(jq -Rn --arg x "$title" '$x')) | .number" \
    | head -n 1
}

create_issue() {
  local title="$1"
  local body="$2"

  local url

  url="$(
    gh issue create \
      --repo "$OWNER/$REPO" \
      --title "$title" \
      --body "$body"
  )"

  basename "$url"
}

ensure_issue() {
  local title="$1"
  local body="$2"

  local issue_number
  issue_number="$(find_issue "$title")"

  if [[ -n "$issue_number" ]]; then
    echo "Existing issue #$issue_number: $title" >&2
    printf '%s\n' "$issue_number"
  else
    echo "Creating: $title" >&2
    create_issue "$title" "$body"
  fi
}

ensure_project_item() {
  local issue_number="$1"
  local issue_url="https://github.com/$OWNER/$REPO/issues/$issue_number"

  local item_id

  item_id="$(
    gh project item-list "$PROJECT_NUMBER" \
      --owner "$OWNER" \
      --limit 500 \
      --format json \
    | jq -r \
      --arg url "$issue_url" \
      '.items[] | select(.content.url == $url) | .id' \
    | head -n 1
  )"

  if [[ -n "$item_id" && "$item_id" != "null" ]]; then
    printf '%s\n' "$item_id"
  else
    gh project item-add "$PROJECT_NUMBER" \
      --owner "$OWNER" \
      --url "$issue_url" \
      --format json \
      --jq '.id'
  fi
}

set_status() {
  local item_id="$1"
  local status="$2"

  local option_id

  case "$status" in
    Done) option_id="$DONE_ID" ;;
    Ready) option_id="$READY_ID" ;;
    *) echo "ERROR: Unsupported status '$status'"; exit 1 ;;
  esac

  gh project item-edit \
    --id "$item_id" \
    --project-id "$PROJECT_ID" \
    --field-id "$STATUS_FIELD_ID" \
    --single-select-option-id "$option_id" \
    >/dev/null
}

add_item() {
  local title="$1"
  local status="$2"
  local body="$3"

  echo
  echo "$title"

  local issue_number
  issue_number="$(ensure_issue "$title" "$body")"

  local item_id
  item_id="$(ensure_project_item "$issue_number")"

  set_status "$item_id" "$status"

  echo "Issue #$issue_number -> $status"
}

add_item \
"AINWA-001 Finalize discovery registry and sourcing policy" \
"Done" \
"AINWA Discovery V1 complete.

- 60-source operational registry
- registry-first discovery
- High / Medium / Low priorities
- source roles and citation rules
- Discovery Only sources cannot be final citations
- promising leads resolve to Primary Source or Original Reporting where available
- paywalled original reporting remains eligible and is tagged when approved
- ainwa-discovery.yml is the operational source of truth

AI prepares. Humans decide."

add_item \
"AINWA-002 Build and harden human review console" \
"Done" \
"Human approval boundary complete.

Actions:
- Approve As-Is
- Edit & Approve
- Snooze
- Reject

Hardening includes URL validation, CSRF protection, loopback-only default, request-size limits, malformed-data handling, duplicate-ID detection, atomic writes, locked approved records, and 33 passing tests."

add_item \
"AINWA-003 Build registry-driven discovery ingestion" \
"Ready" \
"Build cheap deterministic ingestion from ainwa-discovery.yml.

V1:
- RSS/Atom where available
- deterministic checks for High-priority sources without feeds
- reduced-frequency Medium checks
- daily/overflow Low checks

Security:
- registry/domain allowlisting
- SSRF protection
- http/https only
- block private/local/link-local/metadata targets
- redirect validation
- response-size limits
- timeouts
- content-type validation
- treat fetched content as untrusted data."

add_item \
"AINWA-004 Normalize, filter and deduplicate discovery candidates" \
"Ready" \
"Reduce raw discovery cheaply before AI analysis.

- normalize source, URL, headline and timestamp
- reject stale or malformed items
- canonical URL dedupe
- lightweight headline/event dedupe
- preserve provenance
- identify paywalls
- resolve Discovery Only leads toward Primary Source or Original Reporting."

add_item \
"AINWA-005 Generate top candidate set with Claude and advisory Grok/Gemini review" \
"Ready" \
"Claude selects a maximum of 12 candidates using:
- importance
- timeliness
- novelty
- source quality
- reader relevance
- non-duplication

Claude prepares headline, public Cypher summary, category, priority, Top Story/Developing recommendation, rationale and internal notes.

Send the candidate list, criteria and rationale to Grok and Gemini for advisory review.

Grok/Gemini are non-blocking. Human approval remains mandatory."

add_item \
"AINWA-006 Separate public Cypher summaries from internal editorial notes" \
"Ready" \
"Public Cypher summaries should answer:
1. What happened?
2. Why it matters.
3. What changes or who is affected.

Internal verification notes must remain separate and reviewer-only.

Approved public summaries are locked and published verbatim."

add_item \
"AINWA-007 Add multilingual-ready story schema" \
"Ready" \
"Add lightweight multilingual capability hooks.

Structure:
language:
  source_language: en
  localizations: {}

Rules:
- English remains authoritative in v1
- localization only after English human approval
- only reader-facing fields are localized
- canonical category IDs remain language-independent
- no translated routes, selector, hreflang, multilingual sitemap or automatic translation yet."

add_item \
"AINWA-008 Connect candidate pipeline to review console" \
"Ready" \
"Write the selected AINWA candidate set into the hardened review queue.

Include:
- maximum 12 candidates
- stable unique ID
- original source/headline
- proposed AInetWatch headline
- public summary
- category
- priority
- Top Story / Developing recommendation
- paywall flag
- rationale
- duplicate/corroboration note
- internal editorial notes."

add_item \
"AINWA-009 Build approved queue into static AInetWatch homepage and archive" \
"Ready" \
"Generate static pages only from human-approved locked records.

- approved values are authoritative
- no runtime AI for primary page content
- public Cypher summaries render verbatim
- safe escaping of URLs and text
- malformed approved data fails closed
- preserve lightweight AInetWatch layout."

add_item \
"AINWA-010 Integrate safe GitHub and Cloudflare publishing" \
"Ready" \
"Connect deterministic builds to the existing deployment path.

- least-privilege credentials
- secrets outside repository and logs
- fail closed
- preserve human approval boundary
- publication audit trail
- build validation
- rollback path."

add_item \
"AINWA-011 End-to-end production readiness and security test" \
"Ready" \
"Test complete trust chain:

Discovery
-> ingestion
-> filtering
-> Claude preparation
-> Grok/Gemini advisory review
-> human review
-> locked approval
-> static build
-> publication

Include SSRF, prompt injection, malformed sources, duplicates, paywalls, source resolution, HTML escaping, secrets isolation, failed builds, publication retry and rollback."

echo
echo "AINWA project update complete."
