# AINetWatch.com — Redesign & Growth Master Plan

**Version:** 1.0  
**Date:** August 11, 2026  
**Status:** Planning  
**Primary objective:** Increase qualified readership, repeat visits, and outbound clicks to original reporting.

---

## 1. Vision

AINetWatch is a **high-density AI news intelligence wire** designed to help readers:

1. Discover important AI developments quickly.
2. Understand why a story matters before clicking.
3. Reach the original reporting with one click.
4. Return regularly for a continuously updated AI news stream.

### Product philosophy

- Minimalist
- Fast
- Highly scannable
- High information density
- Original-source attribution
- No unnecessary graphics
- No news-card/grid design
- Mobile-first
- Low/no operating cost
- Human editorial control over publication

**Core principle:** ANWU automates the work; humans control the editorial product; the homepage remains a fast, minimalist wire.

---

# 2. Primary KPIs

Track these from the beginning:

- Daily unique visitors
- Returning visitors
- Stories viewed per visitor
- Outbound click-through rate
- Outbound clicks per visitor
- Time to first outbound click
- Tooltip engagement rate
- Search usage
- Email subscribers
- RSS usage
- Search-driven traffic
- Social-driven traffic

Avoid optimizing for pageviews alone.

---

# 3. Phase 0 — Foundation, Indexing & Measurement

**Priority: P0 — Do before major redesign work.**

## Search and indexing

- [ ] Verify Google Search Console
- [ ] Verify Bing Webmaster Tools
- [ ] Verify `robots.txt`
- [ ] Generate `sitemap.xml`
- [ ] Submit sitemap to search engines
- [ ] Verify no accidental `noindex`
- [ ] Verify crawler accessibility
- [ ] Establish canonical URLs
- [ ] Establish clean URL structure
- [ ] Monitor indexed pages
- [ ] Monitor crawl errors

## Technical SEO

- [ ] Unique page titles
- [ ] Meta descriptions
- [ ] Open Graph metadata
- [ ] X/Twitter metadata
- [ ] `WebSite` structured data
- [ ] `Organization` structured data
- [ ] Appropriate article/news structured data
- [ ] Canonical tags
- [ ] XML sitemap
- [ ] Semantic HTML
- [ ] Internal linking strategy

## Analytics

- [ ] Establish baseline traffic measurements
- [ ] Configure visitor/session analytics
- [ ] Configure outbound-click tracking
- [ ] Configure tooltip engagement tracking
- [ ] Configure search tracking
- [ ] Configure email signup tracking
- [ ] Configure social referral tracking
- [ ] Configure Microsoft Clarity
- [ ] Establish baseline report

## Editorial foundation

Create an **Editorial Standards** page covering:

- AINetWatch's role as an aggregator
- Source selection
- Headline treatment
- Attribution
- AI-assisted processing
- Human approval
- Duplicate handling
- Corrections
- Updates
- Advertising/sponsorship disclosure

## Corrections

- [ ] Define correction workflow
- [ ] Add correction/update fields to story data
- [ ] Define how corrected stories are displayed

---

# 4. Phase 1 — Visual & Structural Cleanup

**Priority: P0/P1**

## Homepage

- [ ] Polish masthead
- [ ] Improve logo/tagline treatment
- [ ] Improve typography
- [ ] Improve line spacing
- [ ] Improve column gutters
- [ ] Improve horizontal spacing
- [ ] Improve story separation
- [ ] Improve source attribution
- [ ] Improve headline hierarchy
- [ ] Preserve minimalist wire format

**Design rule:** More information per screen, not more decoration per screen.

## Mobile

- [ ] Responsive layout
- [ ] Increase base font size where necessary
- [ ] Increase tap targets
- [ ] Reduce/stack columns at narrow breakpoints
- [ ] Eliminate horizontal scrolling
- [ ] Define mobile tooltip interaction

## Branding

- [ ] Add Cypher favicon
- [ ] Use consistent Cypher identity
- [ ] Use favicon consistently across browser, social and email
- [ ] Use Cypher in tooltip treatment

## Dark mode

