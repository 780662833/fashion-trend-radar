from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables

ROOT = Path(__file__).resolve().parents[1]


def _seed(db: Path) -> None:
    initialize(db)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name,
               style_code,aliases,audience_tag,identity_confidence)
               VALUES('SHOE-001','sneaker','basketball','Nike','Air Jordan 4','Air Jordan 4 Black Cat',
                      'CU1110-010','AJ4 Black Cat;Jordan 4 Black Cat','unisex',0.98)"""
        )
        ensure_source_tables(conn)
        conn.executemany(
            """INSERT INTO product_market(product_id,market_code,currency,market_status)
               VALUES('SHOE-001',?,?, 'watching')""",
            [("US", "USD"), ("CA", "CAD"), ("GB", "GBP")],
        )


def test_product_evidence_import_and_catalog_audit_ready(tmp_path):
    db = tmp_path / "registry.sqlite"
    _seed(db)
    evidence_csv = tmp_path / "evidence.csv"
    evidence_csv.write_text(
        "product_id,evidence_type,source_url,captured_at,is_primary,notes\n"
        "SHOE-001,official_product_page,https://www.nike.com/t/air-jordan-4-black-cat,"
        "2026-11-16T12:00:00Z,true,Official product identity reference\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_product_evidence.py"),
         "--db", str(db), "--file", str(evidence_csv)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert json.loads(imported.stdout)["imported_or_updated"] == 1

    report_path = tmp_path / "catalog_audit.json"
    audited = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_product_catalog.py"),
         "--db", str(db), "--out", str(report_path)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert audited.returncode == 0, audited.stdout + audited.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "ready"
    assert report["product_count"] == 1
    assert report["blocker_count"] == 0
    assert report["products"][0]["primary_evidence_count"] == 1
    assert report["warning_count"] == 0


def test_catalog_audit_blocks_ambiguous_alias_and_missing_evidence(tmp_path):
    db = tmp_path / "registry.sqlite"
    _seed(db)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name,
               aliases,identity_confidence)
               VALUES('SHOE-002','sneaker','basketball','Nike','Jordan 4 Retro','Jordan 4 Retro Red',
                      'AJ4 Black Cat',0.95)"""
        )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_product_catalog.py"), "--db", str(db)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    codes = {issue["code"] for issue in report["issues"]}
    assert "ambiguous_alias" in codes
    assert "missing_product_evidence" in codes


def test_product_evidence_import_rejects_placeholder_urls(tmp_path):
    db = tmp_path / "registry.sqlite"
    _seed(db)
    evidence_csv = tmp_path / "bad_evidence.csv"
    evidence_csv.write_text(
        "product_id,evidence_type,source_url\n"
        "SHOE-001,official_product_page,https://example.invalid/product\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_product_evidence.py"),
         "--db", str(db), "--file", str(evidence_csv)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert result.returncode == 1
    assert "placeholder URLs cannot be product evidence" in result.stdout
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_evidence").fetchone()[0] == 0
