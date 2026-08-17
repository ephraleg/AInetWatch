# Graph Report - ainwa-review-v1  (2026-08-17)

## Corpus Check
- Corpus is ~44,698 words - fits in a single context window. You may not need a graph.

## Summary
- 595 nodes · 1118 edges · 31 communities (18 shown, 13 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 10 edges (avg confidence: 0.81)
- Token cost: 44,086 input · 0 output

## Community Hubs (Navigation)
- Build Pipeline Tests
- Ingest & Feed Tests
- Source Registry & Discovery Policy
- Review Console Server
- Candidate Generation & AI Advisors
- Filter & Dedup Tests
- Feed Ingestion & SSRF Guard
- Static Homepage Builder
- Candidate Scoring Tests
- Publish & Deploy Tests
- Claude Selection & Parsing
- Candidate Headline Tests
- Candidate Filtering
- SSRF / Private IP Tests
- Generate Main & Approved Guard
- Queue Safety & Race Tests
- URL Canonicalization Tests
- Live Content Migration
- Title Deduplication Tests
- Publication Date Parsing
- Candidate Carryover Tests
- Candidate Preselection Tests
- Registry Loading Tests
- Server Test Harness
- Advisory Model Tests
- Diversity Walk Tests
- URL Validation Tests
- Queue Timestamp Tests
- Redirect Safety Tests
- Response Size Limit Tests
- Publish Script

## God Nodes (most connected - your core abstractions)
1. `_item()` - 56 edges
2. `_selection()` - 38 edges
3. `_item()` - 23 edges
4. `_make_item_lookup()` - 22 edges
5. `_story()` - 20 edges
6. `_write_queue()` - 20 edges
7. `_write_template()` - 19 edges
8. `TestPrivateIPBlocking` - 16 edges
9. `main()` - 14 edges
10. `TestRejectStale` - 13 edges

## Surprising Connections (you probably didn't know these)
- `AINWA Discovery Policy` --semantically_similar_to--> `ainwa-discovery.yml as Operational Discovery Registry`  [INFERRED] [semantically similar]
  ainwa-discovery.yml → README.md
- `review() — POST /api/review with CSRF token` --implements--> `Per-Server-Start CSRF Token (POST /api/review)`  [INFERRED]
  index.html → README.md
- `proposalOf() — normalize candidate proposal fields` --implements--> `Candidate Record Contract (nested + ANWU-legacy formats)`  [INFERRED]
  index.html → README.md
- `AINWA Review Console Frontend (index.html)` --implements--> `AINWA Review Console v1`  [EXTRACTED]
  index.html → README.md
- `AINWA Review Console Frontend (index.html)` --implements--> `Four Human Review Decisions`  [EXTRACTED]
  index.html → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Human Review Action Flow: UI triggers action, CSRF-gated POST, server writes approved/log** — index_html_review_function, index_html_csrf_token_state, index_html_api_review_endpoint, readme_approved_queue, readme_review_log [INFERRED 0.90]
- **Candidate Normalization: sourceOf + proposalOf unify nested and ANWU-legacy formats for rendering** — index_html_source_of, index_html_proposal_of, readme_candidate_contract, index_html_render_candidate [INFERRED 0.88]
- **Discovery Ingestion Source Policy: registry + ingestion tiers + source resolution govern what gets fetched** — ainwa_discovery_yml_registry, ainwa_discovery_yml_ingestion_policy, ainwa_discovery_yml_source_resolution, ainwa_discovery_yml_tier1_anchors [EXTRACTED 0.93]

## Communities (31 total, 13 thin omitted)

### Community 0 - "Build Pipeline Tests"
Cohesion: 0.09
Nodes (28): Path, Critical tests for build.py (AINWA-009). Security gates tested here: - Unlocked…, Unlocked record must cause a hard exit — no output written., Records with a non-approved top-level status must not appear in output., All story text must be HTML-escaped before output., Non-http(s) URLs must not produce an href attribute., 60 approved stories → 50 most recent on homepage, 10 in archive., Return 60 stories. Story IDs encode their recency: s59 is newest. (+20 more)

### Community 1 - "Ingest & Feed Tests"
Cohesion: 0.06
Nodes (7): TestAtomParsing, TestExactURLDedup, TestMalformedXML, TestResolveAndCheck, TestRSSParsing, TestSchemeRejection, TestSourceFields

### Community 2 - "Source Registry & Discovery Policy"
Cohesion: 0.06
Nodes (44): Access Policy (paywall scoring), Cybersecurity Sources (BleepingComputer, The Record, etc.), Discovery-Only Sources (aggregators/newsletters), AINWA Discovery Policy, Government and Regulation Sources, Ingestion Policy (regular/deferred/inactive tiers), Primary AI Lab Sources (OpenAI, Anthropic, Google DeepMind, etc.), AINWA Discovery Registry (ainwa-discovery.yml) (+36 more)

### Community 3 - "Review Console Server"
Cohesion: 0.11
Nodes (32): BaseHTTPRequestHandler, append_log(), apply_action(), apply_manual(), approved_list(), candidate_list(), _canonical_url(), duplicate_ids() (+24 more)

### Community 4 - "Candidate Generation & AI Advisors"
Cohesion: 0.10
Nodes (35): _advisory_prompt(), build_candidates(), build_selection_prompt(), call_claude(), call_gemini(), call_grok(), _carryover_candidates(), _diversity_walk() (+27 more)

### Community 5 - "Filter & Dedup Tests"
Cohesion: 0.13
Nodes (6): _item(), datetime, _run_ts(), TestDedupCanonicalUrl, TestFilterItems, TestRejectStale

### Community 6 - "Feed Ingestion & SSRF Guard"
Cohesion: 0.10
Nodes (31): Element, dedup_by_url(), _guess_desc_format(), is_blocked_ip(), load_registry(), main(), _make_item_id(), _now_iso() (+23 more)

### Community 7 - "Static Homepage Builder"
Cohesion: 0.19
Nodes (23): build(), esc(), is_http_url(), load_chrome(), load_stories(), main(), _priority_rank(), Path (+15 more)

### Community 8 - "Candidate Scoring Tests"
Cohesion: 0.13
Nodes (6): _score_item returns deterministic scores based on role, tier, access, recency,…, Tier component of _score_item., Access component of _score_item., TestAccessScoring, TestScoring, TestTierScoring

### Community 9 - "Publish & Deploy Tests"
Cohesion: 0.11
Nodes (12): _locked_story(), Path, AINWA-011 (#89) Minimum critical production-readiness checks. Does NOT…, Preflight success, credential-absent --deploy failure, token not printed., publish.sh must target dist/, not ., and its allowlist must exclude sensitive…, No production-path file may reference /Users/q/AInetWatch. Test files are…, editorial_notes and advisory must never surface in generated HTML even when…, TestProductionPathIsolation (+4 more)

### Community 10 - "Claude Selection & Parsing"
Cohesion: 0.14
Nodes (7): _item(), build_selection_prompt produces a flat numbered list via preselection., Claude's integer item_id maps deterministically to exact source records., _parse_claude_response parses Claude's flat ranked JSON array., TestFlatResponseContract, TestIndexSelection, TestPromptFormat

### Community 11 - "Candidate Headline Tests"
Cohesion: 0.24
Nodes (4): _make_item_lookup(), _selection(), TestBriefHeadline, TestBuildCandidatesSchema

### Community 12 - "Candidate Filtering"
Cohesion: 0.19
Nodes (17): canonical_url(), _dedup_canonical_url(), _dedup_title(), filter_items(), main(), _now_iso(), _parse_pub_date(), _parse_run_id() (+9 more)

### Community 14 - "Generate Main & Approved Guard"
Cohesion: 0.24
Nodes (5): Path, approved-queue.json must be identical before and after generate.py runs., A story already in approved-queue must not appear as a new candidate., Claude sometimes wraps JSON in ```json ... ``` — must be handled., TestMain

### Community 15 - "Queue Safety & Race Tests"
Cohesion: 0.17
Nodes (5): Confirm all anchor-specific constants and functions are gone., generate.py must not clobber reviewer changes made during the API-call window., A manual candidate added while Claude is running must survive generate.py's…, TestAnchorLogicRemoved, TestQueueWriteRace

### Community 17 - "Live Content Migration"
Cohesion: 0.36
Nodes (9): backup_and_write(), extract_stories(), main(), Path, Convert an extracted story into the exact approved-queue.json schema., Return one dict per story found in the live-content HTML, in document order., read_approved(), to_approved_record() (+1 more)

### Community 23 - "Server Test Harness"
Cohesion: 0.40
Nodes (9): check(), finish(), free_port(), main(), make_candidate(), Path, request(), seed() (+1 more)

### Community 24 - "Advisory Model Tests"
Cohesion: 0.29
Nodes (3): Exception, Grok failure must not prevent candidate file from being written., TestAdvisory

## Knowledge Gaps
- **15 isolated node(s):** `publish.sh script`, `Review Log (data/review-log.json)`, `server.py (Python HTTP server)`, `Top Story Selection (Claude + external reviewers)`, `Primary AI Lab Sources (OpenAI, Anthropic, Google DeepMind, etc.)` (+10 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_item()` connect `Claude Selection & Parsing` to `Candidate Scoring Tests`, `Candidate Headline Tests`, `Generate Main & Approved Guard`, `Queue Safety & Race Tests`, `Candidate Carryover Tests`, `Candidate Preselection Tests`, `Advisory Model Tests`, `Queue Timestamp Tests`?**
  _High betweenness centrality (0.049) - this node is a cross-community bridge._
- **Why does `QueueFileError` connect `Review Console Server` to `Advisory Model Tests`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `_selection()` connect `Candidate Headline Tests` to `Claude Selection & Parsing`, `Generate Main & Approved Guard`, `Queue Safety & Race Tests`, `Candidate Carryover Tests`, `Advisory Model Tests`, `Queue Timestamp Tests`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **What connects `publish.sh script`, `Review Log (data/review-log.json)`, `server.py (Python HTTP server)` to the rest of the system?**
  _15 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Build Pipeline Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.09210526315789473 - nodes in this community are weakly interconnected._
- **Should `Ingest & Feed Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.05580693815987934 - nodes in this community are weakly interconnected._
- **Should `Source Registry & Discovery Policy` be split into smaller, more focused modules?**
  _Cohesion score 0.06025369978858351 - nodes in this community are weakly interconnected._