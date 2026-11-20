# Real source coverage audit

The coverage audit is an acceptance check for supported manual and provider-collected trend evidence. It prevents synthetic examples, stale records, or data from only one market from being mistaken for completed US/CA/GB research.

## Run

    python scripts/audit_source_coverage.py --db data/registry.sqlite

The default acceptance requirement remains current real observations from TikTok Creative Center and Google Trends UI in all three markets: US, CA and GB. A row is current when its period_end is within the last 30 days relative to --as-of. DataForSEO and trendspyg Google Trends observations are displayed separately in the source-market matrix; they do not silently satisfy the default google_trends_ui requirement.

Write a machine-readable report:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --out reports/source_coverage/latest.json

For a reproducible historical audit, provide a fixed date:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --as-of 2026-11-17 --stale-after-days 30

Use --required-sources to change the acceptance set. To require Pinterest as a third source:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --required-sources tiktok_creative_center,google_trends_ui,pinterest_trends_ui

To use the experimental Trendspyg provider instead of the manual Google Trends UI source for a pilot:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --required-sources tiktok_creative_center,trendspyg_google_trends

To require the DataForSEO provider:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --required-sources tiktok_creative_center,dataforseo_google_trends

Supported manual source identifiers: tiktok_creative_center, google_trends_ui, pinterest_trends_ui, reddit_manual and instagram_manual. Supported provider source identifiers: dataforseo_google_trends and trendspyg_google_trends. These remain distinct source IDs with separate coverage rows.

## Interpretation

- current: at least one accepted, non-synthetic observation has a recent period end.
- stale: real observations exist, but the newest period end is older than the freshness cutoff.
- missing: no accepted real observation exists for that source and market.
- synthetic_only: only synthetic observations exist; these never satisfy acceptance.
- product_linked_real_observations: real records with a validated product_id. Aggregate keyword-level observations may legitimately leave product_id blank.

A current status proves only that recent evidence was captured. It does not prove that a sample is representative of all platform activity or that a product is commercially viable.

## Collection policy

- TikTok Creative Center: manually capture public trend pages separately for US, CA and GB, then import with scripts/import_source_observations.py.
- Google Trends UI: export CSV from the public Trends UI for each market; preserve the same comparison group definition within each comparison and import the validated template.
- DataForSEO Google Trends: accepted provider observations are reported under dataforseo_google_trends. Follow the task-post, fetch and billing safeguards in docs/DATA_SOURCE_COLLECTION.md before requiring this source.
- Trendspyg Google Trends: accepted observations are reported under trendspyg_google_trends. This remains an experimental, non-official public-web source; require it explicitly for a pilot and review docs/TRENDSPYG_PROVIDER.md before use.
- Pinterest Trends: manual/approved-source capture is supported by the same observation importer. See docs/PINTEREST_TRENDS_EVIDENCE.md and configs/manual_research/pinterest_keyword_plan.csv. Pinterest is optional in the default gate; include pinterest_trends_ui in --required-sources when you want all three markets to require fresh Pinterest evidence.
- YouTube: the official Data API remains optional and requires its own API key. The project does not use an unofficial scraper or claim that a no-key collector is implemented.
- Reddit and Instagram: keep as manual/approved-source imports until the applicable access terms and authorization are verified.

Do not combine views, post counts, rankings, percentage changes or Google Trends indices as if they were the same unit. Keep each source's native metric, original URL, market and observation window.
