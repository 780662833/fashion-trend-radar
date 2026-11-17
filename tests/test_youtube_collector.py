from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from fashion_radar.collectors.youtube import QuerySpec, collect_youtube, load_query_plan
from fashion_radar.db import connect, initialize
from fashion_radar.source_store import QuotaBudgetExceeded, ensure_source_tables, reserve_api_request

ROOT = Path(__file__).resolve().parents[1]


class FakeYouTubeClient:
    def __init__(self):
        self.calls = []

    def request(self, resource, params):
        self.calls.append((resource, dict(params)))
        if resource == "search":
            return {
                "items": [{
                    "id": {"videoId": "video-123"},
                    "snippet": {
                        "title": "Sneaker styling ideas",
                        "description": "Everyday fit #sneakerstyle",
                        "publishedAt": "2026-11-15T10:00:00Z",
                        "channelId": "UC_PRIVATE_CHANNEL_ID",
                    },
                }]
            }
        return {
            "items": [{
                "id": "video-123",
                "snippet": {
                    "title": "Sneaker styling ideas",
                    "description": "Everyday fit #sneakerstyle",
                    "publishedAt": "2026-11-15T10:00:00Z",
                    "channelId": "UC_PRIVATE_CHANNEL_ID",
                },
                "statistics": {"viewCount": "1200", "likeCount": "42", "commentCount": "7"},
            }]
        }


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES('SHOE-001','sneaker','basketball','Example','Model One','Example Model One')"""
        )
    query_file = tmp_path / "queries.csv"
    query_file.write_text(
        "product_id,market_code,query,language\n"
        "SHOE-001,US,Example Model One outfit,en\n",
        encoding="utf-8",
    )
    return db, query_file


def test_query_plan_validates_market_and_product(tmp_path):
    db, query_file = _setup(tmp_path)
    plan = load_query_plan(query_file, db)
    assert plan == [QuerySpec("SHOE-001", "US", "Example Model One outfit", "en")]


def test_youtube_collector_persists_signal_metrics_and_quota(tmp_path, monkeypatch):
    db, query_file = _setup(tmp_path)
    monkeypatch.setenv("YOUTUBE_SEARCH_DAILY_BUDGET", "80")
    monkeypatch.setenv("YOUTUBE_OTHER_DAILY_BUDGET", "9000")
    client = FakeYouTubeClient()
    result = collect_youtube(
        db, query_file, "2026-10-17T00:00:00Z", max_results=10, client=client
    )
    assert result.status == "success"
    assert result.items_seen == 1
    assert result.items_saved == 1
    assert [call[0] for call in client.calls] == ["search", "videos"]
    with connect(db) as conn:
        signal = conn.execute(
            "SELECT * FROM social_signals WHERE external_content_id='video-123'"
        ).fetchone()
        metric = conn.execute(
            "SELECT * FROM metric_snapshots WHERE signal_id=?", (signal["signal_id"],)
        ).fetchone()
        run = conn.execute("SELECT * FROM source_runs WHERE run_id=?", (result.run_id,)).fetchone()
        usage = conn.execute(
            "SELECT quota_bucket,COUNT(*) AS n FROM api_request_log GROUP BY quota_bucket"
        ).fetchall()
    assert signal["market_code"] == "US"
    assert signal["source_query"] == "Example Model One outfit"
    assert signal["source_method"] == "youtube_data_api"
    assert signal["creator_key"].startswith("ytc_")
    assert "UC_PRIVATE_CHANNEL_ID" not in signal["creator_key"]
    assert metric["views"] == 1200
    assert metric["likes"] == 42
    assert metric["comments"] == 7
    assert metric["shares"] is None
    assert run["status"] == "success"
    assert len(usage) == 2


def test_collector_keeps_null_stats_if_video_detail_is_missing(tmp_path, monkeypatch):
    db, query_file = _setup(tmp_path)

    class SearchOnlyClient:
        def request(self, resource, params):
            return {
                "items": [{
                    "id": {"videoId": "video-456"},
                    "snippet": {
                        "title": "Bag styling ideas",
                        "description": "",
                        "publishedAt": "2026-11-15T10:00:00Z",
                        "channelId": "UC_CHANNEL",
                    },
                }]
            } if resource == "search" else {"items": []}

    monkeypatch.setenv("YOUTUBE_SEARCH_DAILY_BUDGET", "80")
    result = collect_youtube(db, query_file, "2026-10-17T00:00:00Z", client=SearchOnlyClient())
    assert result.items_saved == 1
    with connect(db) as conn:
        metric_count = conn.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0]
    assert metric_count == 0


def test_search_budget_stops_before_overrun(tmp_path):
    db, _ = _setup(tmp_path)
    with connect(db) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT INTO source_runs(run_id,source_name,status,started_at)
               VALUES('run-1','youtube_data_api','running','2026-11-16T00:00:00Z')"""
        )
        for _ in range(2):
            reserve_api_request(conn, "run-1", "youtube", "search.list", "search_queries", 2)
    with connect(db) as conn:
        with pytest.raises(QuotaBudgetExceeded):
            reserve_api_request(conn, "run-1", "youtube", "search.list", "search_queries", 2)


def test_google_trends_manual_rows_preserve_comparison_group(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES('BAG-001','bag','shoulder_bag','Example','Bag One','Example Bag One')"""
        )
    csv_path = tmp_path / "trends.csv"
    csv_path.write_text(
        "source_name,observation_type,market_code,product_id,keyword,metric_name,metric_value,"
        "metric_unit,period_start,period_end,comparison_group,source_url,captured_at\n"
        "google_trends_ui,search_interest,GB,BAG-001,shoulder bag,interest_index,72,index_0_100,"
        "2026-10-01,2026-10-31,GB_BAGS_2026_10,https://trends.google.com/trends/explore,"
        "2026-11-16T10:00:00Z\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_source_observations.py"),
         "--db", str(db), "--file", str(csv_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with connect(db) as conn:
        row = conn.execute(
            "SELECT interest_index,comparison_group FROM search_snapshots WHERE product_id='BAG-001'"
        ).fetchone()
        observation = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
    assert row["interest_index"] == 72
    assert row["comparison_group"] == "GB_BAGS_2026_10"
    assert observation == 1
