from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from fashion_radar.db import connect, initialize

MARKETS = ("US", "CA", "GB")
SOURCES = ("tiktok_creative_center", "google_trends_ui", "youtube_data_api")
SNEAKER_INTENTS = (
    ("on_foot", "on feet", "product fit and real-world appearance"),
    ("outfit", "outfit", "styling and outfit discovery"),
    ("review", "review", "product evaluation and buyer questions"),
)
BAG_INTENTS = (
    ("outfit", "outfit", "styling and outfit discovery"),
    ("what_fits", "what fits", "capacity and everyday-use discovery"),
    ("review", "review", "product evaluation and buyer questions"),
)


def _parts(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[;|\n]+", value) if part.strip()]


def _clean(value: str) -> str:
    return " ".join(value.split()).strip()


def _market_category_term(category: str, subcategory: str, market: str) -> str:
    if category == "sneaker":
        footwear = "trainers" if market == "GB" else "sneakers"
        category_prefix = {
            "basketball": "basketball",
            "running": "running",
            "skate": "skate",
            "hiking": "hiking",
            "retro_court": "retro court",
        }.get(subcategory, "")
        return _clean(f"{category_prefix} {footwear}")
    bag_terms = {
        "shoulder_bag": "shoulder bag",
        "tote": "tote bag",
        "crossbody": "crossbody bag",
        "backpack": "backpack",
        "top_handle": "top handle bag",
        "clutch": "clutch bag",
        "mini_bag": "mini handbag" if market == "GB" else "mini purse",
    }
    return bag_terms.get(subcategory, "handbag" if market == "GB" else "purse")


def build_product_queries(product: dict, market: str, market_terms: str = "") -> list[dict]:
    """Create deterministic, deduplicated product query candidates with explicit intent."""
    category = product["category"]
    canonical = _clean(product["canonical_name"])
    brand_model = _clean(f'{product["brand"]} {product["model_name"]}')
    audience = (product.get("audience_tag") or "unisex").lower()
    candidates: list[tuple[str, str, int, str]] = [
        ("canonical", canonical, 1, "canonical product identity"),
        ("brand_model", brand_model, 1, "brand plus model identity"),
    ]

    if product.get("style_code"):
        candidates.append(("style_code", _clean(product["style_code"]), 1, "manufacturer style code"))
    for alias in _parts(product.get("aliases")):
        candidates.append(("alias", alias, 2, "curated product alias"))

    if category == "sneaker":
        candidates.extend([
            ("category_discovery", f'{canonical} {_market_category_term(category, product["subcategory"], market)}', 2,
             "market-local category vocabulary"),
        ])
        intents = SNEAKER_INTENTS
    else:
        candidates.extend([
            ("category_discovery", f'{canonical} {_market_category_term(category, market)}', 2,
             "market-local category vocabulary"),
        ])
        intents = BAG_INTENTS

    for intent, suffix, reason in intents:
        candidates.append((intent, f"{canonical} {suffix}", 2, reason))
    if audience in {"men", "women"}:
        audience_term = "women's" if audience == "women" else "men's"
        candidates.append(("audience", f"{audience_term} {canonical}", 2,
                           f"catalogue audience tag: {audience}"))

    for term in _parts(market_terms):
        candidates.append(("market_custom", term, 1, "curated product-market search term"))

    seen: set[str] = set()
    result: list[dict] = []
    for query_type, keyword, priority, rationale in candidates:
        keyword = _clean(keyword)
        key = keyword.casefold()
        if not keyword or key in seen:
            continue
        seen.add(key)
        result.append({
            "query_type": query_type,
            "keyword": keyword,
            "priority": priority,
            "rationale": rationale,
        })
    return result


def build_research_plan(conn, markets: tuple[str, ...] = MARKETS) -> list[dict]:
    invalid = sorted(set(markets) - set(MARKETS))
    if invalid:
        raise ValueError(f"unsupported market(s): {', '.join(invalid)}")
    products = conn.execute(
        """SELECT product_id,category,subcategory,brand,model_name,canonical_name,
                  style_code,aliases,audience_tag
           FROM products ORDER BY category,brand,canonical_name,product_id"""
    ).fetchall()
    market_rows = conn.execute(
        """SELECT product_id,market_code,market_search_terms,market_status
           FROM product_market"""
    ).fetchall()
    mapped = {(row["product_id"], row["market_code"]): row for row in market_rows}

    output: list[dict] = []
    for product_row in products:
        product = dict(product_row)
        for market in markets:
            mapping = mapped.get((product["product_id"], market))
            if mapping and mapping["market_status"] == "paused":
                continue
            market_terms = mapping["market_search_terms"] if mapping else ""
            candidates = build_product_queries(product, market, market_terms)
            # TikTok and YouTube can use the complete bounded discovery list.
            for source in ("tiktok_creative_center", "youtube_data_api"):
                for query in candidates:
                    output.append({
                        "product_id": product["product_id"],
                        "category": product["category"],
                        "brand": product["brand"],
                        "canonical_name": product["canonical_name"],
                        "market_code": market,
                        "source_name": source,
                        "query_type": query["query_type"],
                        "keyword": query["keyword"],
                        "priority": query["priority"],
                        "comparison_group": "",
                        "search_window": "last_12_months",
                        "language": "en",
                        "market_terms_source": "product_market" if mapping and market_terms else "generated",
                        "rationale": query["rationale"],
                    })

            # Google Trends compares at most five terms per UI request. Group IDs
            # deliberately isolate each comparison; never compare values across groups.
            for index in range(0, len(candidates), 5):
                group_number = index // 5 + 1
                group_id = f'{market}_{product["product_id"]}_web_12m_g{group_number}'
                for query in candidates[index:index + 5]:
                    output.append({
                        "product_id": product["product_id"],
                        "category": product["category"],
                        "brand": product["brand"],
                        "canonical_name": product["canonical_name"],
                        "market_code": market,
                        "source_name": "google_trends_ui",
                        "query_type": query["query_type"],
                        "keyword": query["keyword"],
                        "priority": query["priority"],
                        "comparison_group": group_id,
                        "search_window": "last_12_months",
                        "language": "en",
                        "market_terms_source": "product_market" if mapping and market_terms else "generated",
                        "rationale": query["rationale"],
                    })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate product- and market-specific manual research query tasks."
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--out", default="configs/manual_research/research_query_plan.csv")
    parser.add_argument("--markets", default="US,CA,GB")
    args = parser.parse_args()
    try:
        markets = tuple(value.strip().upper() for value in args.markets.split(",") if value.strip())
        if not markets or len(set(markets)) != len(markets):
            raise ValueError("markets must be a non-empty, duplicate-free list")
        initialize(args.db)
        with connect(args.db) as conn:
            rows = build_research_plan(conn, markets)
        if not rows:
            print(json.dumps({"status": "empty", "message": "No products found; import a real product catalogue first."}))
            return 1
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    summary = {
        "status": "success",
        "output": str(output),
        "products": len({row["product_id"] for row in rows}),
        "product_market_pairs": len({(row["product_id"], row["market_code"]) for row in rows}),
        "tasks": len(rows),
        "markets": list(markets),
        "sources": list(SOURCES),
        "note": "Query tasks are research prompts, not observed trend data. Google Trends groups must not be compared across group IDs.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
