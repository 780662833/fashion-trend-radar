from __future__ import annotations

from datetime import date

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables
from scripts.audit_source_coverage import build_coverage_report


def _add_observation(
    conn,
    observation_id: str,
    source: str,
    market: str,
    period_end: str,
    *,
    synthetic: int = 0,
    product_id: str | None = None,
    metric_name: str = "trend_rank",
    metric_value: int = 1,
    metric_unit: str = "rank",
    source_url: str | None = None,
    collection_method: str = "manual_capture",
) -> None:
    conn.execute(
        """INSERT INTO source_observations(
               observation_id,source_name,observation_type,market_code,product_id,keyword,
               metric_name,metric_value,metric_unit,period_start,period_end,comparison_group,
               source_url,captured_at,collection_method,is_synthetic,validation_status
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            observation_id, source, "trend", market, product_id, "test keyword",
            metric_name, metric_value, metric_unit, "2026-11-01", period_end, "",
            source_url or f"https://ads.tiktok.com/test/{observation_id}",
            "2026-11-16T12:00:00+00:00", collection_method, synthetic, "accepted",
        ),
    )


def test_required_sources_ready_only_when_all_markets_have_current_real_data(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES('SHOE-001','sneaker','basketball','Example','Model One','Example Model One')"""
        )
        for source in ("tiktok_creative_center", "google_trends_ui"):
            for market in ("US", "CA", "GB"):
                _add_observation(
                    conn, f"{source}-{market}", source, market, "2026-11-15",
                    product_id="SHOE-001",
                )
        report = build_coverage_report(
            conn, date(2026, 11, 17), stale_after_days=30
        )

    assert report["required_sources_ready"] is True
    assert report["required_source_gaps"] == []
    required_rows = [
        row for row in report["source_market_matrix"]
        if row["source_name"] in report["required_sources"]
    ]
    assert len(required_rows) == 6
    assert {row["status"] for row in required_rows} == {"current"}
    assert all(row["product_linked_real_observations"] == 1 for row in required_rows)


def test_google_trends_provider_sources_are_reported_separately_and_can_be_required(
    tmp_path,
):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
        _add_observation(
            conn,
            "trendspyg-us",
            "trendspyg_google_trends",
            "US",
            "2026-11-18",
            metric_name="interest_index",
            metric_value=55,
            metric_unit="index_0_100",
            source_url="https://trends.google.com/trends/explore?geo=US",
            collection_method="non_official_public_web_library",
        )
        _add_observation(
            conn,
            "dataforseo-ca",
            "dataforseo_google_trends",
            "CA",
            "2026-11-18",
            metric_name="interest_index",
            metric_value=61,
            metric_unit="index_0_100",
            source_url="https://trends.google.com/trends/explore?geo=CA",
            collection_method="dataforseo_api",
        )
        report = build_coverage_report(
            conn,
            date(2026, 11, 20),
            stale_after_days=30,
            required_sources=(
                "trendspyg_google_trends",
                "dataforseo_google_trends",
            ),
        )

    statuses = {
        (row["source_name"], row["market_code"]): row["status"]
        for row in report["source_market_matrix"]
    }
    assert statuses[("trendspyg_google_trends", "US")] == "current"
    assert statuses[("trendspyg_google_trends", "CA")] == "missing"
    assert statuses[("trendspyg_google_trends", "GB")] == "missing"
    assert statuses[("dataforseo_google_trends", "US")] == "missing"
    assert statuses[("dataforseo_google_trends", "CA")] == "current"
    assert statuses[("dataforseo_google_trends", "GB")] == "missing"

    # Provider observations do not get silently re-labelled as manual UI captures.
    assert {
        statuses[(source, market)]
        for source in ("google_trends_ui",)
        for market in ("US", "CA", "GB")
    } == {"missing"}
    assert report["required_sources_ready"] is False
    assert report["required_source_gaps"] == [
        {
            "source_name": "trendspyg_google_trends",
            "markets_not_current": ["CA", "GB"],
        },
        {
            "source_name": "dataforseo_google_trends",
            "markets_not_current": ["US", "GB"],
        },
    ]


def test_synthetic_only_and_stale_rows_do_not_count_as_current_coverage(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
        _add_observation(
            conn, "synthetic-us", "tiktok_creative_center", "US", "2026-11-15",
            synthetic=1,
        )
        _add_observation(
            conn, "stale-ca", "tiktok_creative_center", "CA", "2026-09-01"
        )
        report = build_coverage_report(
            conn, date(2026, 11, 17), stale_after_days=30
        )

    statuses = {
        (row["source_name"], row["market_code"]): row["status"]
        for row in report["source_market_matrix"]
    }
    assert statuses[("tiktok_creative_center", "US")] == "synthetic_only"
    assert statuses[("tiktok_creative_center", "CA")] == "stale"
    assert statuses[("tiktok_creative_center", "GB")] == "missing"
    assert report["required_sources_ready"] is False
    assert report["required_source_gaps"] == [
        {
            "source_name": "tiktok_creative_center",
            "markets_not_current": ["US", "CA", "GB"],
        },
        {
            "source_name": "google_trends_ui",
            "markets_not_current": ["US", "CA", "GB"],
        },
    ]
