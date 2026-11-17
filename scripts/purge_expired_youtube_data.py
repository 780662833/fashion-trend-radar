from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables, purge_expired_youtube_references


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review or purge expired YouTube Data API reference data."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument(
        "--apply", action="store_true",
        help="Delete expired YouTube API video references. Without this flag, only report the count.",
    )
    args = parser.parse_args()

    initialize(args.db)
    now = datetime.now(timezone.utc)
    with connect(args.db) as conn:
        ensure_source_tables(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM youtube_video_references WHERE expires_at<=?",
            (now.isoformat(),),
        ).fetchone()[0]
        deleted = purge_expired_youtube_references(conn, now) if args.apply else 0
    print(json.dumps({
        "mode": "apply" if args.apply else "dry_run",
        "checked_at": now.isoformat(),
        "expired_rows_found": count,
        "rows_deleted": deleted,
        "remaining_expired_rows": 0 if args.apply else count,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
