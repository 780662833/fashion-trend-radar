from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables

EVIDENCE_TYPES = {
    "official_product_page",
    "official_release_announcement",
    "authorized_retailer",
    "marketplace_listing",
    "editorial_reference",
}


def _parse(row: dict[str, str], line: int) -> dict:
    product_id = (row.get("product_id") or "").strip()
    evidence_type = (row.get("evidence_type") or "").strip()
    source_url = (row.get("source_url") or "").strip()
    if not product_id:
        raise ValueError(f"row {line}: product_id is required")
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(f"row {line}: unsupported evidence_type {evidence_type!r}")
    parsed_url = urlparse(source_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise ValueError(f"row {line}: source_url must be an HTTPS URL")
    if parsed_url.hostname.lower().endswith(".invalid") or parsed_url.hostname.lower() == "example.invalid":
        raise ValueError(f"row {line}: placeholder URLs cannot be product evidence")
    captured = (row.get("captured_at") or "").strip()
    if captured:
        try:
            instant = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"row {line}: captured_at must be ISO 8601") from None
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
    else:
        instant = datetime.now(UTC)
    primary_raw = (row.get("is_primary") or "false").strip().lower()
    if primary_raw not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"row {line}: is_primary must be a boolean")
    is_primary = primary_raw in {"true", "1", "yes"}
    identity = "|".join((product_id, evidence_type, source_url))
    return {
        "evidence_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "product_id": product_id,
        "evidence_type": evidence_type,
        "source_url": source_url,
        "captured_at": instant.astimezone(UTC).isoformat(),
        "is_primary": int(is_primary),
        "notes": (row.get("notes") or "").strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Attach source references supporting product identity and release details."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--file", required=True)
    args = parser.parse_args()
    initialize(args.db)
    imported = 0
    errors: list[str] = []
    with Path(args.file).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"product_id", "evidence_type", "source_url"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            print(json.dumps({"status": "failed", "missing_columns": sorted(missing)}, indent=2))
            return 2
        with connect(args.db) as conn:
            ensure_source_tables(conn)
            for line, row in enumerate(reader, start=2):
                try:
                    item = _parse(row, line)
                    exists = conn.execute(
                        "SELECT 1 FROM products WHERE product_id=?", (item["product_id"],)
                    ).fetchone()
                    if not exists:
                        raise ValueError(f"row {line}: unknown product_id {item['product_id']!r}")
                    conn.execute(
                        """INSERT INTO product_evidence(evidence_id,product_id,evidence_type,source_url,
                           captured_at,is_primary,notes) VALUES(?,?,?,?,?,?,?)
                           ON CONFLICT(product_id,evidence_type,source_url) DO UPDATE SET
                           captured_at=excluded.captured_at,is_primary=excluded.is_primary,
                           notes=excluded.notes""",
                        tuple(item[key] for key in (
                            "evidence_id", "product_id", "evidence_type", "source_url",
                            "captured_at", "is_primary", "notes",
                        )),
                    )
                    imported += 1
                except ValueError as exc:
                    errors.append(str(exc))
    print(json.dumps({
        "status": "success" if not errors else "partial",
        "imported_or_updated": imported,
        "error_count": len(errors),
        "errors": errors[:50],
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
