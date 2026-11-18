# Real source coverage audit

The coverage audit is a read-only acceptance check for manually captured trend evidence. It prevents synthetic examples, stale records, or data from only one market from being mistaken for completed US/CA/GB research.

## Run

    python scripts/audit_source_coverage.py --db data/registry.sqlite

The default acceptance requirement is current real observations from TikTok Creative Center and Google Trends in all three markets: US, CA and GB. A row is current when its period_end is within the last 30 days relative to --as-of.

Write a machine-readable report:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --out reports/source_coverage/latest.json

For a reproducible historical audit, provide a fixed date:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --as-of 2026-11-17 --stale-after-days 30

Use --required-sources to change the acceptance set. To require Pinterest as a third source:

    python scripts/audit_source_coverage.py --db data/registry.sqlite --required-sources tiktok_creative_center,google_trends_ui,pinterest_trends_ui

Supported manual source identifiers include tiktok_creative_center, google_trends_ui, pinterest_trends_ui, reddit_manual and instagram_manual.

## Interpretation

- current: at least one accepted, non-synthetic observation has a recent period end.
- stale: real observations exist, but the newest period end is older than the freshness cutoff.
- missing: no accepted real observation exists for that source and market.
- synthetic_only: only synthetic observations exist; these never satisfy acceptance.
- product_linked_real_observations: real records with a validated product_id. Aggregate keyword-level observations may legitimately leave product_id blank.

A current status proves only that recent evidence was captured. It does not prove that a sample is representative of all platform activity or that a product is commercially viable.

## Collection policy

- TikTok Creative Center: manually capture public trend pages separately for US, CA and GB, then import with scripts/import_source_observations.py.
- Google Trends: export CSV from the public Trends UI for each market; preserve the same comparison group definition within each comparison and import the validated template.
- Pinterest Trends: manual/approved-source capture is supported by the same observation importer. See docs/PINTEREST_TRENDS_EVIDENCE.md and configs/manual_research/pinterest_keyword_plan.csv. Pinterest is optional in the default gate; include pinterest_trends_ui in --required-sources when you want all three markets to require fresh Pinterest evidence.
- YouTube: the official Data API remains optional and requires its own API key. The project does not use an unofficial scraper or claim that a no-key collector is implemented.
- Reddit and Instagram: keep as manual/approved-source imports until the applicable access terms and authorization are verified.

Do not combine views, post counts, rankings, percentage changes or Google Trends indices as if they were the same unit. Keep each source's native metric, original URL, market and observation window.
