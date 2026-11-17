# Product-market research query planning

The first release uses a repeatable query plan rather than ad-hoc keyword brainstorming. The plan is generated from the real product catalogue and optional product-market mappings; it contains search tasks only, not collected observations or trend metrics.

## 1. Import product-market context

Start with products in the registry, then optionally add country-specific vocabulary:

```powershell
python -m fashion_radar.cli --db data/registry.sqlite import-products --file configs/products.csv
python scripts/import_product_markets.py --db data/registry.sqlite --file configs/product_market.csv
```

Copy `configs/product_market_template.csv` and fill one row per product/market. Currency is fixed by market: US=USD, CA=CAD, GB=GBP. `market_search_terms` is a semicolon-separated list of manually curated local phrases. It is optional; the planner still creates all three market plans if no mapping exists.

Example terms:
- US sneaker: `basketball shoes; sneaker outfit`
- GB sneaker: `basketball trainers; trainers outfit`
- US bag: `mini purse; everyday shoulder bag`
- GB bag: `small handbag; crossbody styling`

Do not copy a market's terms into another market without checking local wording. The optional local price is context only and is not used as trend evidence.

## 2. Generate the query plan

```powershell
python scripts/generate_research_plan.py --db data/registry.sqlite --out configs/manual_research/research_query_plan.csv
```

Restrict the output when doing a single-market pilot:

```powershell
python scripts/generate_research_plan.py --db data/registry.sqlite --markets US --out reports/research_plan_us.csv
```

The output includes:
- product identity and market;
- source: TikTok Creative Center, Google Trends UI, or optional YouTube Data API;
- query type, keyword, priority and rationale;
- language and suggested search window;
- a distinct Google Trends comparison_group for each product-market comparison batch.

The planner deduplicates terms within each product-market, uses subtype-specific wording (for example, basketball trainers in GB and shoulder bag vs. mini purse), and caps each Google Trends comparison group at five terms. Never compare interest indices across different comparison_group values, markets, date windows or search types. TikTok and YouTube query tasks are prompts for manual research / optional official API use; no trend count or video count is fabricated.

## 3. Collect and import observations

Use the plan to search each market separately. Record the exact displayed metric, unit, date window, capture timestamp and source URL in the matching manual observation template. For product-specific rows, populate product_id. For broad category observations that cannot honestly be attributed to one product, leave product_id blank.

Import observations:

```powershell
python scripts/import_source_observations.py --db data/registry.sqlite --file configs/manual_research/tiktok_trends.csv
python scripts/import_source_observations.py --db data/registry.sqlite --file configs/manual_research/google_trends.csv
python scripts/audit_source_coverage.py --db data/registry.sqlite
```

The coverage audit is expected to remain incomplete until accepted, non-synthetic observations exist for both required sources in all three markets. That is an intentional quality gate, not an import error.

## Limits

- Query generation does not prove demand or trend velocity.
- Product aliases and local terms must be reviewed by a human; no automatic product identity inference is made.
- The planner does not scrape TikTok, Google Trends, Instagram, Pinterest or Reddit.
- YouTube API collection remains optional and requires a user-provided official API key.
- A missing local market mapping does not suppress the market; it uses the product's core identity and generic market vocabulary.
