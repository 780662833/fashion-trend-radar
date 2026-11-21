from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from fashion_radar.db import connect, initialize

CURRENCIES = {"US": "USD", "CA": "CAD", "GB": "GBP"}
MARKETS = set(CURRENCIES)


def _parse(row: dict[str, str], line: int) -> dict:
    product_id = (row.get("product_id") or "").strip()
    market = (row.get("market_code") or "").strip().upper()
    currency = (row.get("currency") or "").strip().upper()
    if not product_id:
        raise ValueError(f"row {line}: product_id is required")
    if market not in MARKETS:
        raise ValueError(f"row {line}: market_code must be US, CA or GB")
    if currency != CURRENCIES[market]:
        raise ValueError(f"row {line}: {market} currency must be {CURRENCIES[market]}")
    raw_price = (row.get("local_price") or "").strip()
    price = None
    if raw_price:
        try:
            price = float(raw_price)
        except ValueError:
            raise ValueError(f"row {line}: local_price must be numeric") from None
        if not math.isfinite(price) or price < 0:
            raise ValueError(f"row {line}: local_price must be finite and non-negative")
    status = (row.get("market_status") or "watching").strip().lower()
    if status not in {"watching", "active", "paused"}:
        raise ValueError(f"row {line}: market_status must be watching, active or paused")
    observed = (row.get("observed_at") or "").strip()
    if observed:
        try:
            parsed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"row {line}: observed_at must be ISO 8601") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        observed = parsed.astimezone(UTC).isoformat()
    else:
        observed = datetime.now(UTC).isoformat()
    return {
        "product_id": product_id,
        "market_code": market,
        "currency": currency,
        "local_price": price,
        "market_search_terms": (row.get("market_search_terms") or "").strip(),
        "market_status": status,
        "source_url": (row.get("source_url") or "").strip(),
        "observed_at": observed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import product-market mappings and local query terms.")
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--file", required=True)
    args = parser.parse_args()
    initialize(args.db)
    inserted = 0
    errors: list[str] = []
    with Path(args.file).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"product_id", "market_code", "currency"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            print(json.dumps({"status": "failed", "missing_columns": sorted(missing)}, indent=2))
            return 2
        with connect(args.db) as conn:
            for line, row in enumerate(reader, start=2):
                try:
                    item = _parse(row, line)
                    exists = conn.execute(
                        "SELECT 1 FROM products WHERE product_id=?", (item["product_id"],)
                    ).fetchone()
                    if not exists:
                        raise ValueError(f"row {line}: unknown product_id {item['product_id']!r}")
                    conn.execute(
                        """INSERT INTO product_market(product_id,market_code,currency,local_price,
                           market_search_terms,market_status,source_url,observed_at)
                           VALUES(?,?,?,?,?,?,?,?)
                           ON CONFLICT(product_id,market_code) DO UPDATE SET
                           currency=excluded.currency,local_price=excluded.local_price,
                           market_search_terms=excluded.market_search_terms,
                           market_status=excluded.market_status,source_url=excluded.source_url,
                           observed_at=excluded.observed_at""",
                        tuple(item[key] for key in (
                            "product_id", "market_code", "currency", "local_price",
                            "market_search_terms", "market_status", "source_url", "observed_at",
                        )),
                    )
                    inserted += 1
                except ValueError as exc:
                    errors.append(str(exc))
    print(json.dumps({
        "status": "success" if not errors else "partial",
        "imported_or_updated": inserted,
        "error_count": len(errors),
        "errors": errors[:50],
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
