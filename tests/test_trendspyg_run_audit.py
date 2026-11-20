from __future__ import annotations

import importlib.util
from pathlib import Path

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import (
    ensure_source_tables,
    finish_api_request,
    finish_source_run,
    link_source_run_observation,
    reserve_api_request,
    start_source_run,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_trendspyg_run",
    ROOT / "scripts" / "audit_trendspyg_run.py",
)
assert SPEC is not None and SPEC.loader is not None
AUDIT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT_MODULE)


def _database_with_run(tmp_path: Path, *, link_observation: bool) -> tuple[Path, str]:
    db = tmp_path / "registry.sqlite"
    initialize(db)
    run_id = "test-run-001"
    observation_id = "observation-001"
    started_at = "2026-11-20T09:34:31+00:00"
    finished_at = "2026-11-20T09:34:46+00:00"

    with connect(db) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT INTO products(
                   product_id,category,subcategory,brand,model_name,canonical_name
               ) VALUES('TEST-SHOE','sneaker','basketball','Nike','Kobe 9','Kobe 9')"""
        )
        start_source_run(
            conn, run_id, "trendspyg_google_trends", "US", 1,
            {"provider": "trendspyg"},
        )
        conn.execute(
            "UPDATE source_runs SET started_at=? WHERE run_id=?",
            (started_at, run_id),
        )
        conn.execute(
            """INSERT INTO source_observations(
                   observation_id,source_name,observation_type,market_code,product_id,
                   keyword,metric_name,metric_value,metric_unit,period_start,period_end,
                   comparison_group,source_url,captured_at,collection_method,
                   is_synthetic,validation_status
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                observation_id,
                "trendspyg_google_trends",
                "trend",
                "US",
                "TEST-SHOE",
                "Kobe 9",
                "interest_index",
                25.0,
                "index_0_100",
                "2026-11-19",
                "2026-11-19",
                "trendspyg_US_web_related",
                "https://trends.google.com/trends/explore?q=Kobe+9",
                started_at,
                "non_official_public_web_library",
                0,
                "accepted",
            ),
        )
        if link_observation:
            link_source_run_observation(conn, run_id, observation_id)

        request_id = reserve_api_request(
            conn, run_id, "trendspyg", "google_trends.explore",
            "public_web_requests", 5,
        )
        finish_api_request(conn, request_id, True)
        finish_source_run(
            conn, run_id, "success", items_seen=1, items_saved=1,
            errors=[],
            metadata={"observations_saved": 1},
        )
        conn.execute(
            "UPDATE source_runs SET finished_at=? WHERE run_id=?",
            (finished_at, run_id),
        )

    return db, run_id


def test_audit_uses_direct_run_observation_links(tmp_path):
    db, run_id = _database_with_run(tmp_path, link_observation=True)

    with connect(db) as conn:
        result = AUDIT_MODULE.audit_run(conn, run_id)

    assert result["status"] == "checks_consistent"
    audit = result["observation_audit"]
    assert audit["association_method"] == "source_run_observations"
    assert audit["linked_observation_count"] == 1
    assert audit["matching_observation_count"] == 1
    assert result["checks"]["run_observation_links_match_items_saved"] is True


def test_audit_does_not_treat_legacy_time_window_candidates_as_run_links(tmp_path):
    db, run_id = _database_with_run(tmp_path, link_observation=False)

    with connect(db) as conn:
        result = AUDIT_MODULE.audit_run(conn, run_id)

    assert result["status"] == "legacy_unlinked"
    audit = result["observation_audit"]
    assert audit["association_method"] == "legacy_time_window_unverified"
    assert audit["linked_observation_count"] == 0
    assert audit["legacy_time_window_candidate_count"] == 1
    assert audit["matching_observation_count"] == 1
    assert result["checks"]["run_observation_links_match_items_saved"] is False
