# AINWA Status

Handoff file for future Claude Code sessions. Read this first — don't rediscover completed work.

## Current state

- **AINWA-001** Discovery registry and sourcing policy — complete
- **AINWA-002** Human review console — complete
- **AINWA-003** Registry-driven ingestion v1 — complete
- **AINWA-004** Normalize, filter, and deduplicate discovery candidates — complete (issue #82 closed)

## Key implementation facts

- `ainwa-discovery.yml` is the operational discovery source of truth
- 9 High-priority sources have verified operational feeds
- 9 High-priority sources are deferred to deterministic page checks
- Review console uses four actions: Approve As-Is / Edit & Approve / Snooze / Reject
- Approved records are human-approved and locked
- Review console hardening tests: 33 passing
- Ingestion tests: 54 passing
- Filter tests: 53 passing
- First real ingestion run: 537 items, 0 errors, 9 skipped
- First real filter run: 537 in → 167 stale dropped → 370 out (0 URL-dedup, 0 title-dedup drops)
- Ingestion does **not**: call Claude/Grok/Gemini, fetch article pages, write the candidate queue, schedule, or publish
- Filter does **not**: call Claude/Grok/Gemini, fetch article pages, write the candidate queue, or publish

## Security boundaries already implemented

**Review console:**
- CSRF protection
- URL scheme validation
- Body-size limit
- Malformed-data fail-closed behavior
- Duplicate-ID blocking
- Loopback-only default

**Ingestion:**
- http/https only
- DNS/IP checks
- Private/loopback/link-local/multicast/metadata blocking
- Redirect revalidation
- Timeouts
- Hard 5 MB response cap
- Untrusted feed content only — no article URL fetching

## Multilingual status

- Architecture is multilingual-ready
- English is the authoritative v1 language
- Translations are deferred until after English human approval
- No translated routes, language selector, hreflang, multilingual sitemap, automatic translation, or runtime translation yet

## Current commits

- `e7d65ed` — AINWA-004 filter v1
- `44db145` — AINWA-003 ingestion v1
- `b6b8f47` — homepage drafts/assets/docs/research workbooks/root gitignore baseline commit

## Local Graphify context

- Graphify output exists locally under `graphify-out/`
- It is intentionally gitignored and should be used for orientation, not treated as source of truth

## Important constraints

- Work only in `/Users/q/ainetwatchcom`
- `/Users/q/AInetWatch` is production/reference and must remain read-only unless explicitly authorized
- No deployment without explicit approval
- AI prepares; humans decide
- Keep AINWA v1 lean; avoid framework/database/scope creep

## Homepage retention rule (locked)

- The homepage renders the **50 most recently approved stories**, ordered by `approved.approved_at` descending.
- Stories at positions 51 and beyond roll to `archive.html`.
- `approved.priority`, `top_story`, and `developing` affect **rendering prominence and layout only** (column placement, top-story slot, developing tag) — they do not affect which 50 stories are selected.
- This rule is enforced by `build.py`. It may not be overridden by the build step.

## Pipeline status

- **AINWA-005** Candidate generation — `generate.py` complete; awaiting live run once ANTHROPIC_API_KEY is set. (Refs #83)
- **AINWA-006** public_summary / editorial_notes separation — complete. Server and console updated. (Refs #84)
- **AINWA-007** Multilingual schema — complete (server.py `normalize_language()`). (Refs #85)
- **AINWA-008** Candidate pipeline → review console — field contract verified; console renders generate.py output. (Refs #86)

Pipeline: ingest.py → raw-discovery.json → filter.py → filtered-discovery.json → generate.py → candidate-queue.json → [human review] → approved-queue.json → build.py → index.html + archive.html

## Test counts (current)

- Ingestion tests: 54 passing
- Filter tests: 53 passing
- Review console tests: 61 passing
- Candidate generation tests: 31 passing
- Build tests: 20 passing
- Publish/trust-chain tests: 9 passing

## Current state (updated)

- **AINWA-009** Static build (`build.py`) — complete (#87 closed)
- **AINWA-010** Publish gate (`publish.sh`) — complete (#88 closed)
- **AINWA-011** End-to-end production readiness — complete (#89 closed)

## Next session

Read this file first. AINWA-001 through AINWA-011 are complete. Pipeline is production-ready pending:
- First live candidate generation run (requires `ANTHROPIC_API_TOKEN`)
- First live deploy (requires `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`; operator runs `bash Discovery/ainwa-review-v1/publish.sh --deploy`)
