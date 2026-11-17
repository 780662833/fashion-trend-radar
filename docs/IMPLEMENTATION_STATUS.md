# Implementation status — v0.1.0

## Implemented in this package

- SQLite schema for products, market mapping, source registry, social signals, metric snapshots, search snapshots, audience insights, manual assessments, scores, content experiments, and experiment snapshots.
- Pydantic validation for CSV rows and timezone normalization.
- Idempotent CSV upsert for product, social signal, metrics, search snapshots, audience insights, assessments, and content experiment records.
- Conservative fuzzy product alias resolver with an ambiguity gate.
- Rules-based `rules-v1.0` scoring by product and market.
- Evidence grade, data coverage, reasons, and limitations persisted with score version.
- JSON/CSV/Markdown selection report.
- Content experiment metrics report.
- Read-only Streamlit ranking/score detail app.
- Parquet/DuckDB export module, gated behind the optional `warehouse` extra.
- Synthetic examples and unit tests.

## Verified in this build environment

- `python -m pytest -q`: 6 passed.
- `python -m compileall -q src app scripts tests`: passed.
- Installed editable core package with `pip install -e . --no-build-isolation`; command entry point `fashion-radar` ran successfully.
- `scripts/run_demo.py`: imported all example data, scored five product-market pairs, and exported selection and content-experiment reports.
- CLI status: confirmed 2 products, 6 signals, 13 metric snapshots, 12 search snapshots, 4 audience insight rows, 2 assessments, 2 content experiments, 4 experiment snapshots, 5 scores, and 6 source registry rows.

## Not verified here

- Polars + DuckDB warehouse export: optional dependencies are not installed in this environment; install `.[warehouse]` before running `warehouse-refresh`.
- Streamlit UI runtime: Streamlit is not installed in this environment; install `.[app]` before launching it.
- Live platform connectors: intentionally not implemented yet. Real data ingestion currently uses CSV imports; YouTube and other authorized collectors remain a later integration step.
- No real US/CA/GB trend data is included. Demo rows are synthetic and explicitly marked.

## Next engineering phase

1. Run Phase 1 product master QA using a curated real product candidate list.
2. Define reproducible query sets by category, brand, product family, gender/audience and market.
3. Add an authorized YouTube Data API collector with daily metric snapshots and quota accounting.
4. Add a repeatable manual-research import template for TikTok Creative Center and Google Trends.
5. Validate product resolver behavior on real sneaker style codes and bag model names before automatic linking.
6. Run the 4-week system-vs-human content experiment before adjusting scoring weights.


## Data-source collection and validation phase

Implemented on branch feat/data-source-collection-validation:

- Official YouTube Data API collector for bounded product/market query plans, with one search page per query and explicit search/video request quota buckets.
- Individual YouTube reference records isolated in youtube_video_references; they are not written to product-scoring social_signals or metric_snapshots.
- 30-day expiry and a dry-run-first purge script for non-authorized YouTube API references.
- Source-run and API-request ledgers for audit and quota validation.
- Manual source-observation importer with market, date window, source URL, metric unit and validation checks.
- Google Trends comparison_group preservation; score calculation compares only within the same keyword, source and comparison group.
- Official-source domain checks for TikTok Creative Center, Google Trends, Pinterest Trends, Reddit and Instagram manual evidence.
- Unit tests for collection mapping, quota budgets, YouTube isolation, retention, source URL validation and comparison-group boundaries.

Not yet verified:

- No live YouTube API request has been made in this environment because no API key was provided.
- TikTok Creative Center and Google Trends import workflows are implemented as manual capture/import, not automated collectors.
- Reddit, Instagram and Pinterest live API collectors are not implemented.
- The YouTube collector is API-contract tested with a mocked client; actual account quota and key restrictions still need local validation.
