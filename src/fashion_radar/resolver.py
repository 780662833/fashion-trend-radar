from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rapidfuzz import fuzz, process


@dataclass(frozen=True)
class ProductMatch:
    product_id: str | None
    canonical_name: str | None
    confidence: float
    ambiguous: bool
    candidates: tuple[tuple[str, float], ...]


def resolve_product_name(conn: sqlite3.Connection, query: str, cutoff: float = 82.0) -> ProductMatch:
    """Resolve a product alias conservatively; close fuzzy matches are never auto-selected."""
    normalized = " ".join(query.casefold().split())
    if not normalized:
        return ProductMatch(None, None, 0.0, False, ())

    rows = conn.execute("SELECT product_id,canonical_name,aliases FROM products").fetchall()
    choices: list[tuple[str, str, str]] = []
    for row in rows:
        names = [row["canonical_name"], *(part.strip() for part in row["aliases"].split(";"))]
        for name in names:
            if name:
                choices.append((name, row["product_id"], row["canonical_name"]))
    if not choices:
        return ProductMatch(None, None, 0.0, False, ())

    matches = process.extract(normalized, [item[0].casefold() for item in choices], scorer=fuzz.WRatio, limit=5)
    ranked: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    for _, score, idx in matches:
        product_id = choices[idx][1]
        if product_id not in seen:
            ranked.append((product_id, choices[idx][2], float(score)))
            seen.add(product_id)
    if not ranked or ranked[0][2] < cutoff:
        return ProductMatch(None, None, ranked[0][2] if ranked else 0.0, False,
                            tuple((name, score) for _, name, score in ranked))
    ambiguous = len(ranked) > 1 and ranked[0][2] - ranked[1][2] < 3.0
    if ambiguous:
        return ProductMatch(None, None, ranked[0][2], True,
                            tuple((name, score) for _, name, score in ranked[:3]))
    return ProductMatch(ranked[0][0], ranked[0][1], ranked[0][2], False,
                        tuple((name, score) for _, name, score in ranked[:3]))
