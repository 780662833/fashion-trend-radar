from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .db import connect, initialize
from .importers import (
    import_assessments,
    import_experiments,
    import_experiment_snapshots,
    import_insights,
    import_metrics,
    import_products,
    import_search,
    import_signals,
)
from .reporting import export_experiment_report, export_report
from .scoring import score_all


def _print_import(result: tuple[int, list[str]]) -> int:
    count, errors = result
    print(f"成功导入/更新：{count} 条")
    if errors:
        print(f"失败：{len(errors)} 条")
        for error in errors[:20]:
            print(f"  - {error}")
        if len(errors) > 20:
            print(f"  ... 另有 {len(errors) - 20} 条错误")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fashion-radar", description="潮鞋与包袋社媒选品研究系统")
    parser.add_argument("--db", default="data/registry.sqlite", help="SQLite 数据库路径")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化数据库")

    for command, help_text in [
        ("import-products", "导入商品主表 CSV"),
        ("import-signals", "导入社媒内容信号 CSV"),
        ("import-metrics", "导入内容指标历史快照 CSV"),
        ("import-search", "导入搜索趋势快照 CSV"),
        ("import-insights", "导入评论/购买意图汇总 CSV"),
        ("import-assessments", "导入人工内容与竞争评估 CSV"),
        ("import-experiments", "导入内容测试记录 CSV"),
        ("import-experiment-snapshots", "导入内容测试表现快照 CSV"),
    ]:
        cmd = sub.add_parser(command, help=help_text)
        cmd.add_argument("--file", required=True)

    score = sub.add_parser("score", help="计算三个目标市场的商品机会分数")
    score.add_argument("--date", default=None, help="评分日期 YYYY-MM-DD，默认今天")

    report = sub.add_parser("report", help="导出当前评分报告")
    report.add_argument("--date", default=None, help="只导出指定评分日期")
    report.add_argument("--out", default="reports/latest")

    experiment_report = sub.add_parser("experiment-report", help="导出内容测试表现报告")
    experiment_report.add_argument("--out", default="reports/experiments")

    status = sub.add_parser("status", help="查看数据库记录数")
    warehouse = sub.add_parser("warehouse-refresh", help="将 SQLite 数据导出为 Parquet 并刷新 DuckDB")
    warehouse.add_argument("--out", default="data/warehouse")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if args.command == "init":
        initialize(db_path)
        print(f"数据库已初始化：{db_path}")
        return 0
    if args.command == "status":
        initialize(db_path)
        tables = ["products", "product_market", "social_signals", "metric_snapshots", "search_snapshots", "audience_insights", "manual_assessments", "scores", "content_experiments", "experiment_snapshots", "sources"]
        with connect(db_path) as conn:
            for table in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"{table}: {count}")
        return 0

    initialize(db_path)
    importers = {
        "import-products": import_products,
        "import-signals": import_signals,
        "import-metrics": import_metrics,
        "import-search": import_search,
        "import-insights": import_insights,
        "import-assessments": import_assessments,
        "import-experiments": import_experiments,
        "import-experiment-snapshots": import_experiment_snapshots,
    }
    if args.command in importers:
        return _print_import(importers[args.command](args.file, db_path))
    if args.command == "score":
        scoring_date = date.fromisoformat(args.date) if args.date else date.today()
        with connect(db_path) as conn:
            results = score_all(conn, scoring_date)
        print(f"评分完成：{len(results)} 条，模型版本 rules-v1.0")
        for result in sorted(results, key=lambda x: x.total_score, reverse=True):
            print(f"{result.product_id} {result.market_code}: {result.total_score:.1f} | coverage={result.data_coverage:.0f}% | grade={result.evidence_grade} | {result.recommendation}")
        return 0
    if args.command == "report":
        paths = export_report(db_path, args.out, args.date)
        print(json.dumps(paths, ensure_ascii=False, indent=2))
        return 0
    if args.command == "experiment-report":
        paths = export_experiment_report(db_path, args.out)
        print(json.dumps(paths, ensure_ascii=False, indent=2))
        return 0
    if args.command == "warehouse-refresh":
        from .warehouse import refresh_warehouse
        paths = refresh_warehouse(db_path, args.out)
        print(json.dumps(paths, ensure_ascii=False, indent=2))
        return 0
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    sys.exit(main())
