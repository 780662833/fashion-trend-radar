# Provider-backed research collectors

Updated: 2026-11-19

This implementation adds a guarded DataForSEO Google Trends collector, an Exolyt CSV schema inspector, and a conservative Scrapy public-page fetcher. It does not fabricate sample observations and does not bypass platform access controls.

## 1. DataForSEO Google Trends — implemented, credentials required

### What it does

- Uses the DataForSEO Google Trends Explore Standard asynchronous task API.
- Supports US, CA and GB.
- Batches up to five keywords per task.
- Separates independent `comparison_set` groups, so an exact product phrase is not normalized against a different related-term group.
- Stores provider task IDs, request payload, query group, market, date window, task status, missing-point counts and cost reported by the provider.
- On result retrieval, writes accepted, non-synthetic daily observations to `source_observations` and `search_snapshots`.
- Preserves reported 0 values and annotates them as potentially below the provider's reporting threshold. They are excluded from the search-growth scoring baseline rather than treated as definite zero search volume.

### Pilot plan

File: `configs/manual_research/dataforseo_google_trends_pilot.csv`

It contains two separate comparison groups:
- `exact_phrase`: Kobe 9 EM Low Protro Philippines, by itself.
- `related_term_comparison`: four related Kobe 9 phrases.

Market: US. Window: 2026-08-19 through 2026-11-19. Search type: web. The exact phrase's 100 peak is therefore not mixed into the separate related-term normalization group.

### Cost and safety

DataForSEO currently publishes Google Trends Explore Standard pricing at USD 0.0027 per task, while Live is listed at USD 0.011 per task. Prices and account-specific conditions can change; confirm the provider dashboard before execution. This project defaults to Standard. The pilot has two groups/tasks; current code defaults to a maximum of five billable task-post requests per day.

- Dry-run never makes a provider request.
- `--execute` is required before any billable task-post request.
- Result retrieval is separated from task-post submission.
- Credentials are read from local environment variables / gitignored `.env`; never place them in CSV, source code or GitHub.
- The collector records each task-post attempt and blocks when the configured daily task budget is reached.
- No provider credentials are included in the repository.

### Configure on Windows PowerShell

```powershell
python scripts/configure_dataforseo_credentials.py
```

The two prompts are hidden. They write `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` to the root `.env`, which is already gitignored.

Preview the plan without network calls or charges:

```powershell
python scripts/collect_dataforseo_trends.py --db data/registry.sqlite --queries configs/manual_research/dataforseo_google_trends_pilot.csv
```

The preview should show two billable tasks. Check the keywords, window, US market, comparison groups and estimated cost.

Only after credentials, pricing and the plan are reviewed, submit tasks:

```powershell
python scripts/collect_dataforseo_trends.py --db data/registry.sqlite --queries configs/manual_research/dataforseo_google_trends_pilot.csv --execute
```

Save the returned `run_id`. Standard tasks are asynchronous. Fetch when complete:

```powershell
python scripts/fetch_dataforseo_trends.py --db data/registry.sqlite --run-id YOUR_RUN_ID
```

If a task is still pending, retry the fetch command later. Do not re-submit the task merely because its result is not yet ready.

### Acceptance

For the Kobe 9 / US pilot verify:
- `source_runs` records the task-post run.
- `dataforseo_tasks` records task IDs, groups, dates, cost and final status.
- `api_request_log` records all request attempts.
- `source_observations` and `search_snapshots` contain real, non-synthetic values for the correct product and US.
- Exact phrase and related-term comparisons have different `comparison_group` values.
- A provider 0 is kept as 0 but has a below-reporting-threshold note.
- `missing_data` points are not transformed into zero.
- The returned provider check URL or generated Google Trends URL is retained as evidence.

A successfully queued task is not yet an accepted observation. Acceptance occurs only after the result is fetched and rows are persisted.

## 2. Exolyt — inspect the provider export first

