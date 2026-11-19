from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables

ALLOWED_SOURCES = {
    "tiktok_creative_center",
    "google_trends_ui",
    "dataforseo_google_trends",
    "pinterest_trends_ui",
    "reddit_manual",
    "instagram_manual",
}
MARKETS = {"US", "CA", "GB"}


SOURCE_DOMAINS = {
    "tiktok_creative_center": {"ads.tiktok.com"},
    "google_trends_ui": {"trends.google.com"},
    "dataforseo_google_trends": {"trends.google.com"},
    "pinterest_trends_ui": {"trends.pinterest.com", "pinterest.com"},
    "reddit_manual": {"reddit.com"},
    "instagram_manual": {"instagram.com"},
}


def _valid_url(value: str, required: bool = True, allowed_domains: set[str] | None = None) -> bool:
    if not value and not required:
        return True
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    if not allowed_domains:
        return True
    hostname = parsed.hostname.lower()
    return any(hostname == domain or hostname.endswith("." + domain) for domain in allowed_domains)


def _parse_row(row: dict[str, str], line: int) -> dict:
    source = (row.get("source_name") or "").strip()
    market = (row.get("market_code") or "").strip().upper()
    keyword = (row.get("keyword") or "").strip()
    metric_name = (row.get("metric_name") or "").strip()
    metric_unit = (row.get("metric_unit") or "").strip()
    comparison_group = (row.get("comparison_group") or "").strip()
    source_url = (row.get("source_url") or "").strip()
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"row {line}: unsupported source_name {source!r}")
    if market not in MARKETS:
        raise ValueError(f"row {line}: market_code must be US, CA or GB")
    if not keyword or not metric_name or not metric_unit:
        raise ValueError(f"row {line}: keyword, metric_name and metric_unit are required")
    if not _valid_url(source_url, allowed_domains=SOURCE_DOMAINS[source]):
        expected = ", ".join(sorted(SOURCE_DOMAINS[source]))
        raise ValueError(f"row {line}: source_url must be an HTTPS URL on: {expected}")
    supporting_url = (row.get("supporting_content_url") or "").strip()
    if not _valid_url(supporting_url, required=False):
        raise ValueError(f"row {line}: supporting_content_url must be https when supplied")
    try:
        value = float(row.get("metric_value") or "")
        if not math.isfinite(value) or value < 0:
            raise ValueError
    except ValueError:
        raise ValueError(f"row {line}: metric_value must be a finite non-negative number") from None
    try:
        start = date.fromisoformat(row["period_start"].strip()).isoformat()
        end = date.fromisoformat(row["period_end"].strip()).isoformat()
        captured = datetime.fromisoformat(
            (row.get("captured_at") or "").strip().replace("Z", "+00:00")
        )
    except (KeyError, ValueError):
        raise ValueError(f"row {line}: invalid period_start, period_end or captured_at") from None
    if end < start:
        raise ValueError(f"row {line}: period_end is earlier than period_start")
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    if source in {"google_trends_ui", "dataforseo_google_trends"}:
        if metric_name != "interest_index" or metric_unit != "index_0_100":
            raise ValueError(f"row {line}: Google Trends must use interest_index/index_0_100")
        if value > 100:
            raise ValueError(f"row {line}: Google Trends interest_index must be within 0-100")
        if not comparison_group:
            raise ValueError(f"row {line}: Google Trends comparison_group is required")
    return {
        "source_name": source,
        "observation_type": (row.get("observation_type") or "trend").strip(),
        "market_code": market,
        "product_id": (row.get("product_id") or "").strip() or None,
        "keyword": keyword,
        "metric_name": metric_name,
        "metric_value": value,
        "metric_unit": metric_unit,
        "period_start": start,
        "period_end": end,
        "comparison_group": comparison_group,
        "source_url": source_url,
        "supporting_content_url": supporting_url,
        "captured_at": captured.astimezone(timezone.utc).isoformat(),
        "collection_method": (
            "authorized_provider_import"
            if source == "dataforseo_google_trends" else "manual_capture"
        ),
        "notes": (row.get("notes") or "").strip(),
        "is_synthetic": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and import manually captured public trend observations."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    input_path = Path(args.file)
    database_path = str(Path(args.db).resolve())
    input_file = str(input_path.resolve())
    if not input_path.is_file():
        print(json.dumps({
            "status": "failed",
            "database": database_path,
            "input_file": input_file,
            "rows_read": 0,
            "imported_or_updated": 0,
            "error_count": 1,
            "errors": [f"CSV file does not exist: {input_file}"],
        }, ensure_ascii=False, indent=2))
        return 2

    initialize(args.db)
    imported = 0
    rows_read = 0
    errors: list[str] = []
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "source_name", "market_code", "keyword", "metric_name", "metric_value",
            "metric_unit", "period_start", "period_end", "source_url", "captured_at",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            print(json.dumps({"status": "failed", "missing_columns": sorted(missing)}, indent=2))
            return 2
        with connect(args.db) as conn:
            ensure_source_tables(conn)
            for line, row in enumerate(reader, start=2):
                rows_read += 1
                try:
                    item = _parse_row(row, line)
                    product_id = item["product_id"]
                    if product_id:
                        exists = conn.execute(
                            "SELECT 1 FROM products WHERE product_id=?", (product_id,)
                        ).fetchone()
                        if not exists:
                            raise ValueError(f"row {line}: unknown product_id {product_id!r}")
                    identity = "|".join(
                        str(item[key] or "") for key in (
                            "source_name", "market_code", "product_id", "keyword", "metric_name",
                            "period_start", "period_end", "comparison_group", "source_url",
                        )
                    )
                    item["observation_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    conn.execute(
                        """INSERT INTO source_observations(observation_id,source_name,observation_type,
                           market_code,product_id,keyword,metric_name,metric_value,metric_unit,
                           period_start,period_end,comparison_group,source_url,supporting_content_url,
                           captured_at,collection_method,notes,is_synthetic,validation_status)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'accepted')
                           ON CONFLICT(observation_id) DO UPDATE SET metric_value=excluded.metric_value,
                           metric_unit=excluded.metric_unit,captured_at=excluded.captured_at,
                           supporting_content_url=excluded.supporting_content_url,notes=excluded.notes,
                           product_id=excluded.product_id,validation_status='accepted'""",
                        tuple(item[key] for key in (
                            "observation_id","source_name","observation_type","market_code","product_id",
                            "keyword","metric_name","metric_value","metric_unit","period_start",
                            "period_end","comparison_group","source_url","supporting_content_url",
                            "captured_at","collection_method","notes",
                        )),
                    )
                    if item["source_name"] in {"google_trends_ui", "dataforseo_google_trends"} and product_id:
                        conn.execute(
                            """INSERT INTO search_snapshots(product_id,market_code,keyword,period_start,
                               period_end,interest_index,source,captured_at,is_synthetic,comparison_group)
                               VALUES(?,?,?,?,?,?,?,?,0,?)
                               ON CONFLICT(product_id,market_code,keyword,period_start,period_end,source,
                                           comparison_group)
                               DO UPDATE SET interest_index=excluded.interest_index,
                               captured_at=excluded.captured_at,is_synthetic=excluded.is_synthetic""",
                            (product_id,item["market_code"],item["keyword"],item["period_start"],
                             item["period_end"],item["metric_value"],"google_trends_ui",
                             item["captured_at"],item["comparison_group"]),
                        )
                    imported += 1
                except (ValueError, KeyError) as exc:
                    errors.append(str(exc))
    if rows_read == 0:
        print(json.dumps({
            "status": "failed",
            "database": database_path,
            "input_file": input_file,
            "rows_read": 0,
            "imported_or_updated": 0,
            "error_count": 1,
            "errors": [
                "CSV contains a header but no data rows; add at least one real observation before importing."
            ],
        }, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps({
        "status": "success" if not errors else "partial",
        "database": database_path,
        "input_file": input_file,
        "rows_read": rows_read,
        "imported_or_updated": imported,
        "error_count": len(errors),
        "errors": errors[:50],
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
