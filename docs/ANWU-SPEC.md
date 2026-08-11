# AINetWatch.com — ANWU Specification

**Version:** 1.0  
**Date:** August 11, 2026  
**Status:** Planning  
**Related documents:**
- `docs/MASTER-PLAN.md`
- `docs/PRODUCT-SPEC.md`

---

# 1. Purpose
ANWU is the automated editorial workflow supporting AINetWatch. Its purpose is to reduce human effort required to discover, normalize, classify, deduplicate, summarize, prioritize, review, and publish AI-news candidates.

ANWU does **not** have authority to publish directly to the live site.

> **AI prepares. Humans decide.**

# 2. Editorial Control
Human approval is mandatory. Automation may discover, analyze, score, cluster, summarize, suggest, archive candidates, and prepare publishing batches, but final editorial authority remains human.

Automation must not publish a story, change published editorial content, remove a published story, override a correction, or bypass human review without explicit human approval.

# 3. Core Workflow
```text
DISCOVERY
    ↓
CANDIDATE QUEUE
    ↓
NORMALIZATION
    ↓
SOURCE ANALYSIS
    ↓
CATEGORY
    ↓
DUPLICATE CLUSTERING
    ↓
SUMMARY GENERATION
    ↓
HEADLINE ALTERNATIVES
    ↓
PRIORITY / CONFIDENCE
    ↓
HUMAN REVIEW
    ↓
APPROVED QUEUE
    ↓
STATIC BUILD
    ↓
PUBLISH
```

# 4. Candidate Queue
The Candidate Queue is the working inventory of potentially publishable stories.

Initial location:
```text
data/candidate-queue.json
```
The queue should remain version-controlled.

# 5. Candidate Object
Each candidate should contain at least:
```json
{
  "id": "",
  "status": "discovered",
  "original_headline": "",
  "ainetwatch_headline": "",
  "headline_alternatives": [],
  "primary_source": {
    "name": "",
    "url": "",
    "source_type": "",
    "reliability": ""
  },
  "additional_sources": [],
  "category": "",
  "secondary_categories": [],
  "summary": [],
  "priority_score": 0,
  "confidence_score": 0,
  "corroboration": "",
  "duplicate_group": "",
  "paywall_status": "",
  "discovered_at": "",
  "published_at": "",
  "updated_at": ""
}
```
Additional fields may be added as the system evolves.

# 6. Candidate Status
Allowed initial states:
```text
discovered
review
approved
published
archived
rejected
duplicate
```

State transitions:
```text
discovered → review
review → approved
review → rejected
review → duplicate
approved → published
published → archived
```
Automation must not bypass human review.

# 7. Discovery
ANWU should discover candidates from approved sources and configured discovery mechanisms. Favor breaking developments, major model releases, important research, significant security events, governance/regulation, business, infrastructure, applications, and major announcements.

Discovery should optimize for editorial value rather than volume.

# 8. Discovery Schedule
Initial schedule:

| Time ET | Purpose |
|---|---|
| 04:00 | Overnight developments |
| 06:30 | Early morning update |
| 09:00 | Morning news cycle |
| 10:30 | Late morning update |
| 13:00 | Midday update |
| 16:00 | Afternoon news cycle |
| 19:00 | Evening update |
| 22:00 | Late evening / overnight preparation |

The schedule should be configurable and may later adapt to high-volume news periods.

# 9. Source Classification
Use three independent dimensions.

## Source Type
```text
primary
major-news
specialist
trade
academic-research
government
corporate
commentary
```

## Reliability
```text
high
established
emerging
unverified
```

## Corroboration
```text
primary-source
independently-corroborated
single-source
conflicting
```

Do not collapse these into one universal source score.

# 10. Primary vs. Secondary Sources
A primary source may be a company announcement, government announcement, court filing, academic paper, or official research release.

Primary sources are not automatically superior for every story. Preserve the distinction between who knows something first and who reported it most reliably.

# 11. Duplicate Detection
Duplicate detection should consider:
- Headline similarity
- Entities
- Event
- Date/time
- Claims
- Named organizations
- Named individuals
- Model/product names
- Source relationships

Do not rely exclusively on headline similarity.

# 12. Story Groups
Related stories should be grouped using a shared identifier, for example:
```text
duplicate_group: 2026-08-11-openai-astra
```

The homepage should normally display one story from a group while retaining additional sources for corroboration.

# 13. Human Duplicate Review
The reviewer may select one source, combine corroborating sources, reject all candidates, keep a developing story open, replace the headline, or mark sources as conflicting.

Never silently discard corroborating information.

# 14. Headline Generation
ANWU may generate:
- Source headline
- AINetWatch headline
- Up to three alternatives

Optimize for accuracy, clarity, brevity, reader comprehension, and appropriate urgency—not sensationalism.

# 15. Headline Fidelity
AINetWatch headlines must not introduce unsupported facts, new claims, false certainty, unsubstantiated causality, or misleading implications.

Preserve qualifiers such as "may" and "reportedly" unless corroboration justifies stronger language.

# 16. Summary Generation
Generate a reader-facing summary of normally 2–4 bullets, maximum 5.

It should address some combination of:
- What happened?
- Why does it matter?
- Who is affected?
- What is unusual?
- What should the reader know before clicking?

Avoid long explanations, unsupported interpretation, headline repetition, excessive technical detail, and copied source text.

# 17. Summary Quality Controls
Before human review, check:
- Every claim is source-supported
- No new facts were introduced
- Certainty is not exaggerated
- Reporting and speculation are distinguished
- Headline is not merely repeated
- Length limits are respected
- Language is understandable

