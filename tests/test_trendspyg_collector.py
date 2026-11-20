from __future__ import annotations

from types import SimpleNamespace

import pytest

from fashion_radar.collectors.trendspyg_trends import (
    TrendspygClient,
    collect_trendspyg_trends,
    normalize_trendspyg_points,
)
from fashion_radar.db import connect, initialize

PRODUCT_ID = "PILOT-KOBE-9"


def _database(path):
    initialize(path)
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES(?, 'sneaker','basketball','Nike','Kobe 9 EM Low Protro',
                      'Kobe 9 EM Low Protro Philippines')""",
            (PRODUCT_ID,),
        )
    return path


def _query_file(path, search_type="web"):
    path.write_text(
        "product_id,market_code,comparison_set,keyword,date_from,date_to,search_type,language_code\n"
        f"{PRODUCT_ID},US,exact_phrase,Kobe 9 EM Low Protro Philippines,"
        f"2026-08-19,2026-11-19,{search_type},en\n"
        f"{PRODUCT_ID},US,related_terms,Kobe 9 Philippines,"
        f"2026-08-19,2026-11-19,{search_type},en\n"
        f"{PRODUCT_ID},US,related_terms,Kobe 9 Protro Philippines,"
        f"2026-08-19,2026-11-19,{search_type},en\n",
        encoding="utf-8",
    )
    return path


def test_client_single_keyword_uses_http_without_cookies():
    captured = {}

    def download(keyword, **kwargs):
        captured["keyword"] = keyword
        captured.update(kwargs)
        return [{"date": "2026-11-18T00:00:00+00:00", "value": 37, "is_partial": False}]

    client = TrendspygClient(SimpleNamespace(
        download_google_trends_interest_over_time=download
    ))
    from fashion_radar.collectors.dataforseo_trends import TrendQueryGroup

    group = TrendQueryGroup(
        product_id=PRODUCT_ID, market_code="US",
        keywords=("Kobe 9 Philippines",), date_from="2026-11-01",
        date_to="2026-11-30", search_type="web", language_code="en",
        comparison_set="single", comparison_group="dataforseo_US_web_single",
    )
    result = client.fetch_group(group)

    assert captured["keyword"] == "Kobe 9 Philippines"
    assert captured["geo"] == "US"
    assert captured["timeframe"] == "2026-11-01 2026-11-30"
    assert captured["engine"] == "http"
    assert captured["cookies"] is False
    assert captured["cache"] is False
    assert captured["archive"] is False
    assert captured["max_retries"] == 1
    assert result[0]["values"] == {"Kobe 9 Philippines": 37}


def test_client_uses_one_shared_scale_for_two_to_five_keywords():
    captured = {}

    def compare(keywords, **kwargs):
        captured["keywords"] = keywords
        captured.update(kwargs)
        return {
            "interest_over_time": [{
                "date": "2026-11-18T00:00:00+00:00",
                "values": {keywords[0]: 40, keywords[1]: 80},
                "is_partial": False,
            }]
        }

    client = TrendspygClient(SimpleNamespace(download_google_trends_comparison=compare))
    from fashion_radar.collectors.dataforseo_trends import TrendQueryGroup

    group = TrendQueryGroup(
        product_id=PRODUCT_ID, market_code="CA",
        keywords=("Kobe 9", "Kobe 9 Protro"), date_from="2026-11-01",
        date_to="2026-11-30", search_type="web", language_code="en",
        comparison_set="related", comparison_group="dataforseo_CA_web_related",
    )
    points = client.fetch_group(group)

    assert captured["keywords"] == ["Kobe 9", "Kobe 9 Protro"]
    assert captured["geo"] == "CA"
    assert captured["engine"] == "http"
    assert captured["cookies"] is False
    assert captured["include_geo"] is False
    assert points[0]["values"] == {"Kobe 9": 40, "Kobe 9 Protro": 80}


def test_normalization_keeps_zero_and_skips_partial_points():
    from fashion_radar.collectors.dataforseo_trends import TrendQueryGroup

    group = TrendQueryGroup(
        product_id=PRODUCT_ID, market_code="GB",
        keywords=("Kobe 9", "Kobe 9 Protro"), date_from="2026-11-01",
        date_to="2026-11-30", search_type="web", language_code="en",
        comparison_set="related", comparison_group="dataforseo_GB_web_related",
    )
    rows, missing = normalize_trendspyg_points(
        group,
        [
            {
                "date": "2026-11-08T00:00:00+00:00",
                "values": {"Kobe 9": 0, "Kobe 9 Protro": 65},
                "is_partial": False,
            },
            {
                "date": "2026-11-15T00:00:00+00:00",
                "values": {"Kobe 9": 30, "Kobe 9 Protro": 70},
                "is_partial": True,
            },
        ],
        captured_at="2026-11-20T12:00:00+00:00",
    )

    assert len(rows) == 2
    assert missing == 2
    zero = next(row for row in rows if row["keyword"] == "Kobe 9")
    assert zero["metric_value"] == 0
    assert zero["comparison_group"].startswith("trendspyg_")
    assert zero["period_start"] == zero["period_end"] == "2026-11-08"
    assert "non_official_public_web" in zero["notes"]
    assert "zero_may_mean_insufficient_search_data" in zero["notes"]
    assert zero["collection_method"] == "non_official_public_web_library"


class FakeTrendspygClient:
    def __init__(self):
        self.groups = []

    def fetch_group(self, group):
        self.groups.append(group)
        point_date = "2026-11-18T00:00:00+00:00"
        return [{
            "date": point_date,
            "values": {
                keyword: (0 if keyword == "Kobe 9 EM Low Protro Philippines" else 75)
                for keyword in group.keywords
            },
            "is_partial": False,
        }]


def test_collect_persists_provenance_and_isolates_comparison_groups(tmp_path):
    db = _database(tmp_path / "registry.sqlite")
    query_file = _query_file(tmp_path / "queries.csv")
    client = FakeTrendspygClient()

    result = collect_trendspyg_trends(
        db, query_file, client=client, max_requests=5, min_interval_seconds=0
    )

    assert result["status"] == "success"
    assert result["planned_requests"] == 2
    assert result["requests_completed"] == 2
    assert result["observations_saved"] == 3
    assert len(client.groups) == 2
    with connect(db) as conn:
        observations = conn.execute(
            """SELECT keyword,metric_value,comparison_group,source_url,notes,
                      collection_method,is_synthetic,validation_status
               FROM source_observations ORDER BY keyword"""
        ).fetchall()
        snapshots = conn.execute(
            """SELECT keyword,comparison_group,interest_index
               FROM search_snapshots ORDER BY keyword"""
        ).fetchall()
        requests = conn.execute(
            "SELECT provider,method,succeeded FROM api_request_log ORDER BY request_id"
        ).fetchall()
        run = conn.execute(
            "SELECT status,items_seen,items_saved FROM source_runs WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()

    assert len(observations) == 3
    assert len({row["comparison_group"] for row in observations}) == 2
    assert all(row["source_url"].startswith("https://trends.google.com/trends/explore?") for row in observations)
    assert all(row["collection_method"] == "non_official_public_web_library" for row in observations)
    assert all(row["is_synthetic"] == 0 for row in observations)
    assert all(row["validation_status"] == "accepted" for row in observations)
    assert all(row["provider"] == "trendspyg" and row["succeeded"] == 1 for row in requests)
    assert run["status"] == "success"
    assert run["items_seen"] == 2
    assert run["items_saved"] == 3
    assert len(snapshots) == 3


def test_budget_blocks_before_any_provider_request(tmp_path):
    db = _database(tmp_path / "registry.sqlite")
    query_file = _query_file(tmp_path / "queries.csv")
    client = FakeTrendspygClient()

    with pytest.raises(ValueError, match="exceeding the daily safety budget"):
        collect_trendspyg_trends(
            db, query_file, client=client, max_requests=1, min_interval_seconds=0
        )

    assert client.groups == []


def test_provider_failure_stops_the_run_without_retry_or_identity_rotation(tmp_path):
    db = _database(tmp_path / "registry.sqlite")
    query_file = _query_file(tmp_path / "queries.csv")

    class RateLimitedClient:
        def __init__(self):
            self.calls = 0

        def fetch_group(self, group):
            self.calls += 1
            raise RuntimeError("RateLimitError: HTTP 429")

    client = RateLimitedClient()
    result = collect_trendspyg_trends(
        db, query_file, client=client, max_requests=5, min_interval_seconds=0
    )

    assert result["status"] == "failed"
    assert client.calls == 1
    assert result["error_count"] == 1
    with connect(db) as conn:
        attempts = conn.execute(
            "SELECT COUNT(*) FROM api_request_log WHERE provider='trendspyg'"
        ).fetchone()[0]
        failures = conn.execute(
            "SELECT COUNT(*) FROM api_request_log WHERE provider='trendspyg' AND succeeded=0"
        ).fetchone()[0]
    assert attempts == failures == 1


def test_non_web_search_type_is_rejected_before_provider_call(tmp_path):
    db = _database(tmp_path / "registry.sqlite")
    query_file = _query_file(tmp_path / "queries.csv", search_type="youtube")
    client = FakeTrendspygClient()

    with pytest.raises(ValueError, match="Google web search only"):
        collect_trendspyg_trends(
            db, query_file, client=client, max_requests=5, min_interval_seconds=0
        )

    assert client.groups == []
