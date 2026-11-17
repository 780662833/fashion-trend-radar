from datetime import date
from pathlib import Path

from fashion_radar.db import connect, initialize
from fashion_radar.importers import import_metrics, import_products, import_search, import_signals, import_insights, import_assessments, import_experiments, import_experiment_snapshots
from fashion_radar.reporting import export_experiment_report, export_report
from fashion_radar.scoring import score_all

ROOT = Path(__file__).resolve().parents[1]


def test_demo_pipeline_marks_synthetic_and_exports(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    assert import_products(ROOT / "examples/products.csv", db)[0] == 2
    assert import_signals(ROOT / "examples/signals.csv", db)[0] == 6
    assert import_metrics(ROOT / "examples/metrics.csv", db)[0] == 13
    assert import_search(ROOT / "examples/search.csv", db)[0] == 12
    assert import_search(ROOT / "examples/search.csv", db)[0] == 12
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM search_snapshots").fetchone()[0] == 12
    assert import_insights(ROOT / "examples/insights.csv", db)[0] == 4
    assert import_insights(ROOT / "examples/insights.csv", db)[0] == 4
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audience_insights").fetchone()[0] == 4
    assert import_assessments(ROOT / "examples/assessments.csv", db)[0] == 2
    assert import_experiments(ROOT / "examples/experiments.csv", db)[0] == 2
    assert import_experiment_snapshots(ROOT / "examples/experiment_snapshots.csv", db)[0] == 4
    results = score_all_in_test(db)
    assert results
    assert all("模拟数据" in item.recommendation for item in results)
    assert all(item.evidence_grade == "C" for item in results)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM experiment_snapshots").fetchone()[0] == 4
    paths = export_report(db, tmp_path / "report")
    assert all(Path(path).exists() for path in paths.values())
    experiment_paths = export_experiment_report(db, tmp_path / "experiment-report")
    assert all(Path(path).exists() for path in experiment_paths.values())
    assert "is_synthetic_result" in Path(experiment_paths["json"]).read_text(encoding="utf-8")


def score_all_in_test(db):
    from fashion_radar.db import connect

    with connect(db) as conn:
        return score_all(conn, date(2026, 11, 16))


def test_missing_market_metrics_are_not_fabricated(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    import_products(ROOT / "examples/products.csv", db)
    import_signals(ROOT / "examples/signals.csv", db)
    with connect(db) as conn:
        results = score_all(conn, date(2026, 11, 16))
    sneaker_us = next(x for x in results if x.product_id == "DEMO-SNEAKER-001" and x.market_code == "US")
    assert sneaker_us.component_scores["content_potential"] is None
    assert sneaker_us.component_scores["competition_gap"] is None
    assert sneaker_us.data_coverage < 100
    assert sneaker_us.evidence_grade == "C"
    assert "模拟数据" in sneaker_us.recommendation


def test_product_alias_resolver_uses_confidence_gate(tmp_path):
    from fashion_radar.resolver import resolve_product_name

    db = tmp_path / "registry.sqlite"
    initialize(db)
    import_products(ROOT / "examples/products.csv", db)
    with connect(db) as conn:
        match = resolve_product_name(conn, "sample sneaker")
    assert match.product_id == "DEMO-SNEAKER-001"
    assert match.confidence >= 82
    assert not match.ambiguous


def test_real_evidence_can_reach_grade_b_without_synthetic_data(tmp_path):
    from datetime import datetime, timezone

    db = tmp_path / "registry.sqlite"
    initialize(db)
    import_products(ROOT / "examples/products.csv", db)
    with connect(db) as conn:
        for i in range(5):
            platform = "youtube" if i < 3 else "tiktok"
            conn.execute(
                """INSERT INTO social_signals(signal_id,product_id,platform,market_code,content_url,
                   published_at,observed_at,content_type,creator_key,source_method,data_quality,is_synthetic)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"REAL-{i}", "DEMO-SNEAKER-001", platform, "US", f"https://example.invalid/real/{i}",
                 f"2026-11-{12+i:02d}T12:00:00+00:00", "2026-11-16T12:00:00+00:00", "styling",
                 f"creator-{i}", "official_api", 1.0, 0),
            )
        results = score_all(conn, date(2026, 11, 16))
    result = next(x for x in results if x.product_id == "DEMO-SNEAKER-001" and x.market_code == "US")
    assert result.evidence_grade == "B"
    assert result.data_coverage >= 60
    assert "模拟数据" not in result.recommendation


def test_insight_sample_cannot_be_smaller_than_mentions(tmp_path):
    from fashion_radar.models import AudienceInsightInput
    from pydantic import ValidationError

    try:
        AudienceInsightInput(
            product_id="DEMO-SNEAKER-001", market_code="US", topic="price",
            intent_type="price_question", mention_count=5, sample_size=2,
        )
    except ValidationError:
        return
    raise AssertionError("invalid mention_count/sample_size pair was accepted")


def test_csv_empty_optional_fields_use_defaults_or_nulls(tmp_path):
    db = tmp_path / "registry.sqlite"
    initialize(db)
    csv_path = tmp_path / "products.csv"
    csv_path.write_text(
        "product_id,category,subcategory,brand,model_name,canonical_name,style_code,aliases,release_date\n"
        "TEST-SHOE-01,sneaker,lifestyle,Test Brand,Model A,Model A,,,\n",
        encoding="utf-8",
    )
    count, errors = import_products(csv_path, db)
    assert count == 1
    assert not errors
    with connect(db) as conn:
        row = conn.execute("SELECT aliases,release_date,style_code FROM products WHERE product_id='TEST-SHOE-01'").fetchone()
    assert row["aliases"] == ""
    assert row["release_date"] is None
    assert row["style_code"] is None