- [ ] Add system-aware dark mode
- [ ] Test contrast and readability

## Advertising architecture

Reserve, but do not initially activate:

1. Small top banner
2. Mid-page placement
3. Lower-page placement

Ads must not interfere with headline scanning or outbound CTR.

---

# 5. Phase 2 — Content Structure & Reader Utility

**Priority: P1**

## Categories

Primary categories:

- Models
- Research
- Security
- Governance
- Business
- Infrastructure
- Applications

Allow secondary categories internally.

Use subtle category text rather than large badges.

Preferred pattern:

> **Headline**  
> Source · Category

## Tooltip summaries

### Desktop

Hover over headline → preview.

### Mobile

Tap summary indicator → preview.

### Tooltip format

```text
[Cypher]

HERE'S THE SUMMARY

• What happened
• Why it matters
• Who/what is affected
• Important context

Category
```

Rules:

- Normally 2–4 bullets
- Maximum 5
- Bullets should be short phrases or sentences
- Summary should help readers decide whether to click
- Summary should not replace the original article

## Headline fidelity

Each story is classified as:

- Source headline
- Edited AINetWatch headline

Edited headlines must not introduce unsupported facts, new claims, false certainty, unsubstantiated causality, or sensational conclusions.

---

# 6. Phase 3 — Archive, Search & RSS

**Priority: P1**

## Archive

Create a permanent archive with:

- Date navigation
- Monthly navigation
- Recent stories
- Category filtering
- Search

The homepage remains fast-moving; the archive retains published stories.

## Search

Implement lightweight static search using a generated search index.

Search/filter by:

- Keyword
- Date
- Source
- Category

No database required initially.

## RSS

- [ ] Generate RSS automatically
- [ ] Include approved stories
- [ ] Validate feed
- [ ] Add RSS discovery metadata

---

# 7. Phase 4 — Topic Hubs & SEO Growth

**Priority: P1/P2**

Create crawlable topic pages:

- `/models/`
- `/research/`
- `/security/`
- `/governance/`
- `/business/`
- `/infrastructure/`
- `/applications/`

Topic pages should contain current, recent and historical stories, category descriptions, internal links, and related topics.

Develop evergreen pages around recurring subjects such as:

- AI models
- AI security
- AI regulation
- AI governance
- AI infrastructure
- Frontier AI
- AI companies

The homepage remains minimalist; SEO depth lives primarily in archive and topic structures.

---

# 8. Phase 5 — Audience Development

**Priority: P1/P2**

## Email

### Morning AI NetWatch

Concise daily briefing.

### Afternoon AI NetWatch

“What's Changed” update focused on significant developments since the morning edition.

Tasks:

- [ ] Select email provider
- [ ] Create signup mechanism
- [ ] Create morning template
- [ ] Create afternoon template
- [ ] Track opens/clicks/subscriptions

## Social

### Tier 1

- X
- LinkedIn

### Tier 2

- Bluesky
- TikTok

### Later

- Instagram
- Facebook
- YouTube

Use Cypher consistently as profile/avatar branding.

## Social content format

```text
HEADLINE

Brief explanation.

Why it matters: one sentence.

→ Original source
```

---

# 9. Phase 6 — ANWU v1

**Priority: P2**

ANWU is the editorial automation layer.

## Core workflow

```text
Discovery
    ↓
Candidate Queue
    ↓
Normalize
    ↓
Categorize
    ↓
Duplicate Detection
    ↓
Summarize
    ↓
Score
    ↓
Human Review
    ↓
Approved Queue
    ↓
Build
    ↓
Publish
```

## Discovery schedule

Initial schedule:

- 04:00 ET
- 06:30 ET
- 09:00 ET
- 10:30 ET
- 13:00 ET
- 16:00 ET
- 19:00 ET
- 22:00 ET

Adjust based on actual news volume and editorial workload.

## Human approval

**Mandatory.** Nothing reaches the live site without human approval.

## Candidate fields

