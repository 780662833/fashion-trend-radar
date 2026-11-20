from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables

MARKETS = ("US", "CA", "GB")
MANUAL_SOURCES = (
    "tiktok_creative_center",
    "google_trends_ui",
    "pinterest_trends_ui",
    "reddit_manual",
    "instagram_manual",
)
PROVIDER_SOURCES = (
    "dataforseo_google_trends",
    "trendspyg_google_trends",
)
COVERAGE_SOURCES = (*MANUAL_SOURCES, *PROVIDER_SOURCES)


def build_coverage_report(
    conn,
    as_of: date,
    stale_after_days: int = 30,
    required_sources: tuple[str, ...] = ("tiktok_creative_center", "google_trends_ui"),
) -> dict:
    if stale_after_days < 0:
        raise ValueError("stale_after_days must be >= 0")
    unknown = sorted(set(required_sources) - set(COVERAGE_SOURCES))
    if unknown:
        raise ValueError(f"unsupported required source(s): {', '.join(unknown)}")

    rows = conn.execute(
        """SELECT source_name, market_code,
                  COUNT(*) AS accepted_count,
                  SUM(CASE WHEN is_synthetic=0 THEN 1 ELSE 0 END) AS real_count,
                  SUM(CASE WHEN is_synthetic=1 THEN 1 ELSE 0 END) AS synthetic_count,
                  SUM(CASE WHEN is_synthetic=0 AND product_id IS NOT NULL THEN 1 ELSE 0 END)
                      AS product_linked_real_count,
                  COUNT(DISTINCT CASE WHEN is_synthetic=0 THEN product_id END)
                      AS distinct_products,
                  MAX(CASE WHEN is_synthetic=0 THEN period_end END) AS latest_real_period_end,
                  MAX(CASE WHEN is_synthetic=0 THEN captured_at END) AS latest_real_capture
           FROM source_observations
           WHERE validation_status='accepted'
           GROUP BY source_name, market_code"""
    ).fetchall()
    indexed = {(row["source_name"], row["market_code"]): row for row in rows}
    cutoff = (as_of - timedelta(days=stale_after_days)).isoformat()

    matrix = []
    for source in COVERAGE_SOURCES:
        for market in MARKETS:
            row = indexed.get((source, market))
            real_count = int(row["real_count"] or 0) if row else 0
            synthetic_count = int(row["synthetic_count"] or 0) if row else 0
            latest_period_end = row["latest_real_period_end"] if row else None
            if real_count == 0:
                status = "synthetic_only" if synthetic_count else "missing"
            elif latest_period_end < cutoff:
                status = "stale"
            else:
                status = "current"
            matrix.append({
                "source_name": source,
                "market_code": market,
                "status": status,
                "accepted_observations": int(row["accepted_count"] or 0) if row else 0,
                "real_observations": real_count,
                "synthetic_observations": synthetic_count,
                "product_linked_real_observations": (
                    int(row["product_linked_real_count"] or 0) if row else 0
                ),
                "distinct_products": int(row["distinct_products"] or 0) if row else 0,
                "latest_real_period_end": latest_period_end,
                "latest_real_capture": row["latest_real_capture"] if row else None,
            })

    required_gaps = []
    for source in required_sources:
        missing_markets = [
            item["market_code"] for item in matrix
            if item["source_name"] == source and item["status"] != "current"
        ]
        if missing_markets:
            required_gaps.append({
                "source_name": source,
                "markets_not_current": missing_markets,
            })

    return {
        "as_of": as_of.isoformat(),
        "stale_after_days": stale_after_days,
        "freshness_cutoff": cutoff,
        "markets": list(MARKETS),
        "required_sources": list(required_sources),
        "required_sources_ready": not required_gaps,
        "required_source_gaps": required_gaps,
        "source_market_matrix": matrix,
        "notes": [
            "Only accepted, non-synthetic source observations count as real coverage.",
            "DataForSEO and trendspyg Google Trends observations are reported as separate provider sources; neither is merged into google_trends_ui.",
            "Trendspyg is a non-official public-web integration; listing its observations does not establish ongoing access rights or representativeness.",
            "YouTube Data API references are stored separately and are not included in this source-observation matrix.",
            "A current row indicates recent captured evidence, not that the sample is statistically representative.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit accepted real-data coverage for manual trend sources across US/CA/GB."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--as-of", default=date.today().isoformat(),
                        help="Reference date in YYYY-MM-DD format; defaults to today.")
    parser.add_argument("--stale-after-days", type=int, default=30)
    parser.add_argument(
        "--required-sources",
        default="tiktok_creative_center,google_trends_ui",
        help="Comma-separated sources that must have current real observations in all markets.",
    )
    parser.add_argument("--out", help="Optional path for a JSON report.")
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.as_of)
        required_sources = tuple(
            value.strip() for value in args.required_sources.split(",") if value.strip()
        )
        initialize(args.db)
        with connect(args.db) as conn:
            ensure_source_tables(conn)
            report = build_coverage_report(
                conn, as_of, args.stale_after_days, required_sources
            )
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    report["status"] = "ready" if report["required_sources_ready"] else "incomplete"
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["required_sources_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
