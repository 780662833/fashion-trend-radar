from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fashion_radar.db import connect, initialize

ROOT = Path(__file__).resolve().parents[1]


def _database_with_product(tmp_path: Path) -> Path:
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES('SHOE-001','sneaker','basketball','Example','Model One','Example Model One')"""
        )
    return db


def _write_tiktok_csv(path: Path, product_id: str) -> None:
    path.write_text(
        "source_name,observation_type,market_code,product_id,keyword,metric_name,metric_value,"
        "metric_unit,period_start,period_end,source_url,captured_at\n"
        "tiktok_creative_center,hashtag,US,"
        f"{product_id},examplemodel,video_count,125,posts,2026-11-01,2026-11-07,"
        "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en,"
        "2026-11-16T10:00:00Z\n",
        encoding="utf-8",
    )


def test_manual_tiktok_observation_is_linked_to_product(tmp_path):
    db = _database_with_product(tmp_path)
    csv_path = tmp_path / "tiktok.csv"
    _write_tiktok_csv(csv_path, "SHOE-001")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_source_observations.py"),
         "--db", str(db), "--file", str(csv_path)],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["imported_or_updated"] == 1

    with connect(db) as conn:
        row = conn.execute(
            "SELECT product_id,market_code,keyword,is_synthetic FROM source_observations"
        ).fetchone()
    assert row["product_id"] == "SHOE-001"
    assert row["market_code"] == "US"
    assert row["keyword"] == "examplemodel"
    assert row["is_synthetic"] == 0


def test_manual_observation_rejects_unknown_product_id(tmp_path):
    db = _database_with_product(tmp_path)
    csv_path = tmp_path / "tiktok.csv"
    _write_tiktok_csv(csv_path, "MISSING-PRODUCT")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_source_observations.py"),
         "--db", str(db), "--file", str(csv_path)],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    assert result.returncode == 1
    assert "unknown product_id 'MISSING-PRODUCT'" in result.stdout
    with connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
    assert count == 0