- Unique ID
- Original headline
- AINetWatch headline
- Alternative headlines
- Source URL
- Primary source
- Additional sources
- Source type
- Reliability
- Corroboration status
- Category
- Secondary categories
- Priority score
- Confidence score
- Discovery timestamp
- Publication timestamp
- Duplicate group
- Summary
- Paywall status
- Status

## Candidate status

```text
discovered
review
approved
published
archived
rejected
duplicate
```

---

# 10. Phase 7 — Source Quality & Duplicate Intelligence

**Priority: P2**

## Source classification

### Source Type

- Primary source
- Major news outlet
- Specialist publication
- Trade publication
- Academic/research
- Government
- Corporate
- Commentary

### Reliability

- High
- Established
- Emerging
- Unverified

### Corroboration

- Primary source
- Independently corroborated
- Single-source
- Conflicting reports

## Duplicate clustering

ANWU identifies related stories and creates a story group.

Example:

```text
Story Group
├── Reuters
├── Axios
├── SecurityWeek
└── TechCrunch
```

Human reviewer selects the best headline, primary source, and supporting sources.

Only one story appears on the homepage. Additional sources remain attached for corroboration.

---

# 11. Phase 8 — ANWU v2

**Priority: P3 — Only after ANWU v1 demonstrates the need.**

Potential additions:

- Secondary AI review
- Perplexity verification
- Dynamic priority scoring
- Advanced duplicate clustering
- Source-quality scoring
- Contradiction detection
- Corroboration analysis
- Automated archive recommendations
- Editorial quality checks
- Headline fidelity checks
- Automated SEO checks

ANWU should optimize editorial quality and human efficiency, not publishing volume.

---

# 12. Data Architecture

GitHub remains the single source of truth.

## Core data files

```text
data/
├── candidate-queue.json
├── approved-queue.json
├── archive.json
└── search-index.json
```

## Preferred publishing architecture

Use **build-time generation**, not client-side rendering of the primary news wire.

```text
Discovery
    ↓
Candidate Queue
    ↓
Human Approval
    ↓
Approved Queue
    ↓
Static Build
    ↓
HTML
    ↓
GitHub
    ↓
Cloudflare
```

The published homepage should contain headlines in the initial HTML response.

JavaScript may enhance the experience but should not be required to discover primary news content.

## Generated outputs

The approved queue should generate:

- Homepage
- Archive
- Category pages
- Topic pages
- RSS
- Sitemap
- Search index
- Metadata
- Structured data

One approved dataset should drive the publishing system.

---

# 13. Archive Strategy

Do not use a strict “10 days and disappear” model.

## Homepage

Prioritize:

- Current news
- High-impact developments
- Developing stories

## Archive

Retain all published stories.

## Priority-based persistence

Evergreen/high-impact stories can remain prominent or linked through topic hubs.

Examples:

- Major model releases
- Significant AI security incidents
- Landmark regulation
- Major research
- Important court decisions
- Major infrastructure developments

---

# 14. Monetization Readiness

Reserve three ad positions:

1. Small top banner
2. Mid-page
3. Lower-page

Rules:

- Minimal
- Non-intrusive
- Clearly identifiable
- No interference with headline scanning
- No advertising initially unless traffic warrants it

Measure advertising against:

- Outbound CTR
- Return visits
- Session depth
- Page abandonment

Editorial utility takes priority over early ad revenue.

---

# 15. Analytics & Experimentation

After baseline measurement is established, test:

## Headlines

- Original vs. edited
- Short vs. longer
- Alternative headline treatments

## Placement

- Story density
- Category placement
- Tooltip indicator placement

## Tooltip

- 2 vs. 3 vs. 4 bullets
- Summary wording
- Mobile interaction

## Primary measurements

- Outbound CTR
- Stories/session
- Tooltip engagement
- Return rate
- Time to first click
- Email signup rate

---

# 16. Quick-Win Roadmap

## Step 1 — Foundation

- [ ] Search Console
- [ ] Bing Webmaster Tools
- [ ] robots.txt
- [ ] sitemap
- [ ] canonical
- [ ] metadata
- [ ] structured data
- [ ] analytics
- [ ] indexing verification

## Step 2 — Wire

