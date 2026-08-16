# AInetWatch Homepage — ANWU Rendering Contract & Multilingual Architecture

**Status:** Proposed addendum — architecture only, nothing in this document is implemented yet.
**Scope:** Development directory only (`/Users/q/ainetwatchcom`). Nothing here has been merged into
the production repository (`/Users/q/AInetWatch`); its `docs/ANWU-SPEC.md`, `docs/PRODUCT-SPEC.md`,
and `docs/MASTER-PLAN.md` were read for reference and section-number continuity only and were not
modified.
**Date:** 2026-08-12
**Relationship to production ANWU-SPEC.md:** This document extends production `docs/ANWU-SPEC.md`
§5–§6 (Candidate Object / Candidate Status) and §22–§25 (Human Review Interface / Approved Queue /
Build-Time Publishing). It does not replace or contradict those sections — it defines the layer that
sits *after* candidate review: the Approved Story Record, the homepage rendering contract, and how
both extend cleanly to multiple languages later. Section numbers below (§5, §6, §22, §25, etc.) refer
to production `docs/ANWU-SPEC.md`.

---

## 1. Why this exists

The homepage draft (`index.html` in this directory) is currently hand-authored sample markup. Before
any further visual work, this document establishes the **content model and rendering contract** the
future ANWU build system will use to generate that same markup from human-approved data — without
requiring a redesign later. It also makes the data model multilingual-ready without turning on any
language beyond English at launch.

Nothing here requires building agents, calling AI APIs, or touching Cloudflare/deployment. It is the
schema and the seams between pipeline stages.

---

## 2. Provenance model: three tiers

Every approved story record keeps three tiers of data clearly separate, so the system can always
answer "did a human write this, or did AI propose it, or is this what the source actually said?"

| Tier | Who produced it | Mutable after approval? |
|---|---|---|
| **`source`** | The original outlet | No — factual record of what was published and where |
| **`proposal`** | ANWU (AI) | No — frozen once submitted for review, for audit purposes |
| **`approved`** | Human reviewer | Yes, only via a new review cycle — this is the authoritative publishing record |

The homepage build **only ever reads `approved`** (plus `source.source_url` for the outbound link and
`source.source_name` for the byline). `proposal` exists for audit/comparison but is never rendered
directly — a human always decides what actually reaches `approved`.

---

## 3. Approved Story Schema

Extends the existing Candidate Object (production ANWU-SPEC §5) rather than replacing it. A candidate
becomes an Approved Story Record the moment a human reviewer takes `APPROVE AS-IS` or `EDIT AND
APPROVE` (§4 below).

```json
{
  "story_id": "2026-08-12-openai-astra-delay",

  "source": {
    "source_headline": "OpenAI Holding Off on Releasing 'Astra'...",
    "source_name": "Axios",
    "source_url": "https://www.axios.com/2026/08/07/openai-astra-model-delay-cybersecurity-risks",
    "source_published_at": "2026-08-07T00:00:00Z",
    "discovered_at": "2026-08-07T04:03:00Z"
  },

  "proposal": {
    "proposed_headline": "OpenAI holds back Astra over cyber risk concerns",
    "headline_alternatives": ["...", "..."],
    "summary_bullets": ["...", "...", "..."],
    "categories": ["Security", "Models"],
    "priority_score": 8,
    "confidence_score": 0.9,
    "developing_suggested": true,
    "top_story_suggested": false,
    "image_suggested": false
  },

  "approved": {
    "status": "approved",
    "edited": true,
    "display_headline": "OpenAI Holding Off on Releasing 'Astra': cannot rule out critical cyber capabilities",
    "summary_bullets": ["...", "...", "..."],
    "categories": ["Security", "Models"],
    "priority": 9,
    "developing": true,
    "top_story": true,
    "image": null,
    "image_alt": null,
    "reviewed_by": "human",
    "approved_at": "2026-08-07T09:15:00Z",
    "snooze_until": null
  },

  "language": {
    "source_language": "en",
    "localizations": {}
  }
}
```

Field notes:

- **`status`** (`approved.status`) reuses production ANWU-SPEC §6's candidate status enum
  (`discovered → review → approved → published → archived`, plus `rejected`/`duplicate`) with one
  addition: **`snoozed`** (§4). It is not a second, parallel status machine.
