from __future__ import annotations

import argparse
import json
import os

from fashion_radar.collectors.dataforseo_trends import (
    load_query_plan,
    submit_trend_tasks,
)
from fashion_radar.source_store import load_local_env


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or submit Google Trends tasks through DataForSEO Standard mode."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument(
        "--queries",
        default="configs/manual_research/dataforseo_google_trends_pilot.csv",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        help="Maximum billable task posts for this run; defaults to DATAFORSEO_DAILY_TASK_BUDGET or 5.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually submit billable tasks. Without this flag the command is a free dry-run.",
    )
    args = parser.parse_args()
    load_local_env()

    try:
        groups = load_query_plan(args.queries, args.db)
        budget = args.max_tasks or int(os.getenv("DATAFORSEO_DAILY_TASK_BUDGET", "5"))
        if budget < 1:
            raise ValueError("--max-tasks must be >= 1")
        plan = [
            {
                "product_id": group.product_id,
                "market_code": group.market_code,
                "keywords": list(group.keywords),
                "date_from": group.date_from,
                "date_to": group.date_to,
                "search_type": group.search_type,
                "language_code": group.language_code,
                "comparison_group": group.comparison_group,
            }
            for group in groups
        ]
        if not args.execute:
            print(json.dumps({
                "status": "dry_run",
                "database": args.db,
                "query_file": args.queries,
                "billable_tasks": len(groups),
                "daily_safety_budget": budget,
                "estimated_standard_task_cost_usd": round(len(groups) * 0.0027, 4),
                "tasks": plan,
                "note": "No provider request was made. Add --execute to submit tasks.",
            }, ensure_ascii=False, indent=2))
            return 0

        if not os.getenv("DATAFORSEO_LOGIN") or not os.getenv("DATAFORSEO_PASSWORD"):
            raise ValueError(
                "DataForSEO credentials are missing. Run "
                "python scripts/configure_dataforseo_credentials.py first."
            )
        result = submit_trend_tasks(
            args.db, args.queries, max_tasks=budget
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"pending_results", "success"} else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "not_started", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
