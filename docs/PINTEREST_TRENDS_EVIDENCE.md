# Pinterest Trends evidence workflow (v1)

Pinterest is a discovery and visual-aesthetic evidence source. It helps identify silhouettes, colors, materials, styling patterns and emerging visual themes. It is not a direct measure of purchases, sales or product-level demand. Cross-check candidates with Google Trends, TikTok Creative Center, YouTube references and first-party/retailer product evidence.

## Access policy and collection method

- First release uses manual capture from Pinterest Trends or public Pinterest pages and the existing scripts/import_source_observations.py importer.
- Do not use unofficial crawlers, session-cookie scraping, browser automation that bypasses access controls, or bulk downloading of Pins.
- Use an official Pinterest API only after the account/app has been granted the relevant access and applicable Pinterest terms have been reviewed. Do not assume Trends API access is generally available or free for every developer account.
- Store only the displayed metric and unit actually visible to the researcher. Do not convert Pin counts, saves, trend rank, percentage change and search-interest indices into one common metric.
- A Pinterest observation is not proof that a named sneaker or bag is selling. Link a validated product_id when evidence is product-specific; otherwise leave it blank and treat the record as keyword-level evidence.

## Research plan

Start with configs/manual_research/pinterest_keyword_plan.csv. It provides 36 repeatable starter research tasks across US, CA and GB, covering sneaker silhouettes, styling, colors, handbag silhouettes, materials and styling.

The CSV rows are research tasks, not collected observations; they contain no measured trend values. In Pinterest Trends, select the matching market, search each phrase, record the exact period and metric shown, and save the page URL and capture timestamp. If a phrase or metric is unavailable in a market, record that limitation instead of inventing a value.

For time-series comparisons, keep market, query wording, metric definition and period window stable across captures. Do not compare independently normalized values across different query groups unless Pinterest explicitly documents comparability.

## Import template

Copy configs/manual_research/pinterest_trends_template.csv to configs/manual_research/pinterest_trends.csv, then fill one row per observed keyword/market/period.

| Field | Rule |
|---|---|
| source_name | Exactly pinterest_trends_ui |
| observation_type | Usually trend or keyword_interest |
| market_code | US, CA or GB |
| product_id | Existing product ID only; blank for broad keyword research |
| keyword | Exact phrase searched |
| metric_name | Exact displayed metric label normalized to snake_case |
| metric_value | Numeric value displayed by Pinterest |
| metric_unit | Native unit, such as index_0_100, percent_change or rank; do not mix units |
| period_start / period_end | Actual observation window, ISO date |
| comparison_group | Optional stable query-set identifier where applicable |
| source_url | HTTPS Pinterest Trends or Pinterest source page URL |
| supporting_content_url | Optional public Pin or product reference |
| captured_at | ISO 8601 timestamp with timezone |
| notes | Market selector, category, displayed label, limitations and context |

Import command:

    python scripts/import_source_observations.py --db data/registry.sqlite --file configs/manual_research/pinterest_trends.csv

The current importer already accepts pinterest_trends_ui, validates HTTPS Pinterest source URLs, validates US/CA/GB and product IDs, preserves the original URL/window/metric and marks manual captures as non-synthetic. It does not infer missing metrics or claim the sample is representative.

## Coverage audit

Pinterest is an optional required source. To require recent real Pinterest observations in all three markets in addition to TikTok and Google Trends, run:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --required-sources tiktok_creative_center,google_trends_ui,pinterest_trends_ui --out reports/source_coverage/pinterest_v1.json

The default acceptance set remains TikTok plus Google Trends, so existing workflows are not silently changed. Pinterest becomes required only when explicitly added to --required-sources.

A current status means at least one accepted, non-synthetic observation has a period end within the configured freshness window. It does not establish statistical representativeness or commercial demand.

## Evidence interpretation

- Pinterest high / Google Trends rising / TikTok low: early visual-aesthetic candidate; continue monitoring, do not call it a proven product winner.
- Pinterest high / TikTok high / Google Trends rising: stronger cross-source momentum; inspect exact product identity and competitive saturation.
- Pinterest high / Google Trends flat / TikTok low: possibly inspiration-led or seasonal styling interest; keep at keyword/theme level.
- Do not add Pinterest-specific bonus points to the existing score until comparable history and a documented scoring review exist.

## Official references

- Pinterest Trends: https://trends.pinterest.com/
- Pinterest Trends API documentation: https://developers.pinterest.com/docs/analytics-and-reports/trends/
- Pinterest developer policies: https://policy.pinterest.com/en/developer-guidelines
