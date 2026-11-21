from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from fashion_radar.collectors.youtube import collect_youtube
from fashion_radar.source_store import load_local_env


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect public YouTube video search signals through the official YouTube Data API."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--queries", default="configs/youtube_queries.csv")
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--max-results", type=int, default=25)
    args = parser.parse_args()
    load_local_env()
    if args.days_back < 1 or args.days_back > 365:
        parser.error("--days-back must be between 1 and 365")

    published_after = (
        datetime.now(UTC) - timedelta(days=args.days_back)
    ).isoformat().replace("+00:00", "Z")
    try:
        result = collect_youtube(
            db_path=args.db,
            query_file=args.queries,
            published_after=published_after,
            max_results=args.max_results,
        )
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "not_started", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    payload = {
        "status": result.status,
        "run_id": result.run_id,
        "queries": result.queries,
        "items_seen": result.items_seen,
        "items_saved": result.items_saved,
        "errors": result.errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