- **`edited`** is the only new bit needed to distinguish "approved as-is" from "edited and approved" —
  deliberately *not* a separate top-level status, per the "don't over-engineer" instruction. Both are
  just `approved.status = "approved"` with `edited: true|false`.
- **`priority`** in `approved` is the human-confirmed ranking value (may differ from
  `proposal.priority_score`, which is frozen AI output).
- **`top_story`** / **`developing`** are booleans a human sets during review, not something the build
  computes — see §6.
- No large HTML fragments are stored anywhere in this record (§7 of your instructions) — only text,
  URLs, and flags. All markup is owned by the template layer.

---

## 4. Human review lifecycle

Reviewer sees the full package together — source article, source name/URL, proposed headline,
proposed summary, proposed categories, priority/confidence, duplicate-group context — and takes
**one** action on the whole package (per your instruction, headline and summary are not approved
separately):

```
REJECT            → status: rejected. Never published. Preserved for audit (§29 of production spec).
SNOOZE            → status: snoozed. Returns to the review queue later; does not publish while snoozed.
APPROVE AS-IS     → status: approved, edited: false. proposal.* copied verbatim into approved.*.
EDIT AND APPROVE  → status: approved, edited: true. Reviewer's edited values become approved.*.
```

State transitions (extends production ANWU-SPEC §6):

```
discovered → review
review     → approved   (as-is or edited — see `edited` flag)
review     → rejected
review     → snoozed
snoozed    → review      (returns for reconsideration; does not auto-approve)
approved   → published
published  → archived
```

`MERGE`/`DUPLICATE`/`ARCHIVE` from production §22 remain valid but are duplicate-cluster operations
(§12–§13 of production spec), not alternatives to the four core actions above — they act on which
candidate becomes canonical *before* the reviewer reaches this four-way decision on the surviving
candidate.

---

## 5. Homepage rendering contract

```
Approved Story Records (data/approved-queue.json)
        ↓
   deterministic build/template step
        ↓
   static index.html  (and future /es/, /fr/, ... — see §8)
```

ANWU agents never write homepage HTML. They only ever produce Approved Story Records; a separate,
deterministic build step is the *only* thing that emits markup. This is what makes rollback and
review straightforward — you can always regenerate the homepage from the approved dataset alone, and
`git` gives you full version history of what was approved and when.

### 5b. Homepage retention rule (locked — do not override in build step)

The homepage renders the **50 most recently approved stories**, ordered by `approved.approved_at`
descending. Stories at positions 51 and beyond roll to `archive.html`.

`approved.priority`, `top_story`, and `developing` govern **rendering prominence and layout only**:

- `top_story: true` → the story gets the `.top-story` section (large headline slot at top)
- `developing: true` → the story gets the `.developing-strip` section (second featured slot)
- `priority` → column placement and ordering within the three-column wire grid

None of these fields affect which 50 stories are selected. Selection is purely by recency
(`approved_at` descending). A high-priority story approved two days ago does not displace a
low-priority story approved one minute ago.

This rule is enforced in `build.py` and may not be relaxed without updating this document.

---

Field → markup mapping (also documented inline in `index.html`'s top comment, and reflected today via
non-functional `data-story-id` / `data-top-story` / `data-developing` attributes on the sample
markup, so the mapping is inspectable in the file itself, not just in this doc):

| Approved record field | Homepage element |
|---|---|
| `approved.top_story` | Selects which single story gets the `.top-story` treatment |
| `approved.developing` | Renders the "Developing" tag and developing-strip slot |
| `approved.headline` | Headline link text |
| `source.name` | Source label |
| `source.url` | Headline `href` (http/https only; otherwise no link) |
| `approved.priority` | Column placement and ordering within the wire grid |
| `approved.category` | Tooltip category tag |
| `approved.public_summary[]` | Tooltip bullets (published verbatim; 3 bullets) |
| `approved.approved_at` | Used for homepage retention sort (50 most recent); not yet displayed |
| `approved.image` / `image_alt` | Optional story image + alt text (reserved; not yet rendered) |

