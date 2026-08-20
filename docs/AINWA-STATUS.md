# AINWA Status

Handoff file for future Claude Code sessions. Read this first.

## Current state

- **AINWA-001** Discovery registry and sourcing policy — complete
- **AINWA-002** Human review console — complete
- **AINWA-003** Registry-driven ingestion v1 — complete
- **AINWA-004** Normalize, filter, and deduplicate discovery candidates — complete (#82 closed)
- **AINWA-005** Candidate generation (`generate.py`) — complete (#83 closed); awaiting first live run once `ANTHROPIC_API_KEY` is set
- **AINWA-006** `public_summary` / `editorial_notes` separation — complete (#84 closed)
- **AINWA-007** Multilingual schema hook (`normalize_language()`) — complete (#85 closed)
- **AINWA-008** Candidate pipeline → review console field contract — complete (#86 closed)
- **AINWA-009** Static build (`build.py`) — complete (#87 closed)
- **AINWA-010** Publish gate (`publish.sh`) — complete (#88 closed)
- **AINWA-011** End-to-end production readiness — complete (#89 closed)

No deployment has occurred. AINWA v1 implementation sprint is complete.

## Remaining operator actions

1. **First live candidate generation** — set `ANTHROPIC_API_KEY` and run `python3 Discovery/ainwa-review-v1/generate.py`
2. **First live deploy** — after human review and approval, set `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, then run `bash Discovery/ainwa-review-v1/publish.sh --deploy`

## Pipeline

`ingest.py` → `raw-discovery.json` → `filter.py` → `filtered-discovery.json` → `generate.py` → `candidate-queue.json` → human control center → Publish Queue → `approved-queue.json` → `build.py` → `index.html` + `archive.html` → `publish.sh --deploy` → `dist/` → Cloudflare Worker

## Test counts

- Ingestion: 54 passing
- Filter: 53 passing
- Review console: 61 passing
- Candidate generation: 31 passing
- Build: 20 passing
- Publish / trust-chain: 9 passing

## Key implementation facts

- `ainwa-discovery.yml` is the operational discovery source of truth
- 9 high-priority sources have verified operational feeds
- 9 high-priority sources are deferred to deterministic page checks
- Review console actions: Approve As-Is / Edit & Approve / Snooze / Reject
- Approved records are human-approved and locked (`approved.locked == True`)
- `editorial_notes` is reviewer-only and never enters approved records or built HTML
- Grok/Gemini `advisory` is non-blocking and never enters approved records or built HTML
- First real ingestion run: 537 items, 0 errors, 9 skipped
- First real filter run: 537 in → 167 stale dropped → 370 out

## Security boundaries

**Review console:** CSRF protection, URL scheme validation, body-size limit, malformed-data fail-closed behavior, duplicate-ID blocking, loopback-only default.

**Ingestion:** http/https only, DNS/IP checks, private/loopback/link-local/multicast/metadata blocking, redirect revalidation, timeouts, hard 5 MB response cap, untrusted feed content only.

**Build:** unlocked records hard-fail before output; all story text is HTML-escaped; non-http(s) URLs render without links; `editorial_notes` is never read.

**Publish:** `dist/` allowlist enforced; unexpected files abort; `--deploy` is the explicit human gate; credentials are checked only on the deploy path and never printed; deployment target is `dist/` only.

## Homepage retention rule

- Homepage renders the 60 most recently approved stories by `approved.approved_at` descending
- Stories 51 and beyond roll to `archive.html`
- `approved.priority`, `top_story`, and `developing` affect prominence/layout only, not which 60 stories are retained
- Enforced by `build.py`

## Multilingual status

- Architecture is multilingual-ready
- English is the authoritative v1 language
- Translations are deferred until after English human approval
- No translated routes, language selector, hreflang, multilingual sitemap, automatic translation, or runtime translation yet

## Local Graphify context

- Graphify output exists locally under `graphify-out/`
- It is intentionally gitignored
- Use it for orientation only, not as source of truth

## Important constraints

- Work only in `/Users/q/ainetwatchcom`
- `/Users/q/AInetWatch` is production/reference and remains read-only unless explicitly authorized
- Production-path files must not reference `/Users/q/AInetWatch`
- No deployment without explicit operator approval
- **AI prepares. Humans decide.**
- Keep AINWA v1 lean; avoid framework/database/scope creep