- [ ] Typography
- [ ] Spacing
- [ ] Mobile
- [ ] Masthead
- [ ] Favicon
- [ ] Headline hierarchy
- [ ] Source presentation

## Step 3 — Utility

- [ ] Categories
- [ ] Tooltips
- [ ] Mobile summary interaction
- [ ] RSS
- [ ] Archive
- [ ] Search

## Step 4 — SEO

- [ ] Category pages
- [ ] Topic hubs
- [ ] Internal linking
- [ ] Evergreen content structure
- [ ] Structured data expansion

## Step 5 — Audience

- [ ] Email
- [ ] X
- [ ] LinkedIn
- [ ] Bluesky
- [ ] Social publishing workflow

## Step 6 — ANWU v1

- [ ] Candidate Queue
- [ ] Discovery
- [ ] Categorization
- [ ] Deduplication
- [ ] Summaries
- [ ] Scoring
- [ ] Human approval
- [ ] Automated publishing

## Step 7 — ANWU v2

- [ ] Secondary AI review
- [ ] Advanced source verification
- [ ] Advanced clustering
- [ ] Automated quality controls

## Step 8 — Monetization

- [ ] Activate reserved ad slots
- [ ] Measure effect
- [ ] Optimize placement

---

# 17. Recommended GitHub Repository Documentation

```text
docs/
├── MASTER-PLAN.md
├── PRODUCT-SPEC.md
├── SEO-SPEC.md
├── ANWU-SPEC.md
├── DATA-ARCHITECTURE.md
├── EDITORIAL-STANDARDS.md
└── SOCIAL-STRATEGY.md
```

## Documentation roles

**MASTER-PLAN.md** — strategic roadmap and phases.

**PRODUCT-SPEC.md** — exact behavior for homepage, headlines, tooltips, mobile, categories, archive, search, RSS and footer.

**SEO-SPEC.md** — metadata, sitemap, canonical, structured data, category pages, topic hubs and indexing requirements.

**ANWU-SPEC.md** — workflow, candidate schema, scoring, source classification, duplicate handling, human approval and publishing.

**DATA-ARCHITECTURE.md** — JSON schemas, build process, generated files and publishing flow.

**EDITORIAL-STANDARDS.md** — source standards, headline standards, attribution, corrections, AI-assisted processing and human review.

**SOCIAL-STRATEGY.md** — platform priorities, posting formats, brand standards and publishing workflow.

---

# 18. Project Management Model

Use GitHub as the execution system.

## Project workflow

```text
BACKLOG
   ↓
READY
   ↓
IN PROGRESS
   ↓
REVIEW
   ↓
APPROVED
   ↓
DONE
```

Each implementation task should become a GitHub Issue.

Example IDs:

```text
SEO-001  Verify robots.txt
SEO-002  Create XML sitemap
UI-001   Redesign masthead
UI-002   Improve mobile typography
CONTENT-001  Implement categories
TOOL-001  Desktop tooltip
ANWU-001  Candidate Queue schema
ANWU-002  Duplicate clustering
```

## Issue fields

Each issue should include:

- ID
- Phase
- Priority
- Description
- Acceptance criteria
- Dependencies
- Status
- Estimated effort
- Notes

---

# 19. Project Principles

1. **Indexability before automation.**
2. **Measure before optimizing.**
3. **Keep the homepage minimalist.**
4. **Use summaries to improve click decisions, not replace source articles.**
5. **Human approval remains mandatory.**
6. **Archive everything worth retaining.**
7. **Use build-time static HTML for core content.**
8. **Treat source quality and corroboration separately.**
9. **Optimize for returning qualified readers, not raw pageviews.**
10. **Add complexity only when actual volume or data justifies it.**
11. **Keep the GitHub repository as the system of record.**
12. **Use ANWU to reduce editorial workload, not to manufacture content volume.**

---

# 20. Definition of Success

AINetWatch succeeds when a reader can:

**Open the site → scan dozens of meaningful AI developments quickly → understand the important ones through summaries → click directly to the best original reporting → return later for new developments.**

The technology should make that experience faster and more reliable without making the homepage more complicated.
