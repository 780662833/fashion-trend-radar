from __future__ import annotations

import json
from pathlib import Path

from fashion_radar.collectors.tiktok_embedded import collect_tiktok_embedded
from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables
from scripts.audit_tiktok_embedded_run import audit_run


class FakeGuestClient:
    async def fetch_seed(self, seed_type, seed_value, market_code, max_items):
        assert (seed_type, seed_value, market_code, max_items) == (
            "hashtag", "kobeshoes", "US", 5
        )
        return [{
            "content_id": "7450000000000000001",
            "content_url": (
                "https://www.tiktok.com/@creator/video/7450000000000000001"
            ),
            "published_at": "2026-11-17T10:00:00+00:00",
            "content_type": "video",
            "caption": "Kobe shoes on court",
            "hashtags": ["kobeshoes", "basketball"],
            "author_id": "author-123",
            "author_handle": "creator",
            "views": 12000,
            "likes": 800,
            "comments": 24,
            "shares": 16,
            "saves": 31,
        }]


def _live_run(tmp_path: Path) -> tuple[Path, str]:
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT INTO products(
               product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES('SHOE-001','sneaker','basketball','Nike','Kobe 9','Nike Kobe 9')"""
        )
    seed_file = tmp_path / "tiktok_seeds.csv"
    seed_file.write_text(
        "market_code,seed_type,seed_value,suggested_product_id,max_items\n"
        "US,hashtag,kobeshoes,SHOE-001,5\n",
        encoding="utf-8",
    )
    result = collect_tiktok_embedded(db, seed_file, client=FakeGuestClient())
    assert result.status == "success"
    assert result.items_saved == 1

    # Model one completed provider request so the audit exercises the live-run
    # ledger contract without making a network request in the test suite.
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO api_request_log(
               run_id,provider,method,quota_bucket,quota_date_pt,quota_units,
               attempted_at,finished_at,succeeded,error_code
            ) VALUES(?,?,?,?,?,?,?,?,1,'')""",
            (
                result.run_id,
                "tiktok_dtk_embedded",
                "tiktok.hashtag_posts",
                "anonymous_guest",
                "2026-11-20",
                1,
                "2026-11-20T12:00:00+00:00",
                "2026-11-20T12:00:01+00:00",
            ),
        )
        row = conn.execute(
            "SELECT metadata_json FROM source_runs WHERE run_id=?",
            (result.run_id,),
        ).fetchone()
        metadata = json.loads(row["metadata_json"])
        metadata["requests_made"] = 1
        conn.execute(
            "UPDATE source_runs SET metadata_json=? WHERE run_id=?",
            (json.dumps(metadata), result.run_id),
        )
    return db, result.run_id


def test_tiktok_run_audit_accepts_consistent_live_run_and_confirms_isolation(tmp_path):
    db, run_id = _live_run(tmp_path)
    with connect(db) as conn:
        report = audit_run(conn, run_id)

    assert report["status"] == "checks_consistent"
    assert report["source_run"]["status"] == "success"
    assert report["source_run"]["seed_count"] == 1
    assert report["source_run"]["items_seen"] == 1
    assert report["source_run"]["items_saved"] == 1
    assert report["candidate_audit"]["candidate_count"] == 1
    assert report["candidate_audit"]["needs_review_count"] == 1
    assert report["candidate_audit"]["research_context_count"] == 1
    assert report["api_request_log"]["request_count"] == 1
    assert report["api_request_log"]["succeeded_count"] == 1
    assert all(report["checks"].values())
    assert report["candidate_audit"]["unreviewed_candidate_social_signal_matches"] == 0
    assert report["candidate_audit"]["unreviewed_candidate_metric_snapshot_matches"] == 0
    assert report["samples"][0]["external_content_id"] == "7450000000000000001"
    assert "caption" not in report["samples"][0]
    assert "creator_handle" not in report["samples"][0]


def test_tiktok_run_audit_flags_unreviewed_candidate_in_scoring_tables(tmp_path):
    db, run_id = _live_run(tmp_path)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO social_signals(
               signal_id,product_id,platform,market_code,external_content_id,
               content_url,published_at,observed_at,content_type,caption,hashtags,
               creator_key,source_method
            ) VALUES(
               'AUTO-PROMOTED','SHOE-001','tiktok','US','7450000000000000001',
               'https://www.tiktok.com/@creator/video/7450000000000000001',
               '2026-11-17T10:00:00+00:00','2026-11-20T12:00:00+00:00',
               'video','Kobe shoes on court','kobeshoes,basketball',
               'ttc_test','embedded_guest'
            )"""
        )
        conn.execute(
            """INSERT INTO metric_snapshots(
               signal_id,snapshot_at,views,likes,comments,shares,saves,metric_source
            ) VALUES(
               'AUTO-PROMOTED','2026-11-20T12:00:00+00:00',12000,800,24,16,31,
               'tiktok_embedded'
            )"""
        )
        report = audit_run(conn, run_id)

    assert report["status"] == "mismatch"
    assert report["checks"]["unreviewed_candidates_not_in_social_signals"] is False
    assert report["checks"]["unreviewed_candidates_have_no_metric_snapshots"] is False
    assert report["candidate_audit"]["unreviewed_candidate_social_signal_matches"] == 1
    assert report["candidate_audit"]["unreviewed_candidate_metric_snapshot_matches"] == 1


def test_tiktok_run_audit_rejects_a_different_source_run(tmp_path):
    db, run_id = _live_run(tmp_path)
    with connect(db) as conn:
        conn.execute(
            "UPDATE source_runs SET source_name='youtube_data_api' WHERE run_id=?",
            (run_id,),
        )
        report = audit_run(conn, run_id)

    assert report["status"] == "source_mismatch"
    assert report["expected_source_name"] == "tiktok_dtk_embedded"
