from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


CANDIDATES = {
    "video_url": ("url", "video_url", "share_url", "post_url"),
    "hashtag": ("hashtag", "tag", "keyword", "topic"),
    "creator": ("creator", "username", "author", "account"),
    "views": ("views", "view_count", "plays"),
    "likes": ("likes", "like_count"),
    "comments": ("comments", "comment_count"),
    "shares": ("shares", "share_count"),
    "date": ("date", "created_at", "published_at", "timestamp"),
    "country": ("country", "region", "market"),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect an Exolyt CSV export without importing or exposing row contents."
    )
    parser.add_argument("--file", required=True, help="CSV exported from Exolyt.")
    parser.add_argument("--out", help="Optional JSON report path.")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(json.dumps({
            "status": "failed",
            "error": f"CSV does not exist: {path.resolve()}",
        }, ensure_ascii=False, indent=2))
        return 2

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        columns = reader.fieldnames or []
        normalized = {column.strip().casefold(): column for column in columns}
        matches = {
            semantic: next(
                (normalized[candidate] for candidate in candidates if candidate in normalized),
                None,
            )
            for semantic, candidates in CANDIDATES.items()
        }
        row_count = sum(1 for row in reader if any(str(v or "").strip() for v in row.values()))

    report = {
        "status": "inspected",
        "file": str(path.resolve()),
        "sha256": digest,
        "rows": row_count,
        "delimiter": dialect.delimiter,
        "columns": columns,
        "candidate_column_matches": matches,
        "imported": False,
        "note": (
            "This is schema discovery only. Exolyt exports vary by report type; "
            "do not import until the actual export fields, metric semantics, market scope, "
            "period and source URLs have been reviewed."
        ),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
