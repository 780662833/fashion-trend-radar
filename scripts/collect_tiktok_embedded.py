from __future__ import annotations

import argparse
import json

from fashion_radar.collectors.tiktok_embedded import (
    collect_tiktok_embedded,
    load_seed_plan,
)
from fashion_radar.db import initialize
from fashion_radar.source_store import load_local_env


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect a bounded set of TikTok public guest posts through the "
            "embedded DTK provider. This is not an official TikTok API."
        )
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument(
        "--seeds",
        default="configs/manual_research/tiktok_embedded_seeds.csv",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Make network requests; default is preview only.",
    )
    parser.add_argument(
        "--enable-public-web-provider",
        action="store_true",
        help="Explicitly opt in to the experimental non-official public-web provider.",
    )
    args = parser.parse_args()
    load_local_env()

    try:
        initialize(args.db)
        seeds = load_seed_plan(args.seeds, args.db)
    except (ValueError, OSError) as exc:
        print(
            json.dumps(
                {"status": "not_started", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "preview",
                    "network_requests": 0,
                    "seeds": [
                        {
                            "market_code": seed.market_code,
                            "seed_type": seed.seed_type,
                            "seed_value": seed.seed_value,
                            "suggested_product_id": seed.suggested_product_id,
                            "max_items": seed.max_items,
                        }
                        for seed in seeds
                    ],
                    "message": (
                        "No network calls were made. Add --execute and "
                        "--enable-public-web-provider only after reviewing "
                        "current platform terms and data-use rights."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.enable_public_web_provider:
        parser.error(
            "--execute also requires --enable-public-web-provider"
        )

    try:
        result = collect_tiktok_embedded(args.db, args.seeds)
    except (ValueError, OSError, RuntimeError) as exc:
        print(
            json.dumps(
                {"status": "not_started", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "status": result.status,
                "run_id": result.run_id,
                "seeds": result.seeds,
                "items_seen": result.items_seen,
                "items_saved": result.items_saved,
                "requests_made": result.requests_made,
                "errors": result.errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
