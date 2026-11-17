from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from fashion_radar.collectors.youtube import load_query_plan, probe_youtube_api
from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Fashion Trend Radar data-source readiness.")
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--queries", default="configs/youtube_queries.csv")
    parser.add_argument("--probe-youtube", action="store_true",
                        help="Perform one quota-counted, read-only YouTube search request.")
    args = parser.parse_args()

    initialize(args.db)
    with connect(args.db) as conn:
        ensure_source_tables(conn)
        product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        source_count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        run_count = conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0]

    report = {
        "database": str(args.db),
        "database_initialized": True,
        "products_in_database": product_count,
        "registered_sources": source_count,
        "source_runs_recorded": run_count,
        "youtube_api_key_configured": bool(os.getenv("YOUTUBE_API_KEY")),
        "youtube_queries_file": str(args.queries),
        "youtube_query_plan": "not_checked",
        "youtube_query_rows": 0,
        "manual_import_templates": {
            "tiktok": Path("configs/manual_research/tiktok_trends_template.csv").exists(),
            "google_trends": Path("configs/manual_research/google_trends_template.csv").exists(),
        },
        "probe": None,
        "warnings": [],
    }

    try:
        query_plan = load_query_plan(args.queries, args.db)
        report["youtube_query_plan"] = "valid"
        report["youtube_query_rows"] = len(query_plan)
    except (ValueError, OSError) as exc:
        report["youtube_query_plan"] = "invalid_or_missing"
        report["warnings"].append(str(exc))

    if not report["youtube_api_key_configured"]:
        report["warnings"].append(
            "YOUTUBE_API_KEY is not configured. Store it in the local environment; never commit it."
        )
    if args.probe_youtube:
        if not report["youtube_api_key_configured"]:
            report["warnings"].append("--probe-youtube requested but YOUTUBE_API_KEY is missing.")
            report["probe"] = {"status": "not_run", "reason": "missing_api_key"}
        else:
            report["probe"] = probe_youtube_api(args.db)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["youtube_query_plan"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