No direct public developer API is assumed in this project. Exolyt advertises data exports and integrations; the available columns vary by report and subscription. This project therefore does not claim a live Exolyt API connector.

Export one sample report from Exolyt (for example hashtag/video monitoring) as CSV, then run:

```powershell
python scripts/inspect_exolyt_export.py --file path\to\exolyt_export.csv --out reports\provider_eval\exolyt_schema.json
```

The inspector:
- records the absolute file path, row count, SHA-256, delimiter and headers;
- suggests candidate columns for video URL, hashtag, creator, views, likes, comments, shares, date and country;
- does not import rows or claim that any candidate field is semantically correct.

Before building an importer, verify from the actual report and Exolyt documentation:
- whether the report is per-video, per-hashtag or aggregate;
- the meaning and unit of each metric;
- country attribution (creator country, audience country or filter context);
- observation time and historical interval;
- whether stable source URLs/video IDs are included;
- whether your subscription permits scheduled export and commercial internal storage.

A vendor export without video-level URLs should not be turned into per-video `social_signals`. Aggregate hashtag observations belong in `source_observations`. An account's own Creator video statistics are owned-account performance, not market-wide demand.

## 3. Scrapy — allowlisted public pages only

Install the optional dependency:

```powershell
python -m pip install -e ".[research-crawl]"
```

Copy the seed template and add only page URLs that the project is permitted to access:

```powershell
Copy-Item configs\manual_research\public_page_seeds_template.csv configs\manual_research\public_page_seeds.csv
```

Columns:
- `source_name`: descriptive source label.
- `market_code`: US/CA/GB when supported by the page; otherwise blank.
- `product_id`: existing product ID when the page is about a known product.
- `keyword`: research topic.
- `source_url`: exact HTTPS URL to fetch.
- `allowed_domain`: exact registrable/site domain allowlist, e.g. `sneakernews.com`.

Run:

```powershell
python scripts/crawl_public_pages.py --seeds configs/manual_research/public_page_seeds.csv --out data/raw/public_pages/crawl.jsonl
```

Safety controls:
- the spider fetches only URLs explicitly listed in the seed file;
- source URL host must match the declared allowlisted domain;
- TikTok, Instagram, Facebook and YouTube domains are blocked; use approved APIs/provider exports for those platforms;
- `ROBOTSTXT_OBEY=True`, 2-second delay, one concurrent request per domain, AutoThrottle, no cookies, no login/session replay and no link following;
- page output is raw research material only; it is not automatically inserted into scoring tables.

Check each site's robots.txt, terms and rights before adding it to the allowlist. A page being public does not automatically grant permission for unrestricted automated collection, redistribution or long-term storage.

## 4. Database comparison-group correction

Previously, the `search_snapshots` uniqueness key omitted `comparison_group`, so independent normalized Trends requests for the same product/market/keyword/period/source could overwrite one another. The schema migration now adds `comparison_group` and changes the uniqueness key to include it. The standard CSV search importer also uses this key.

This is important for Google Trends: indices from unrelated comparison groups must not be treated as one growth series.

## 5. Remaining external prerequisites

| Source | Implementation status | External requirement |
|---|---|---|
| DataForSEO Trends | Collector and fake-client tests implemented | User DataForSEO account + API login/password |
| Exolyt | Export schema inspector implemented | Exolyt subscription/export sample; actual fields and storage rights to be verified |
| Scrapy | Public-page spider implemented | Scrapy optional install + reviewed, allowlisted seed URLs |
| YouTube Data API | Existing collector unchanged | YouTube Data API key |
| TikTok Display/Discovery API | Not implemented here | TikTok developer-app approvals, OAuth scopes, Discovery account entitlement |

Do not start the full 297-task batch until the Kobe 9 / US DataForSEO task result has been fetched, Exolyt's actual export format has been inspected, and the public-page seed policy has been reviewed. No source should be represented as live-validated merely because its code exists.