# 18. Priority Score
Use a 0–10 scale.

| Score | Meaning |
|---:|---|
| 9–10 | Major/high-impact development |
| 7–8 | Significant |
| 5–6 | Worth reviewing |
| 3–4 | Low priority |
| 0–2 | Likely reject/archive |

Priority is advisory, not an automatic publication decision.

# 19. Priority Factors
Consider:
- Magnitude
- Number affected
- AI-industry importance
- Security implications
- Governance/regulatory significance
- Model significance
- Business significance
- Novelty
- Timeliness
- Reader interest
- Corroboration

Weighting should remain configurable.

# 20. Confidence Score
Confidence is separate from priority.

Example:
```text
priority_score: 8
confidence_score: 0.94
```

High-priority/low-confidence stories require more careful review. Low-priority/high-confidence stories may simply be less important.

# 21. Paywall Handling
Record:
```text
paywall_status
```

Values:
```text
free
paywalled
metered
unknown
```

Paywall status must not override source quality. Link only to legitimately accessible reporting.

# 22. Human Review Interface
Show:
- Original headline
- Suggested headline
- Alternatives
- Primary and additional sources
- Source classification
- Priority
- Confidence
- Duplicate group
- Summary
- Category
- Discovery time
- Paywall status

Actions:
```text
APPROVE
EDIT
MERGE
REJECT
DUPLICATE
ARCHIVE
```

# 23. Approved Queue
Approved stories move to:
```text
data/approved-queue.json
```

Only approved records may enter the public build.

# 24. Publishing
A publishing operation should:
1. Read approved stories.
2. Update homepage data.
3. Update archive.
4. Update category pages.
5. Update search index.
6. Update RSS.
7. Update sitemap where necessary.
8. Generate static HTML.
9. Commit changes to GitHub.
10. Trigger deployment.

Batch publishing where practical.

# 25. Build-Time Publishing
Primary content should be rendered to static HTML during the build:
```text
approved-queue.json
        ↓
      build
        ↓
   static HTML
        ↓
     GitHub
        ↓
    Cloudflare
```

The browser should not need the candidate queue to discover primary news content.

# 26. Archive Management
Published stories remain in the archive. ANWU may recommend homepage removal, archive retention, category retention, evergreen promotion, or story updates.

Do not implement a strict 10-day deletion rule.

# 27. Evergreen Stories
Potential evergreen stories include:
- Major model releases
- Major AI security incidents
- Landmark regulation
- Important research
- Major court decisions
- Major infrastructure developments

These may receive special archive/topic treatment.

# 28. Secondary AI Review
ANWU v1 should not depend on a second AI reviewer.

ANWU v2 may add another model/provider for:
- Priority rescoring
- Duplicate reclustering
- Contradiction detection
- Unsupported-claim flags
- Summary comparison
- Source-conflict identification

Secondary review remains advisory; human approval remains mandatory.

# 29. Failure Handling
If automation fails:
- Do not publish incomplete data
- Preserve the previous approved dataset
- Record the failure
- Flag affected candidates
- Allow manual recovery

The live site should remain operational if ANWU is unavailable.

# 30. Security Rules
Never place in the public repository:
- API keys
- Access tokens
- Passwords
- Webhook secrets
- Private credentials
- Authentication secrets

Store secrets using appropriate GitHub Actions/environment secret mechanisms.

ANWU must not bypass human approval.

# 31. Logging
Record enough information to understand ANWU actions, including:
- Discovery time
- Processing time
- Candidate ID
- Source
- Model/process used
- Score generated
- Duplicate group
- Human decision
- Publication time
- Errors

Logs must not contain secrets.

# 32. Versioning
Version material ANWU changes, for example:
```text
ANWU v1.0
ANWU v1.1
ANWU v2.0
```

Document changes to scoring, source classification, summary generation, duplicate detection, and publishing behavior.

# 33. Initial Implementation Priority
Build in this order:
1. Candidate Queue schema
2. Discovery
3. Source normalization
4. Categorization
5. Duplicate detection
6. Summary generation
7. Headline alternatives
8. Priority/confidence scoring
9. Human review
10. Approved Queue
11. Static build
12. Automated publishing

Do not begin with sophisticated multi-agent orchestration.

# 34. ANWU v1 Definition of Done
ANWU v1 is complete when it can:
- [ ] Discover candidates
- [ ] Store candidates in JSON
- [ ] Normalize source information
- [ ] Categorize stories
- [ ] Detect likely duplicates
- [ ] Generate headline alternatives
- [ ] Generate short summaries
- [ ] Assign priority
- [ ] Assign confidence
- [ ] Present candidates for human review
- [ ] Require explicit approval
- [ ] Move approved stories into approved queue
- [ ] Generate static site content
- [ ] Publish approved content
- [ ] Preserve rejected/duplicate candidates
- [ ] Recover safely from processing failures

# 35. ANWU Design Principles
1. **AI prepares; humans decide.**
2. **No automated publication without approval.**
3. **Priority and confidence are different concepts.**
4. **Source quality and corroboration are different concepts.**
5. **Duplicate stories should be clustered, not blindly discarded.**
6. **Summaries should improve click decisions, not replace source reporting.**
7. **Headline accuracy is more important than clickbait.**
8. **The approved queue is the publishing boundary.**
9. **The system must fail safely.**
10. **Complexity should be added only when actual volume justifies it.**
