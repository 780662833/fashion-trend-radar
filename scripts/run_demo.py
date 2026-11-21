from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fashion_radar.db import connect, initialize
from fashion_radar.importers import (
    import_assessments,
    import_experiment_snapshots,
    import_experiments,
    import_insights,
    import_metrics,
    import_products,
    import_search,
    import_signals,
)
from fashion_radar.reporting import export_experiment_report, export_report
from fashion_radar.scoring import score_all


def main() -> int:
    parser = argparse.ArgumentParser(description="加载合成演示数据并运行完整 MVP 流程")
    parser.add_argument("--db", default="data/demo_registry.sqlite")
    parser.add_argument("--out", default="reports/demo")
    parser.add_argument("--score-date", default="2026-11-16")
    args = parser.parse_args()

    db = Path(args.db)
    initialize(db)
    imports = [
        ("products", import_products),
        ("signals", import_signals),
        ("metrics", import_metrics),
        ("search", import_search),
        ("insights", import_insights),
        ("assessments", import_assessments),
        ("experiments", import_experiments),
        ("experiment_snapshots", import_experiment_snapshots),
    ]
    for name, importer in imports:
        count, errors = importer(ROOT / "examples" / f"{name}.csv", db)
        print(f"{name}: {count} imported")
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1

    with connect(db) as conn:
        results = score_all(conn, date.fromisoformat(args.score_date))
    print(f"scores: {len(results)}")
    print("Note: all example rows are synthetic. Do not use these scores as market evidence.")
    print("report:")
    for key, value in export_report(db, args.out, args.score_date).items():
        print(f"  {key}: {value}")
    print("content experiments:")
    for key, value in export_experiment_report(db, args.out).items():
        print(f"  {key}: {value}")
    try:
        from fashion_radar.warehouse import refresh_warehouse
        print("warehouse:")
        for key, value in refresh_warehouse(db, Path(args.out) / "warehouse").items():
            print(f"  {key}: {value}")
    except RuntimeError as exc:
        print(f"warehouse 未运行：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
