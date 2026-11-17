from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from fashion_radar.db import connect, initialize
from fashion_radar.source_store import ensure_source_tables

MARKETS = ("US", "CA", "GB")
SUBCATEGORIES = {
    "sneaker": {"basketball", "running", "lifestyle", "skate", "hiking", "retro_court"},
    "bag": {"shoulder_bag", "tote", "crossbody", "backpack", "top_handle", "clutch", "mini_bag"},
}
AUDIENCES = {"men", "women", "unisex", "unknown"}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def audit_catalog(conn, min_confidence: float = 0.8) -> dict:
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")
    products = conn.execute(
        """SELECT product_id,category,subcategory,brand,model_name,canonical_name,
                  style_code,aliases,audience_tag,identity_confidence
           FROM products ORDER BY product_id"""
    ).fetchall()
    evidence_rows = conn.execute(
        """SELECT product_id,COUNT(*) AS evidence_count,
                  SUM(CASE WHEN is_primary=1 THEN 1 ELSE 0 END) AS primary_count
           FROM product_evidence GROUP BY product_id"""
    ).fetchall()
    evidence = {row["product_id"]: row for row in evidence_rows}
    market_rows = conn.execute(
        """SELECT product_id,market_code,market_status FROM product_market"""
    ).fetchall()
    markets_by_product: dict[str, set[str]] = defaultdict(set)
    for row in market_rows:
        if row["market_status"] != "paused":
            markets_by_product[row["product_id"]].add(row["market_code"])

    issues: list[dict] = []
    identities: dict[str, list[str]] = defaultdict(list)
    style_codes: dict[str, list[str]] = defaultdict(list)
    aliases: dict[str, list[tuple[str, str]]] = defaultdict(list)
    product_summaries = []

    def add(severity: str, code: str, product_ids: list[str], detail: str) -> None:
        issues.append({
            "severity": severity,
            "code": code,
            "product_ids": sorted(set(product_ids)),
            "detail": detail,
        })

    for row in products:
        product = dict(row)
        product_id = product["product_id"]
        brand_key = _key(product["brand"])
        identities[f'{brand_key}:{_key(product["canonical_name"])}'].append(product_id)
        if product["style_code"]:
            style_codes[f'{brand_key}:{_key(product["style_code"])}'].append(product_id)
        names = [product["canonical_name"], *(
            part.strip() for part in (product["aliases"] or "").split(";")
        )]
        for name in names:
            normalized = _key(name)
            if normalized:
                aliases[normalized].append((product_id, name))

        if product["subcategory"] not in SUBCATEGORIES.get(product["category"], set()):
            add("blocker", "unsupported_subcategory", [product_id],
                f'{product["category"]}/{product["subcategory"]} is not in the v1 taxonomy.')
        if product["audience_tag"] not in AUDIENCES:
            add("blocker", "invalid_audience_tag", [product_id],
                f'Unsupported audience_tag: {product["audience_tag"]!r}.')
        if float(product["identity_confidence"]) < min_confidence:
            add("blocker", "low_identity_confidence", [product_id],
                f'identity_confidence={product["identity_confidence"]} is below {min_confidence:.2f}.')
        if not product["style_code"]:
            add("warning", "missing_style_code", [product_id],
                "No style code; retain a primary official or authorized product reference.")
        if not product["aliases"]:
            add("warning", "missing_aliases", [product_id],
                "No curated aliases; query coverage may miss local or colloquial product names.")

        evidence_row = evidence.get(product_id)
        evidence_count = int(evidence_row["evidence_count"]) if evidence_row else 0
        primary_count = int(evidence_row["primary_count"] or 0) if evidence_row else 0
        if evidence_count == 0:
            add("blocker", "missing_product_evidence", [product_id],
                "No source reference documents the product identity.")
        elif primary_count == 0:
            add("warning", "missing_primary_evidence", [product_id],
                "Evidence exists but none is marked as the primary identity reference.")

        mapped_markets = sorted(markets_by_product.get(product_id, set()))
        missing_markets = sorted(set(MARKETS) - set(mapped_markets))
        if missing_markets:
            add("warning", "incomplete_market_mapping", [product_id],
                f"No active product-market mapping for: {', '.join(missing_markets)}. Baseline queries can still be generated.")

        product_summaries.append({
            "product_id": product_id,
            "category": product["category"],
            "canonical_name": product["canonical_name"],
            "identity_confidence": float(product["identity_confidence"]),
            "evidence_count": evidence_count,
            "primary_evidence_count": primary_count,
            "active_market_mappings": mapped_markets,
        })

    for key, product_ids in identities.items():
        if len(set(product_ids)) > 1:
            add("blocker", "duplicate_canonical_identity", product_ids,
                f"Multiple products share normalized brand/canonical identity {key!r}.")
    for key, product_ids in style_codes.items():
        if len(set(product_ids)) > 1:
            add("blocker", "duplicate_brand_style_code", product_ids,
                f"Multiple products share normalized brand/style_code {key!r}.")
    for key, references in aliases.items():
        product_ids = [product_id for product_id, _ in references]
        if len(set(product_ids)) > 1:
            names = sorted({name for _, name in references})
            add("blocker", "ambiguous_alias", product_ids,
                f"Normalized alias {key!r} maps to multiple products: {', '.join(names)}.")

    blockers = sum(issue["severity"] == "blocker" for issue in issues)
    warnings = sum(issue["severity"] == "warning" for issue in issues)
    return {
        "status": "blocked" if blockers else "ready",
        "as_of": date.today().isoformat(),
        "min_confidence": min_confidence,
        "product_count": len(products),
        "blocker_count": blockers + (1 if not products else 0),
        "warning_count": warnings,
        "issues": ([{
            "severity": "blocker",
            "code": "empty_catalog",
            "product_ids": [],
            "detail": "No product records exist; import a curated product catalogue first.",
        }] if not products else []) + issues,
        "products": product_summaries,
        "notes": [
            "Evidence references document provenance but are not automatically authenticated by this audit.",
            "Missing product-market mappings are warnings because the query planner can generate baseline terms for all requested markets.",
            "A ready catalogue is a prerequisite for research planning, not proof of market demand.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit product identity quality, source provenance and market mapping."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--min-confidence", type=float, default=0.8)
    parser.add_argument("--out", help="Optional JSON report path.")
    args = parser.parse_args()
    try:
        initialize(args.db)
        with connect(args.db) as conn:
            ensure_source_tables(conn)
            report = audit_catalog(conn, args.min_confidence)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
