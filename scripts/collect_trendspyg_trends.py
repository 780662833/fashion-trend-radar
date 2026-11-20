from __future__ import annotations

import argparse
import json
import os

from fashion_radar.collectors.dataforseo_trends import load_query_plan
from fashion_radar.collectors.trendspyg_trends import collect_trendspyg_trends
from fashion_radar.db import initialize


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or collect Google Trends interest through the optional, "
            "non-official trendspyg public-web provider."
        )
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument(
        "--queries",
        default="configs/manual_research/dataforseo_google_trends_pilot.csv",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        help="Maximum Google public-web requests for this run; defaults to 5 per day.",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        help="Minimum seconds between requests; defaults to 15.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Make live Google Trends public-web requests. Default is a no-network preview.",
    )
    parser.add_argument(
        "--enable-public-web-provider",
        action="store_true",
        help="Acknowledge this is a non-official Google public-web integration.",
    )
    args = parser.parse_args()

    try:
        initialize(args.db)
        groups = load_query_plan(args.queries, args.db)
        budget = (
            args.max_requests
            if args.max_requests is not None
            else int(os.getenv("TRENDSPYG_DAILY_REQUEST_BUDGET", "5"))
        )
        interval = (
            args.min_interval
            if args.min_interval is not None
            else float(os.getenv("TRENDSPYG_MIN_REQUEST_INTERVAL_SECONDS", "15"))
        )
        if budget < 1:
            raise ValueError("--max-requests must be >= 1")
        if interval < 0:
            raise ValueError("--min-interval must be >= 0")
        if len(groups) > budget:
            raise ValueError(
                f"Query plan has {len(groups)} requests, exceeding the run budget {budget}"
            )
        if not args.execute:
            print(json.dumps({
                "status": "dry_run",
                "provider": "trendspyg",
                "database": args.db,
                "query_file": args.queries,
                "requests": len(groups),
                "daily_safety_budget": budget,
                "minimum_interval_seconds": interval,
                "groups": [
                    {
                        "product_id": group.product_id,
                        "market_code": group.market_code,
                        "keywords": list(group.keywords),
                        "date_from": group.date_from,
                        "date_to": group.date_to,
                        "search_type": group.search_type,
                        "comparison_group": group.comparison_group.replace(
                            "dataforseo_", "trendspyg_", 1
                        ),
                        "scale": (
                            "shared within this group"
                            if len(group.keywords) > 1
                            else "single-keyword series; do not compare with other groups"
                        ),
                    }
                    for group in groups
                ],
                "note": (
                    "No network request was made. Live collection requires both "
                    "--execute and --enable-public-web-provider."
                ),
            }, ensure_ascii=False, indent=2))
            return 0

        if not args.enable_public_web_provider:
            raise ValueError(
                "Live collection requires --enable-public-web-provider; review "
                "Google's current terms and data-use rights first."
            )
        result = collect_trendspyg_trends(
            args.db,
            args.queries,
            max_requests=budget,
            min_interval_seconds=interval,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "success" else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({
            "status": "not_started",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
