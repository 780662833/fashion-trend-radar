from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from fashion_radar.collectors.youtube import QuerySpec, collect_youtube, load_query_plan
from fashion_radar.db import connect, initialize
from fashion_radar.scoring import score_all
from fashion_radar.source_store import (
    QuotaBudgetExceeded,
    ensure_source_tables,
    purge_expired_youtube_references,
    reserve_api_request,
)

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


def test_youtube_collector_keeps_api_data_out_of_product_scoring(tmp_path, monkeypatch):
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
        reference = conn.execute(
            "SELECT * FROM youtube_video_references WHERE video_id='video-123'"
        ).fetchone()
        social_signal_count = conn.execute("SELECT COUNT(*) FROM social_signals").fetchone()[0]
        metric_count = conn.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0]
        run = conn.execute("SELECT * FROM source_runs WHERE run_id=?", (result.run_id,)).fetchone()
        usage = conn.execute(
            "SELECT quota_bucket,COUNT(*) AS n FROM api_request_log GROUP BY quota_bucket"
        ).fetchall()
    assert reference["market_code"] == "US"
    assert reference["search_query"] == "Example Model One outfit"
    assert reference["title"] == "Sneaker styling ideas"
    assert reference["view_count"] == 1200
    assert reference["like_count"] == 42
    assert reference["comment_count"] == 7
    assert reference["expires_at"] > reference["observed_at"]
    assert social_signal_count == 0
    assert metric_count == 0
    assert run["status"] == "success"
    assert len(usage) == 2


def test_collector_keeps_missing_youtube_statistics_null(tmp_path, monkeypatch):
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
        reference = conn.execute(
            "SELECT view_count,like_count,comment_count FROM youtube_video_references WHERE video_id='video-456'"
        ).fetchone()
        metric_count = conn.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0]
    assert reference["view_count"] is None
    assert reference["like_count"] is None
    assert reference["comment_count"] is None
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
    with connect(db) as conn, pytest.raises(QuotaBudgetExceeded):
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


def test_manual_source_import_rejects_non_official_source_domain(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    csv_path = tmp_path / "bad_source.csv"
    csv_path.write_text(
        "source_name,observation_type,market_code,keyword,metric_name,metric_value,"
        "metric_unit,period_start,period_end,source_url,captured_at\n"
        "tiktok_creative_center,trend,US,sneaker,views,100,views,2026-11-01,2026-11-07,"
        "https://not-tiktok.example/trends,2026-11-16T10:00:00Z\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_source_observations.py"),
         "--db", str(db), "--file", str(csv_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "source_url must be an HTTPS URL on" in result.stdout
    with connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
    assert count == 0


def test_youtube_reference_retention_purges_expired_rows_only(tmp_path):
    db, _ = _setup(tmp_path)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO youtube_video_references(reference_id,product_id,market_code,video_id,
               search_query,content_url,published_at,observed_at,expires_at,title)
               VALUES('old','SHOE-001','US','old-video','shoe','https://youtu.be/old',
               '2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00',
               '2026-10-31T00:00:00+00:00','Old title')"""
        )
        conn.execute(
            """INSERT INTO youtube_video_references(reference_id,product_id,market_code,video_id,
               search_query,content_url,published_at,observed_at,expires_at,title)
               VALUES('fresh','SHOE-001','US','fresh-video','shoe','https://youtu.be/fresh',
               '2026-11-15T00:00:00+00:00','2026-11-15T00:00:00+00:00',
               '2026-12-15T00:00:00+00:00','Fresh title')"""
        )
        deleted = purge_expired_youtube_references(
            conn, datetime(2026, 11, 16, tzinfo=UTC)
        )
        remaining = conn.execute("SELECT video_id FROM youtube_video_references").fetchall()
    assert deleted == 1
    assert [row["video_id"] for row in remaining] == ["fresh-video"]


def test_search_growth_never_crosses_google_trends_comparison_groups(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES('BAG-002','bag','tote','Example','Bag Two','Example Bag Two')"""
        )
        conn.execute(
            """INSERT INTO search_snapshots(product_id,market_code,keyword,period_start,period_end,
               interest_index,source,captured_at,is_synthetic,comparison_group)
               VALUES('BAG-002','US','tote bag','2026-10-01','2026-10-07',25,'google_trends_ui',
               '2026-10-08T00:00:00Z',0,'US_TOTE_GROUP_A')"""
        )
        conn.execute(
            """INSERT INTO search_snapshots(product_id,market_code,keyword,period_start,period_end,
               interest_index,source,captured_at,is_synthetic,comparison_group)
               VALUES('BAG-002','US','tote bag','2026-10-08','2026-10-14',90,'google_trends_ui',
               '2026-10-15T00:00:00Z',0,'US_TOTE_GROUP_B')"""
        )
        result = next(
            item for item in score_all(conn, date(2026, 11, 16))
            if item.product_id == "BAG-002" and item.market_code == "US"
        )
    assert result.component_scores["demand_evidence"] is None
    assert any("搜索趋势只有单期记录" in note for note in result.limitations)



def test_local_env_loads_missing_values_without_overwriting_existing(monkeypatch, tmp_path):
    from fashion_radar.source_store import load_local_env

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# local-only credentials\nYOUTUBE_API_KEY=\"local-key\"\nOTHER_VALUE=ok\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "caller-key")
    monkeypatch.delenv("OTHER_VALUE", raising=False)
    assert load_local_env(str(env_file))
    import os
    assert os.getenv("YOUTUBE_API_KEY") == "caller-key"
    assert os.getenv("OTHER_VALUE") == "ok"
