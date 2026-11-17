from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from fashion_radar.db import connect, initialize

ROOT = Path(__file__).resolve().parents[1]


def _seed_product(db: Path) -> None:
    initialize(db)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name,
               style_code,aliases,audience_tag)
               VALUES('BAG-001','bag','shoulder_bag','Example','Mini Shoulder','Example Mini Shoulder',
                      'EX-001','Example Mini Bag;Mini Shoulder Purse','women')"""
        )


def test_product_market_import_and_research_plan_are_market_specific(tmp_path):
    db = tmp_path / "registry.sqlite"
    _seed_product(db)
    market_csv = tmp_path / "markets.csv"
    market_csv.write_text(
        "product_id,market_code,currency,market_search_terms,market_status\n"
        "BAG-001,US,USD,mini purse;everyday shoulder bag,watching\n"
        "BAG-001,GB,GBP,small handbag;crossbody styling,active\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_product_markets.py"),
         "--db", str(db), "--file", str(market_csv)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert json.loads(imported.stdout)["imported_or_updated"] == 2

    plan_path = tmp_path / "query_plan.csv"
    generated = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_research_plan.py"),
         "--db", str(db), "--out", str(plan_path)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    summary = json.loads(generated.stdout)
    assert summary["products"] == 1
    assert summary["product_market_pairs"] == 3

    with plan_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["market_code"] for row in rows} == {"US", "CA", "GB"}
    assert any(row["keyword"] == "mini purse" and row["market_code"] == "US" for row in rows)
    assert any(row["keyword"] == "small handbag" and row["market_code"] == "GB" for row in rows)
    assert any(row["keyword"] == "Example Mini Shoulder shoulder bag" and row["market_code"] == "GB" for row in rows)
    assert all(row["product_id"] == "BAG-001" for row in rows)

    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["source_name"] == "google_trends_ui":
            groups[row["comparison_group"]].append(row["keyword"])
    assert groups
    assert all(1 <= len(keywords) <= 5 for keywords in groups.values())
    assert all("web_12m_g" in group_id for group_id in groups)


def test_product_market_import_rejects_currency_mismatch(tmp_path):
    db = tmp_path / "registry.sqlite"
    _seed_product(db)
    market_csv = tmp_path / "invalid_markets.csv"
    market_csv.write_text(
        "product_id,market_code,currency,market_search_terms\n"
        "BAG-001,GB,USD,small handbag\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_product_markets.py"),
         "--db", str(db), "--file", str(market_csv)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert result.returncode == 1
    assert "GB currency must be GBP" in result.stdout
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_market").fetchone()[0] == 0



def test_paused_product_market_is_excluded_from_plan(tmp_path):
    db = tmp_path / "registry.sqlite"
    _seed_product(db)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO product_market(product_id,market_code,currency,market_status)
               VALUES('BAG-001','US','USD','paused')"""
        )
    plan_path = tmp_path / "query_plan.csv"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_research_plan.py"),
         "--db", str(db), "--markets", "US,GB", "--out", str(plan_path)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with plan_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {row["market_code"] for row in rows} == {"GB"}



def test_uk_basketball_sneaker_queries_use_trainers_vocabulary(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES('SHOE-001','sneaker','basketball','Example','Court One','Example Court One')"""
        )
    plan_path = tmp_path / "uk_plan.csv"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_research_plan.py"),
         "--db", str(db), "--markets", "GB", "--out", str(plan_path)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with plan_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["keyword"] == "Example Court One basketball trainers" for row in rows)