**`editorial_notes` is never read by `build.py`.** It is reviewer-only data and must not
appear in any generated HTML output. `approved` records do not carry `editorial_notes` (enforced
by `server.py`'s `make_approved()`), but `build.py` must not read it even if present.

### 5a. What requires a schema change, and what doesn't

**Presentation-only elements do not require new ANWU schema fields.** The schema exists to define
*content and editorial state* — it is not a constraint on how that content is styled or laid out.

The visual design is free to introduce presentation-only elements — dividers, typography treatments,
spacing, column treatments, emphasis, labels derived from existing fields, responsive behavior, or
other visual hierarchy — without touching the story schema or this document, as long as they're
rendered from data the approved record already carries (or from no story data at all — e.g. a purely
decorative rule between columns).

Add or modify the schema **only** when a visual element depends on new story-specific editorial
data — that is, information that doesn't already exist on the approved record and that a human
reviewer would need to see, set, or approve. For example: a new badge that requires its own reviewer
judgment call (not derivable from `priority`, `developing`, or `top_story`), a byline field, or a
"why this is ranked here" note would all need new fields; a bolder headline treatment for high
`priority` stories, a different column width, or a redesigned tooltip layout would not — those are
just new ways of displaying data the schema already has.

In short: a new *look* for existing data is a template change. A new *fact* about a story is a schema
change. Only the latter needs sign-off here before implementation.

---

## 6. Cypher tooltip generation

The tooltip's static parts (Cypher image, "Here's the summary" heading) are template chrome, not
per-story data. The dynamic parts come straight from the approved record:

- Bullets → `approved.summary_bullets[]` (2–5 items, already validated at the human-review stage per
  production ANWU-SPEC §17)
- Category tags → `approved.categories[]`

Because the reviewer sees proposed headline + proposed summary + categories + the source article
together as one package (§4), by the time a record reaches `approved`, its tooltip content is already
final — the build step only serializes it into HTML. No runtime summarization, no client-side AI call,
ever.

---

## 7. Multilingual data model (English-only at launch)

**Status: implemented at the schema/compatibility level (2026-08-13), in `server.py`'s
`normalize_language()` in the AINWA review console (`Discovery/ainwa-review-v1/`). Every
approved record now carries a real `language` object — v1 just never populates
`localizations`.**

```json
"language": {
  "source_language": "en",
  "localizations": {
    "es": {
      "status": "pending",
      "headline": null,
      "public_summary": null,
      "approved_at": null
    }
  }
}
```

> **Reconciliation note (2026-08-13):** an earlier draft of this section used
> `display_headline`/`summary_bullets`/`image_alt`/`translated_at` and a `status` value of
> `not_started`. The actual implementation uses the simpler shape above (`headline` as a single
> string, `public_summary` as a single string rather than a bullet list, `status: "pending"` as
> the default) to match the exact structure specified when this was built. `image_alt` and a
> `translated_at` timestamp were dropped from the current schema, not because they're wrong,
> but because nothing implemented yet needs them — reintroduce them if/when a real translation
> stage is built and needs that granularity, rather than speculatively carrying unused fields
> now.

Design choices, and why:

- **Only translatable, reader-facing fields are language-keyed** (`headline`, `public_summary`).
  Internal fields — `source_url`, `story_id`, `priority`, category IDs, timestamps, etc. — are
  language-independent and stay at the top level. No duplication, and no translation of anything
  that isn't reader-facing.
- **Category *identifiers* stay canonical** (`Models`, `Security`, etc. — same internal keys in every
  language). Only the *display label* per language is localized, and that lives in a small shared
  label table owned by the template layer (e.g. `{ Security: { en: "Security", es: "Seguridad" } }`),
  not repeated inside every story record. This avoids re-translating the same seven category names on
  every single story.
- **Each localization has its own `status`** (default `"pending"`) and its own `approved_at`,
  independent of the English record's status. This directly supports "translated content may
  need its own review/approval" without assuming it always will.
- English requires no `localizations` entries to function — the object is additive and empty at
  launch (`{}`), so nothing about the current English-only workflow changes. `normalize_language()`
  is deliberately non-blocking: a malformed `language` field on upstream candidate data is logged
  and reset to the safe default rather than ever blocking or corrupting an English approval —
  human-approved English content stays authoritative regardless of what multilingual metadata
  looks like.

### Translation workflow (future, not built yet)

```
English candidate → human editorial approval → authoritative approved English record
        ↓
  (only approved records become translation candidates — rejected/snoozed stories are never
   translated, so no translation effort is wasted)
        ↓
  translation stage → localized content written into language.localizations.<lang>
        ↓
  (optional) per-language human approval → language.localizations.<lang>.status = "approved"
        ↓
  localized static build
```

---

## 8. Future static localized-page strategy (reserved, not implemented)

- Static routes: `/`, `/es/`, `/fr/`, `/de/`, etc. — one static build per language with `status:
  "approved"` localizations available, generated by the same deterministic build step as English, just
  fed `language.localizations.<lang>` instead of the top-level English fields.
- Each language build gets its own canonical URL (e.g. `https://ainetwatch.com/es/`) and `hreflang`
  alternates linking every available language version plus `x-default` pointing at the English page.
- Each language gets its own sitemap entries, generated the same way `sitemap.xml` is today (see
  production HANDOFF doc — sitemap is already hand-maintained/script-generatable, not a database).
- The eventual language selector is a set of plain links between statically generated equivalent
  pages — never a JavaScript/client-side translation control, consistent with §1's zero-runtime-AI
  constraint.
- None of this is implemented now. English remains the only build target; the schema simply doesn't
  need to change shape when this is turned on later.
- **No static builder exists in this project yet** (verified 2026-08-13 — `index.html` and
  `index-live-content.html` are hand-authored, not generated from `data/approved-queue.json` by
  any script). So "the builder must tolerate `language.localizations` without using it" can't
  currently be tested against real code — it's a constraint on whatever builder gets written
  later, not a behavior that exists to verify today. Approved records already have a well-formed
  `language` object regardless (see §7), so a future builder that simply never reads that key
  will satisfy this by construction.

---

## 9. SEO / AEO / GEO considerations

- Nothing about the SEO metadata hardened in the prior pass (title, description, canonical, OG,
  Twitter, Clarity, `WebSite`/`Organization` JSON-LD) changes because of this architecture. It remains
  page-level metadata owned by the template, independent of story records.
- The schema is shaped so **`NewsArticle`** structured data per story could be added later without a
  schema redesign: `approved.display_headline` → `headline`, `source.source_url` → `url` (or a future
  AInetWatch permalink, if individual story pages are ever added), `source.source_published_at` →
  `datePublished`, `approved.summary_bullets` (joined) → `description`, `approved.image`/`image_alt` →
  `image`. Not implemented in this pass, per your instruction — noted only so it isn't blocked later.
- Similarly, an **`ItemList`** for the wire itself could later use `approved.priority` for
  `itemListElement` ordering — the field already exists for the on-page ordering use case, so it would
  be reused, not added.
- Multilingual pages, when built, would each carry their own localized title/description/OG/Twitter
  metadata and `hreflang` — the current single-language metadata approach doesn't need to change until
  that ships.

---

## 10. Simplicity constraints (reaffirmed)

- Flat, version-controlled JSON files (`data/candidate-queue.json`, `data/approved-queue.json`),
  consistent with the pattern already defined in production ANWU-SPEC §4/§23 — no database.
- No microservices, no runtime APIs behind the public homepage, no frontend framework.
- GitHub + Cloudflare static deployment model is unaffected — the build step is still "read JSON,
  emit static HTML," just with a richer, multilingual-ready JSON shape.

---

## 11. Explicitly NOT done in this step

- No agents built, no external AI APIs connected.
- No translation system, no language pages generated, no `hreflang`/localized sitemap entries added.
- No Cloudflare/deployment changes.
- No production files touched — `/Users/q/AInetWatch` was read for reference only.
- No visual redesign of `index.html` — only markup annotations (`data-story-id` etc.), the removal of
  Search/mailing-list per your instruction, and comments.

**2026-08-13 multilingual compatibility pass** — `§7` reconciled with the schema actually
implemented in `server.py`/`normalize_language()`; still explicitly not done: translated routes,
`hreflang`, multilingual sitemap entries, a language selector UI, automatic translation API
calls, a localization review workflow, translated archives, or browser-based translation. The
review console UI was not touched — it remains English-only with no multilingual controls.
