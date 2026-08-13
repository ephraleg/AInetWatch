# AINWA Status

Handoff file for future Claude Code sessions. Read this first — don't rediscover completed work.

## Current state

- **AINWA-001** Discovery registry and sourcing policy — complete
- **AINWA-002** Human review console — complete
- **AINWA-003** Registry-driven ingestion v1 — complete
- **Next issue: #82** — normalize, filter, and deduplicate discovery candidates

## Key implementation facts

- `ainwa-discovery.yml` is the operational discovery source of truth
- 9 High-priority sources have verified operational feeds
- 9 High-priority sources are deferred to deterministic page checks
- Review console uses four actions: Approve As-Is / Edit & Approve / Snooze / Reject
- Approved records are human-approved and locked
- Review console hardening tests: 33 passing
- Ingestion tests: 54 passing
- First real ingestion run: 537 items, 0 errors, 9 skipped
- Ingestion does **not**: call Claude/Grok/Gemini, fetch article pages, write the candidate queue, schedule, or publish

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

## Next session

Start by reading this file, checking the existing Graphify graph, and reviewing GitHub issue #82. Do not redo completed discovery/review/ingestion work.
