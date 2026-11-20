# Experimental trendspyg Google Trends provider

Updated: 2026-11-20

## Status and positioning

This is an optional, experimental, non-official public-web integration. It is not a Google Trends API and is not an exact replacement for DataForSEO. It uses the third-party \`trendspyg\` package (pinned to 1.9.0, MIT) to request Google Trends public web data.

The upstream library documents HTTP-only mode, but direct public requests may be refused or rate-limited by Google (for example, HTTP 403/429). A successful response is not guaranteed. Before enabling automated use, review Google's current Terms of Service, applicable usage rules, and the rights to collect, retain, analyze, and use the returned data. Public visibility alone is not permission for unrestricted automation or redistribution.

This connector is disabled by default. It does not use Google credentials, browser automation, Chrome, login cookies, cookie jars, browser fallback, proxy rotation, identity rotation, automatic retries, or access-control workarounds. On any request, parsing, or rate-limit failure the run stops and records the failure. The default request spacing and budget are deliberately conservative, not a guarantee of compliance or availability.

## Install

Python 3.11 or later:

~~~powershell
python -m pip install -e ".[trendspyg,dev]"
~~~

The base project remains usable without this optional dependency.

## Query plan

The collector reuses the DataForSEO query CSV schema so the same reviewed product/market query groups can be evaluated by either provider:

- \`product_id\` — must already exist in the local product catalogue.
- \`market_code\` — US, CA, or GB; passed as the Google Trends geography (\`geo\`), not proof of the audience's actual country.
- \`comparison_set\` — keeps standalone phrases separate from related-term comparisons.
- \`keyword\` — search phrase.
- \`date_from\`, \`date_to\` — requested period.
- \`search_type\` — this provider supports \`web\` only.
- \`language_code\` — retained for a shared query-plan schema; this adapter uses the upstream package's language behavior and does not claim that it reproduces Google's UI language selection.

The existing Kobe 9 US plan can be previewed as an example. Use only products actually present in your own registry.

## Preview (no network requests)

~~~powershell
python scripts/collect_trendspyg_trends.py --db data/registry.sqlite --queries configs/manual_research/dataforseo_google_trends_pilot.csv
~~~

Preview validates product references, request count, market, dates and search type. It prints the exact groups and explains which values share a scale. It does not contact Google.

## Explicit live execution

Only after reviewing the current Google terms and the query plan:

~~~powershell
python scripts/collect_trendspyg_trends.py --db data/registry.sqlite --queries configs/manual_research/dataforseo_google_trends_pilot.csv --execute --enable-public-web-provider
~~~

Default guards:
- Maximum 5 Google Trends requests per Pacific Time day.
- Minimum 15 seconds between requests.
- No automatic retry. Any 403, 429, risk-control, malformed response, or other provider error stops the current run.
- Every request attempt is logged in \`api_request_log\`; run metadata and counts are stored in \`source_runs\`.

Optional environment overrides:
- \`TRENDSPYG_DAILY_REQUEST_BUDGET\`
- \`TRENDSPYG_MIN_REQUEST_INTERVAL_SECONDS\`

CLI overrides:
- \`--max-requests N\`
- \`--min-interval SECONDS\`

Do not raise these limits to evade restrictions or continue after Google refuses a request.

## Comparability and normalization rules

Google Trends reports relative interest on a 0–100 scale, not absolute search volume.

- One keyword uses its own independently normalized series. Do not compare it with another independently queried keyword or group.
- Two to five keywords are submitted as one comparison group and share that group's 0–100 scale.
- Different comparison groups, markets, search types, and date windows are never directly compared.
- \`comparison_group\` is stored on both \`source_observations\` and \`search_snapshots\`.
- A point marked partial is skipped and counted as missing. The collector stores the actual point date as both \`period_start\` and \`period_end\`; it does not invent a weekly interval.
- Numeric zero is preserved, with a note that it can indicate insufficient search data rather than proven zero demand.

## Storage and scoring isolation

Successful observations are stored as non-synthetic rows in:
- \`source_observations\`
- \`search_snapshots\`

They retain the Google Trends Explore URL, market, keyword, comparison group, capture time, collection method, and the fact that the data came from the non-official public-web library.

This collector does not write TikTok/social content records or metric snapshots. It does not alter product identity, audience attribution, or social performance signals. A stored Trends index remains an observed relative-search signal and must not be described as absolute volume or market-wide demand.

## Validation status

The automated tests use mocked responses and an offline fake client. CI installs the real optional package and checks integration compatibility, but it does not make live Google requests. The provider must not be described as live-validated until an explicitly reviewed, permitted pilot has run successfully and the saved records have passed provenance and value checks.

## Upstream references

- PyPI package: https://pypi.org/project/trendspyg/
- Source repository: https://github.com/flack0x/trendspyg
- Google Trends: https://trends.google.com/trends/
- Google Terms of Service: https://policies.google.com/terms
