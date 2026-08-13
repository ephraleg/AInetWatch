# AINWA Review Console v1

A deliberately small human-approval interface for AInetWatch.

## What it does

- Reads up to the top 12 active records from `data/candidate-queue.json`.
- Shows source, original headline, proposed AInetWatch headline, summary, category, priority, Top Story, Developing, Claude rationale, and duplicate/corroboration notes together.
- Opens the original source in a separate browser tab.
- Supports four human decisions:
  - **Approve As-Is**
  - **Edit & Approve**
  - **Snooze**
  - **Reject**
- Writes human-approved stories to `data/approved-queue.json`.
- Marks approved editorial fields as `locked: true`; downstream automation should treat them as authoritative and never regenerate them.
- Logs every review action to `data/review-log.json`.

## Run

```bash
cd ainwa-review-v1
python3 server.py
```

Open `http://127.0.0.1:8765` if the browser does not open automatically.

Stop with **Control-C**.

Options:

- `--host` — defaults to `127.0.0.1`. A non-loopback host is refused unless `--allow-remote`
  is also passed (there is still no authentication — see Security below).
- `--port` — defaults to `8765`.
- `--data-dir` — override the data directory (mainly for isolated test runs).
- `--no-open` — don't launch a browser automatically.

## Security (v1.1 hardening, 2026-08-13)

This console still has **no user authentication** and is still meant to run on `127.0.0.1`
only — that hasn't changed. What v1.1 adds is protection against the two concrete risks an
unauthenticated-but-local server actually has:

- **Source links are scheme-validated.** Only `http://`/`https://` source URLs are ever
  rendered as a clickable link (checked both server-side, before a URL can even reach an
  approved record, and again client-side before rendering). A `javascript:` or other
  non-http(s) URL landing in `source.url` — e.g. from a future automated discovery feed —
  can't execute.
- **`POST /api/review` requires a per-server-start CSRF token.** `GET /api/state` returns a
  `csrf_token` that the frontend must echo back in an `X-AINWA-CSRF-Token` header on every
  review action; requests are also rejected unless `Content-Type: application/json`, and any
  `Origin` header present must match this console's own origin. Together this stops another
  browser tab from silently triggering review actions against your local queue while the
  console is open — a real risk for any unauthenticated localhost server, not a hypothetical
  one.
- **`--allow-remote` is required to bind off-loopback**, and doing so prints a prominent
  warning. The server still has no login, so this should only ever be used deliberately, on a
  trusted network, temporarily.
- **POST bodies over 1 MB are rejected** before being read.
- **Malformed `data/*.json` is never silently treated as empty.** If a queue/approved/log file
  exists but fails to parse, the server logs the error to stderr, leaves the file untouched,
  and `/api/state` returns a visible error — the UI shows "Data error — review is blocked"
  instead of "Review queue is clear." Same for duplicate candidate IDs: the console refuses to
  process any review action while `candidate-queue.json` contains two candidates with the same
  `id`, and says so.

## Test with the example candidate

```bash
cp data/candidate-queue.example.json data/candidate-queue.json
python3 server.py
```

`data/approved-queue.example.json` similarly documents the approved-record shape (including the
`language` field) — it's a reference/example file, not something the server reads at startup.

## Keyboard shortcuts

- `A` — Approve As-Is
- `E` — Edit & Approve
- `S` — Snooze
- `R` — Reject
- `←` / `→` — previous / next candidate

## Candidate contract

AINWA can emit either the newer nested structure:

```json
{
  "id": "...",
  "status": "review",
  "rank": 1,
  "original_headline": "...",
  "source": {
    "name": "...",
    "url": "...",
    "role": "Original Reporting",
    "reliability": "High",
    "paywall": false
  },
  "proposal": {
    "headline": "...",
    "summary": ["..."],
    "category": "...",
    "priority": "High",
    "top_story": false,
    "developing": false,
    "why_selected": "...",
    "duplicate_note": "..."
  }
}
```

or the older ANWU-style fields (`primary_source`, `ainetwatch_headline`, `summary`, `priority_score`, etc.). The server normalizes both for v1 compatibility.

## Approved record contract

Approved stories are written in this shape:

```json
{
  "id": "...",
  "source": { "name": "...", "url": "..." },
  "approved": {
    "headline": "...",
    "summary": ["..."],
    "category": "...",
    "priority": "...",
    "top_story": false,
    "developing": false,
    "paywall": false,
    "approved_at": "...",
    "approved_by": "human",
    "locked": true
  },
  "language": {
    "source_language": "en",
    "localizations": {}
  }
}
```

`approved.locked = true` is the publishing boundary. Claude/other agents may not overwrite those fields after human approval.

`language` is added automatically to every approved record — see **Multilingual readiness** below.
`data/approved-queue.example.json` shows this in full, including what a populated localization
entry looks like (illustration only — v1 never writes one).

## Multilingual readiness (v1 compatibility hooks)

The candidate/approved data model is structurally multilingual-ready, so a future translation
stage can be added without a schema migration. As of this pass:

- Every approved record carries a `language` object (`source_language` + `localizations`).
  `source_language` is always `"en"` and `localizations` is always `{}` in v1 — nothing is
  translated, and no language other than English is active.
- Translation can only ever happen **after** an English story is human-approved and locked.
  There is no path for a candidate to be localized before approval, and no path for a
  localization to be generated independently of the approved English content — future
  translations must derive from `approved.headline`/`approved.summary`, never reinterpret the
  source article on their own.
- Only reader-facing fields are ever language-keyed (`headline`, `public_summary` inside a
  `localizations.<lang>` entry). Internal fields — IDs, URLs, priority, category identifiers,
  timestamps — stay at the top level, language-independent, in every language.
- The review console itself is unchanged and English-only: no language selector, no translation
  controls, no multilingual review screens. Reviewing a story still means reviewing its English
  content only.
- No multilingual publishing is active: no translated routes, no `hreflang`, no multilingual
  sitemap entries, no translated archives, no automatic translation API calls, no browser-based
  translation. Those all remain explicitly out of scope until a real translation stage is
  designed and built.

## Intentional v1 limits

- No user authentication/login: this is intended to run locally on `127.0.0.1`. (See
  Security above for what *is* now protected against without adding auth.)
- No database.
- No framework or package installation.
- No live publishing button yet. Approval only moves a story into the Approved Queue.
- Snooze values are stored, but the future ingestion scheduler decides when a snoozed story is reintroduced.
- Reject reasons are deliberately short so they can later inform source/performance tuning.

## Discovery registry: source of truth

`ainwa-discovery.yml` (in this directory) is AINWA's **operational discovery registry** —
the source list, roles, priorities, and discovery policy that the future ingestion stage is
meant to read at runtime. It is not currently read by this review console on purpose: review
and discovery are separate AINWA stages, and the review console should only ever consume
already-created candidates, not couple itself to discovery/ingestion logic.

The `.xlsx` workbooks one directory up (`AINWA_Discovery_Registry_V2.xlsx`,
`Gemini_Discovery_Source_Registry.xlsx`, `Grok_Discovery_Source_Registry.xlsx`,
`Perplexity_Discovery_Source_Registry.xlsx`, `AInetWatch_Sources_and_Reliability_AINWA_Draft.xlsx`)
are **research/design inputs only** — they are not runtime configuration for anything, and
should not be read by any pipeline component. `ainwa-discovery.yml` is what future discovery
code should load.
