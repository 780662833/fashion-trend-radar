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
               VALUES('BAG-001','bag','shoulder','Example','Bag One','Example Bag One')"""
        )
    return db


def _write_pinterest_csv(path: Path, source_url: str) -> None:
    path.write_text(
        "source_name,observation_type,market_code,product_id,keyword,metric_name,metric_value,"
        "metric_unit,period_start,period_end,comparison_group,source_url,captured_at\n"
        "pinterest_trends_ui,trend,GB,BAG-001,slouchy shoulder bag,interest_index,68,"
        "index_0_100,2026-10-01,2026-10-31,GB_bags_oct_2026,"
        f"{source_url},2026-11-16T10:00:00Z\n",
        encoding="utf-8",
    )


def test_manual_pinterest_observation_is_linked_to_product(tmp_path):
    db = _database_with_product(tmp_path)
    csv_path = tmp_path / "pinterest.csv"
    _write_pinterest_csv(csv_path, "https://trends.pinterest.com/")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_source_observations.py"),
         "--db", str(db), "--file", str(csv_path)],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["imported_or_updated"] == 1
    with connect(db) as conn:
        row = conn.execute(
            "SELECT source_name,product_id,market_code,keyword,is_synthetic,validation_status "
            "FROM source_observations"
        ).fetchone()
    assert row["source_name"] == "pinterest_trends_ui"
    assert row["product_id"] == "BAG-001"
    assert row["market_code"] == "GB"
    assert row["keyword"] == "slouchy shoulder bag"
    assert row["is_synthetic"] == 0
    assert row["validation_status"] == "accepted"


def test_pinterest_observation_rejects_non_pinterest_source_url(tmp_path):
    db = _database_with_product(tmp_path)
    csv_path = tmp_path / "pinterest.csv"
    _write_pinterest_csv(csv_path, "https://example.com/not-pinterest")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_source_observations.py"),
         "--db", str(db), "--file", str(csv_path)],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    assert result.returncode == 1
    assert "source_url must be an HTTPS URL on" in result.stdout
    with connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
    assert count == 0
