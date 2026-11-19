from __future__ import annotations

import argparse
import json
import os

from fashion_radar.collectors.dataforseo_trends import fetch_trend_task_results
from fashion_radar.source_store import load_local_env


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch completed DataForSEO Google Trends Standard tasks and persist observations."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", help="Fetch all pending tasks submitted by a run.")
    group.add_argument("--task-id", help="Fetch one DataForSEO task by provider task ID.")
    args = parser.parse_args()
    load_local_env()

    if not os.getenv("DATAFORSEO_LOGIN") or not os.getenv("DATAFORSEO_PASSWORD"):
        print(json.dumps({
            "status": "not_started",
            "error": "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not configured.",
        }, ensure_ascii=False, indent=2))
        return 2
    try:
        result = fetch_trend_task_results(
            args.db, run_id=args.run_id, task_id=args.task_id
        )
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"success", "pending_results"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
