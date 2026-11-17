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
) -> None:
    conn.execute(
        """INSERT INTO source_observations(
               observation_id,source_name,observation_type,market_code,product_id,keyword,
               metric_name,metric_value,metric_unit,period_start,period_end,comparison_group,
               source_url,captured_at,collection_method,is_synthetic,validation_status
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            observation_id, source, "trend", market, product_id, "test keyword",
            "trend_rank", 1, "rank", "2026-11-01", period_end, "",
            f"https://ads.tiktok.com/test/{observation_id}",
            "2026-11-16T12:00:00+00:00", "manual_capture", synthetic, "accepted",
        ),
    )


def test_required_sources_ready_only_when_all_markets_have_current_real_data(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
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
    assert {
        tuple((gap["source_name"], *gap["markets_not_current"]))
        for gap in report["required_source_gaps"]
    }
