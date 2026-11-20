# Embedded TikTok provider (DTK)

Status: experimental and opt-in. The code and offline tests are present, but no live collection has been accepted yet.

## Design

The provider imports a pinned subset of DTK 5.1.11 inside the existing fashion-trend-radar Python process:

- TikTok endpoint definitions and parameter builders;
- DTK response parsers and normalized content models;
- DTK's native Python TikTok signer;
- DTK's wreq-backed transport.

It does not start the DTK FastAPI application, MCP server, task worker, browser-RPC sidecar, PostgreSQL/TimescaleDB, Redis, downloader or transcriber. It writes research candidates to the existing SQLite registry.

The optional dependency is pinned to upstream commit 3c045e11aecd58ab076562f1246931b9dd5d909b. It is supported only on Python 3.12 and 3.13. This integration imports DTK internal modules rather than a stable public SDK, so any DTK upgrade requires explicit compatibility review and tests.

## Guest-only access boundary

The provider sends an empty cookie jar and never reads browser profiles, Creator credentials, session files or imported login cookies. It also does not launch a browser or DTK's identity-minting service.

The allowlist is limited to:

- a known public video ID and its public detail;
- a public creator handle and public author posts;
- a hashtag detail and posts listed under that hashtag.

Free-text TikTok post search is deliberately not supported. The pinned DTK endpoint metadata marks TikTok keyword post search as login-required. The provider checks DTK's endpoint login metadata before each request and refuses endpoints outside its own allowlist.

A platform response indicating login requirement, HTTP 401/403/429, invalid JSON or risk control is recorded as a failed request. There is no automatic retry, account rotation, cookie import or other attempt to bypass a refusal.

The supported public endpoints can change or become restricted at any time. A previous successful public request is not a guarantee of future availability.

## Usage rights and opt-in

This is a non-official public-web integration, not TikTok's Developer API. Public visibility does not itself grant unrestricted automated collection, reuse or long-term storage rights. Review current TikTok terms, applicable law and the intended data-use rights before enabling it. The collector is disabled unless the operator supplies both explicit CLI flags.

## Install

Use Python 3.12 or 3.13:

~~~powershell
python -m pip install -e ".[tiktok-embedded,dev]"
~~~

No separate DTK deployment is required. The extra installs DTK and its declared dependencies into the same virtual environment. Its dependency set is larger than a small client library because the upstream package declares dependencies for its complete application, even though this integration does not start the web app, Redis or PostgreSQL.

Python 3.11 remains supported by the base fashion-trend-radar package and its normal test suite; the embedded DTK client refuses to start there.

## Prepare a seed file

Copy the header-only template:

~~~powershell
Copy-Item configs/manual_research/tiktok_embedded_seed_template.csv configs/manual_research/tiktok_embedded_seeds.csv
~~~

The CSV columns are:

| Field | Meaning |
|---|---|
| market_code | US, CA or GB research context for the seed |
| seed_type | video_id, creator_handle or hashtag |
| seed_value | Public numeric video ID, public @handle or hashtag |
| suggested_product_id | Optional existing product ID for later manual review |
| max_items | Maximum items requested for creator/hashtag seeds, 1–50 |

There is deliberately no keyword seed type. Obtain creator handles, hashtags or known public video IDs from sources that you are permitted to use, then add the seed values to this plan.

## Preview and run

Preview first. Preview mode makes no upstream requests:

~~~powershell
python scripts/collect_tiktok_embedded.py --db data/registry.sqlite --seeds configs/manual_research/tiktok_embedded_seeds.csv
~~~

After reviewing the seeds and permissions, explicitly enable the experimental provider:

~~~powershell
python scripts/collect_tiktok_embedded.py --db data/registry.sqlite --seeds configs/manual_research/tiktok_embedded_seeds.csv --execute --enable-public-web-provider
~~~

Defaults are deliberately conservative:

- 40 API requests per Pacific Time day, configurable with TIKTOK_DAILY_REQUEST_BUDGET (maximum 1,000);
- 40 requests per run, configurable with TIKTOK_MAX_REQUESTS_PER_RUN (maximum 200);
- at least 2.5 seconds between calls, configurable with TIKTOK_MIN_REQUEST_INTERVAL_SECONDS (minimum 2 seconds);
- optional operator-supplied outbound proxy via TIKTOK_PROXY_URL.

Do not set a faster interval to overcome a restriction. Stop the run if the platform refuses access or returns risk-control responses.

## Audit a completed pilot run

After a one-seed test, audit the exact run without making another TikTok request:

~~~powershell
python scripts/audit_tiktok_embedded_run.py --db data/registry.sqlite --latest
~~~

For a specific run, use the `run_id` printed by the collector:

~~~powershell
python scripts/audit_tiktok_embedded_run.py --db data/registry.sqlite --run-id YOUR_RUN_ID
~~~

The audit opens the existing database in read-only mode. It does not initialize or migrate the schema and does not call TikTok. It checks that:

- the candidate row count matches `source_runs.items_saved`;
- the API request log count matches `metadata.requests_made`, and each request has a final successful result;
- candidate URLs use HTTPS on `www.tiktok.com`, with no duplicate market/content identities;
- market attribution remains `research_context`, with no inferred audience country;
- candidates still marked `needs_review` have not been inserted into `social_signals` or `metric_snapshots`.

The latest selector includes failed and partial runs, so a later unsuccessful attempt is not silently skipped. A non-zero exit or `mismatch` is a failed acceptance result; inspect the request log and candidate audit before making any further collection attempt.

## Market and scoring semantics

The CSV market_code is the market context used to plan the seed and configure request locale hints. It does not prove creator residence or audience geography. Candidate rows use market_basis=research_context and leave audience_market_code NULL.

All content starts with link_status=needs_review. suggested_product_id is a human review hint, not a confirmed product match. The collector does not create social_signals or metric_snapshots and therefore cannot change product scores. A later review process must confirm exact product identity, rights to retain the data and any market attribution before promoting a record into scoring.

## Data and privacy

The integration stores normalized public content fields and visible counters, not the complete raw response. Creator IDs are hashed before persistence; the visible creator handle is kept for research review. Do not add sensitive personal data or use this feature to profile private individuals.

The source run and each attempted endpoint are recorded in source_runs and api_request_log. A failed request is not treated as an empty result or zero metric.

## Validation status

Offline tests use a fake client to verify seed validation, source-run provenance, the market-basis rule and isolation from product-scoring tables. These tests do not prove that TikTok endpoints are reachable from a particular network, that the platform will continue to serve anonymous requests, or that a collection is statistically representative.

Before a larger run, use a single public seed, inspect its recorded endpoint outcomes and candidate rows, and manually verify the returned item URLs and product identity.
