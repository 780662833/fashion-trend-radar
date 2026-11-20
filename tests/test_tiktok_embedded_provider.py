from __future__ import annotations

from pathlib import Path

import pytest

from fashion_radar.collectors.tiktok_embedded import (
    TikTokSeedError,
    collect_tiktok_embedded,
    load_seed_plan,
)
from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables


class FakeGuestClient:
    async def fetch_seed(self, seed_type, seed_value, market_code, max_items):
        assert seed_type == "hashtag"
        assert seed_value == "kobeshoes"
        assert market_code == "US"
        assert max_items == 5
        return [
            {
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
            }
        ]


def _setup(tmp_path: Path) -> tuple[Path, Path]:
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
    return db, seed_file


def test_seed_plan_validates_seed_types_markets_and_product_ids(tmp_path):
    db, seed_file = _setup(tmp_path)
    seeds = load_seed_plan(seed_file, db)
    assert seeds[0].suggested_product_id == "SHOE-001"

    seed_file.write_text(
        "market_code,seed_type,seed_value\nUS,keyword,kobe 9\n",
        encoding="utf-8",
    )
    with pytest.raises(TikTokSeedError, match="seed_type"):
        load_seed_plan(seed_file, db)


def test_embedded_guest_collection_stages_candidates_without_scoring(tmp_path):
    db, seed_file = _setup(tmp_path)
    result = collect_tiktok_embedded(
        db, seed_file, client=FakeGuestClient()
    )
    assert result.status == "success"
    assert result.seeds == 1
    assert result.items_seen == 1
    assert result.items_saved == 1
    assert result.requests_made == 0

    with connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM tiktok_content_candidates"
        ).fetchone()
        social_count = conn.execute(
            "SELECT COUNT(*) FROM social_signals"
        ).fetchone()[0]
        metric_count = conn.execute(
            "SELECT COUNT(*) FROM metric_snapshots"
        ).fetchone()[0]
        source = conn.execute(
            "SELECT * FROM source_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()

    assert row["market_code"] == "US"
    assert row["market_basis"] == "research_context"
    assert row["audience_market_code"] is None
    assert row["suggested_product_id"] == "SHOE-001"
    assert row["link_status"] == "needs_review"
    assert row["views"] == 12000
    assert row["likes"] == 800
    assert row["creator_key"].startswith("ttc_")
    assert social_count == 0
    assert metric_count == 0
    assert source["status"] == "success"
    assert '"identity_mode": "anonymous_guest_no_cookies"' in source["metadata_json"]


def test_seed_file_rejects_keyword_search(tmp_path):
    db, seed_file = _setup(tmp_path)
    seed_file.write_text(
        "market_code,seed_type,seed_value\nUS,keyword,retro basketball shoes\n",
        encoding="utf-8",
    )
    with pytest.raises(TikTokSeedError, match="seed_type"):
        load_seed_plan(seed_file, db)


def test_candidate_requires_tiktok_content_url(tmp_path):
    from fashion_radar.collectors.tiktok_embedded import (
        TikTokSeed,
        _persist_candidate,
    )

    db, _ = _setup(tmp_path)
    with connect(db) as conn:
        ensure_source_tables(conn)
        inserted = _persist_candidate(
            conn,
            "run",
            TikTokSeed("US", "video_id", "123"),
            {"content_id": "123", "content_url": "https://not-tiktok.example/video/123"},
            "2026-11-20T00:00:00+00:00",
        )
    assert inserted is False


def test_optional_provider_module_import_does_not_require_dtk():
    # Importing the main project or its provider declarations must remain safe
    # on Python 3.11, where the optional DTK package is not installable.
    from fashion_radar.providers.tiktok import DTKEmbeddedClient

    assert DTKEmbeddedClient.__name__ == "DTKEmbeddedClient"


def test_pinned_dtk_client_constructs_offline_on_supported_python():
    import asyncio
    import sys

    if sys.version_info < (3, 12) or sys.version_info >= (3, 14):
        pytest.skip("the optional DTK extra supports Python 3.12 and 3.13")

    from dtk.platforms.tiktok.endpoints import SEARCH_POSTS
    from fashion_radar.providers.tiktok.dtk_embedded import (
        DTKEmbeddedClient,
        GUEST_ENDPOINTS,
        _load_dtk_modules,
    )

    modules = _load_dtk_modules()
    adapter = modules["ADAPTER"]
    assert adapter.platform.value == "tiktok"

    for endpoint in GUEST_ENDPOINTS:
        spec = adapter.endpoints.specs[endpoint]
        assert getattr(spec.login_for({}), "value", None) == "none"
        assert not spec.session_bound

    search_spec = adapter.endpoints.specs[SEARCH_POSTS]
    assert getattr(
        search_spec.login_for({"keyword": "basketball shoes"}), "value", None
    ) == "required"

    client = DTKEmbeddedClient(max_requests_per_run=1)
    assert client.identity_mode == "anonymous_guest_no_cookies"
    assert client.requests_made == 0
    asyncio.run(client.aclose())
