from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from ..db import connect, initialize
from ..providers.tiktok.dtk_embedded import (
    DTK_SOURCE_COMMIT,
    DTKEmbeddedClient,
    EXPECTED_DTK_VERSION,
)
from ..source_store import (
    QuotaBudgetExceeded,
    ensure_source_tables,
    finish_api_request,
    finish_source_run,
    reserve_api_request,
    start_source_run,
)

ALLOWED_MARKETS = {"US", "CA", "GB"}
ALLOWED_SEED_TYPES = {"video_id", "creator_handle", "hashtag"}
SOURCE_NAME = "tiktok_dtk_embedded"


class TikTokSeedError(ValueError):
    pass


@dataclass(frozen=True)
class TikTokSeed:
    market_code: str
    seed_type: str
    seed_value: str
    suggested_product_id: str | None = None
    max_items: int = 20


@dataclass
class TikTokCollectionResult:
    run_id: str
    status: str
    seeds: int
    items_seen: int
    items_saved: int
    errors: list[str]
    requests_made: int
    network_diagnostics: list[dict[str, Any]] = field(default_factory=list)


class SeedClient(Protocol):
    async def fetch_seed(
        self, seed_type: str, seed_value: str, market_code: str, max_items: int
    ) -> list[dict[str, Any]]: ...


def load_seed_plan(
    path: str | Path, db_path: str | Path | None = None
) -> list[TikTokSeed]:
    input_path = Path(path)
    if not input_path.is_file():
        raise FileNotFoundError(
            "TikTok seed file does not exist: " + str(input_path.resolve())
        )
    rows: list[TikTokSeed] = []
    seen: set[tuple[str, str, str]] = set()
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"market_code", "seed_type", "seed_value"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise TikTokSeedError(
                "TikTok seed CSV missing columns: " + ", ".join(sorted(missing))
            )
        for line, raw in enumerate(reader, start=2):
            market = (raw.get("market_code") or "").strip().upper()
            seed_type = (raw.get("seed_type") or "").strip().lower()
            seed_value = (raw.get("seed_value") or "").strip()
            product_id = (raw.get("suggested_product_id") or "").strip() or None
            raw_max = (raw.get("max_items") or "20").strip() or "20"
            if market not in ALLOWED_MARKETS:
                raise TikTokSeedError(
                    "row " + str(line) + ": market_code must be US, CA or GB"
                )
            if seed_type not in ALLOWED_SEED_TYPES:
                raise TikTokSeedError(
                    "row "
                    + str(line)
                    + ": seed_type must be one of "
                    + ", ".join(sorted(ALLOWED_SEED_TYPES))
                )
            if not seed_value:
                raise TikTokSeedError("row " + str(line) + ": seed_value is required")
            if seed_type == "creator_handle":
                seed_value = seed_value.lstrip("@")
                if not seed_value or len(seed_value) > 100:
                    raise TikTokSeedError(
                        "row " + str(line) + ": invalid creator_handle"
                    )
            elif seed_type == "hashtag":
                seed_value = seed_value.lstrip("#")
                if not seed_value or len(seed_value) > 100:
                    raise TikTokSeedError("row " + str(line) + ": invalid hashtag")
            elif len(seed_value) > 64 or not seed_value.isdigit():
                raise TikTokSeedError(
                    "row " + str(line) + ": video_id must be a numeric TikTok content ID"
                )
            try:
                max_items = int(raw_max)
            except ValueError:
                raise TikTokSeedError(
                    "row " + str(line) + ": max_items must be an integer"
                ) from None
            if not 1 <= max_items <= 50:
                raise TikTokSeedError(
                    "row " + str(line) + ": max_items must be between 1 and 50"
                )
            key = (market, seed_type, seed_value.casefold())
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                TikTokSeed(market, seed_type, seed_value, product_id, max_items)
            )
    if not rows:
        raise TikTokSeedError("TikTok seed CSV contains no data rows")

    if db_path is not None:
        with connect(db_path) as conn:
            known_products = {
                row["product_id"]
                for row in conn.execute("SELECT product_id FROM products")
            }
        unknown = sorted(
            {
                row.suggested_product_id
                for row in rows
                if row.suggested_product_id
            }
            - known_products
        )
        if unknown:
            raise TikTokSeedError(
                "Unknown suggested_product_id values: " + ", ".join(unknown)
            )
    return rows


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _stable_creator_key(author_id: str | None) -> str | None:
    if not author_id:
        return None
    digest = hashlib.sha256(author_id.encode("utf-8")).hexdigest()[:20]
    return "ttc_" + digest


def _persist_candidate(
    conn: sqlite3.Connection,
    run_id: str,
    seed: TikTokSeed,
    item: dict[str, Any],
    observed_at: str,
) -> bool:
    content_id = str(item.get("content_id") or "").strip()
    content_url = str(item.get("content_url") or "").strip()
    if not content_id or not content_url.startswith("https://www.tiktok.com/"):
        return False

    digest = hashlib.sha256(
        (run_id + "|" + seed.market_code + "|" + content_id).encode("utf-8")
    ).hexdigest()[:32]
    hashtags = item.get("hashtags") or []
    if not isinstance(hashtags, list):
        hashtags = []

    conn.execute(
        """INSERT OR IGNORE INTO tiktok_content_candidates(
           candidate_id,run_id,market_code,market_basis,audience_market_code,
           seed_type,seed_value,suggested_product_id,link_status,external_content_id,
           content_url,published_at,content_type,caption,hashtags_json,creator_key,
           creator_handle,views,likes,comments,shares,saves,observed_at)
           VALUES(?,?,?,'research_context',NULL,?,?,?,'needs_review',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            digest,
            run_id,
            seed.market_code,
            seed.seed_type,
            seed.seed_value,
            seed.suggested_product_id,
            content_id,
            content_url,
            item.get("published_at"),
            str(item.get("content_type") or "unknown")[:40],
            str(item.get("caption") or "")[:4000],
            json.dumps(
                [str(value)[:100] for value in hashtags[:100]],
                ensure_ascii=False,
            ),
            _stable_creator_key(str(item.get("author_id") or "") or None),
            str(item.get("author_handle") or "")[:100],
            _positive_int(item.get("views")),
            _positive_int(item.get("likes")),
            _positive_int(item.get("comments")),
            _positive_int(item.get("shares")),
            _positive_int(item.get("saves")),
            observed_at,
        ),
    )
    return conn.execute("SELECT changes()").fetchone()[0] == 1


def _daily_budget() -> int:
    raw = os.getenv("TIKTOK_DAILY_REQUEST_BUDGET", "40")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("TIKTOK_DAILY_REQUEST_BUDGET must be an integer") from exc
    if not 1 <= value <= 1000:
        raise ValueError("TIKTOK_DAILY_REQUEST_BUDGET must be between 1 and 1000")
    return value


def _per_run_budget() -> int:
    raw = os.getenv("TIKTOK_MAX_REQUESTS_PER_RUN", "40")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("TIKTOK_MAX_REQUESTS_PER_RUN must be an integer") from exc
    if not 1 <= value <= 200:
        raise ValueError("TIKTOK_MAX_REQUESTS_PER_RUN must be between 1 and 200")
    return value


def _min_interval() -> float:
    raw = os.getenv("TIKTOK_MIN_REQUEST_INTERVAL_SECONDS", "2.5")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            "TIKTOK_MIN_REQUEST_INTERVAL_SECONDS must be numeric"
        ) from exc
    if value < 2.0 or value > 300:
        raise ValueError(
            "TIKTOK_MIN_REQUEST_INTERVAL_SECONDS must be between 2 and 300"
        )
    return value


class _SQLiteRequestGuard:
    def __init__(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        daily_budget: int,
        min_interval: float,
    ) -> None:
        self._conn = conn
        self._run_id = run_id
        self._daily_budget = daily_budget
        self._min_interval = min_interval
        self._last_request_at: float | None = None
        self.requests_made = 0

    async def before_request(self, endpoint: str) -> object:
        request_id = reserve_api_request(
            self._conn,
            self._run_id,
            SOURCE_NAME,
            endpoint,
            "anonymous_guest",
            self._daily_budget,
            1,
        )
        if self._last_request_at is not None:
            remaining = self._min_interval - (
                time.monotonic() - self._last_request_at
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_request_at = time.monotonic()
        self.requests_made += 1
        return request_id

    def after_request(
        self, token: object, succeeded: bool, error_code: str = ""
    ) -> None:
        finish_api_request(
            self._conn, int(token), succeeded, error_code
        )


def collect_tiktok_embedded(
    db_path: str | Path,
    seed_file: str | Path,
    *,
    client: SeedClient | None = None,
) -> TikTokCollectionResult:
    """Collect guest-accessible content into an unscored manual-review queue."""
    initialize(db_path)
    seeds = load_seed_plan(seed_file, db_path)
    daily_budget = _daily_budget()
    min_interval = _min_interval()
    per_run_budget = _per_run_budget()
    run_id = str(uuid4())
    errors: list[str] = []
    network_diagnostics: list[dict[str, Any]] = []
    items_seen = 0
    items_saved = 0
    observed_at = datetime.now(timezone.utc).isoformat()
    requests_made = 0
    owned_client = client is None

    def capture_diagnostic(exc: BaseException) -> None:
        raw = getattr(exc, "diagnostic", None)
        if not isinstance(raw, dict):
            return
        stage = raw.get("connection_stage")
        error_code = raw.get("error_code")
        exception_type = raw.get("exception_type")
        retryable = raw.get("retryable")
        version = raw.get("diagnostic_version")
        allowed_stages = {
            "dns",
            "tcp",
            "tls",
            "http",
            "transport",
            "unknown",
            "not_started",
        }
        if (
            version != 1
            or stage not in allowed_stages
            or not isinstance(error_code, str)
            or len(error_code) > 80
            or (exception_type is not None and not isinstance(exception_type, str))
            or type(retryable) is not bool
        ):
            return
        network_diagnostics.append(
            {
                "diagnostic_version": 1,
                "connection_stage": stage,
                "exception_type": exception_type,
                "error_code": error_code,
                "retryable": retryable,
            }
        )

    with connect(db_path) as conn:
        ensure_source_tables(conn)
        start_source_run(
            conn,
            run_id,
            SOURCE_NAME,
            seeds[0].market_code
            if len({seed.market_code for seed in seeds}) == 1
            else None,
            len(seeds),
            {
                "mode": "embedded_public_guest",
                "dtk_version": EXPECTED_DTK_VERSION,
                "dtk_source_commit": DTK_SOURCE_COMMIT,
                "identity_mode": "anonymous_guest_no_cookies",
                "keyword_post_search": "not_supported_guest_login_required",
                "product_linking": "manual_review_required",
                "markets": sorted({seed.market_code for seed in seeds}),
                "audience_geo_available": False,
            },
        )
        guard = _SQLiteRequestGuard(
            conn, run_id, daily_budget, min_interval
        )
        actual_client: SeedClient | None = client

        async def run_seeds() -> None:
            nonlocal items_seen, items_saved
            try:
                if actual_client is None:
                    raise RuntimeError("TikTok embedded client was not initialized")
                for seed in seeds:
                    try:
                        items = await actual_client.fetch_seed(
                            seed.seed_type,
                            seed.seed_value,
                            seed.market_code,
                            seed.max_items,
                        )
                        items_seen += len(items)
                        for item in items:
                            if _persist_candidate(
                                conn, run_id, seed, item, observed_at
                            ):
                                items_saved += 1
                    except QuotaBudgetExceeded as exc:
                        errors.append(str(exc))
                        break
                    except Exception as exc:
                        capture_diagnostic(exc)
                        code = getattr(exc, "error_code", type(exc).__name__)
                        errors.append(
                            seed.market_code
                            + "/"
                            + seed.seed_type
                            + "/"
                            + seed.seed_value
                            + ": "
                            + str(code)
                        )
            finally:
                # Close the wreq transport in the same event loop that used it.
                close = getattr(actual_client, "aclose", None)
                if owned_client and close is not None:
                    await close()

        try:
            if actual_client is None:
                actual_client = DTKEmbeddedClient(
                    request_guard=guard,
                    proxy_url=os.getenv("TIKTOK_PROXY_URL") or None,
                    max_requests_per_run=per_run_budget,
                )
            asyncio.run(run_seeds())
            requests_made = guard.requests_made
            status = (
                "success"
                if not errors
                else ("partial" if items_saved else "failed")
            )
        except Exception as exc:
            capture_diagnostic(exc)
            errors.append(
                getattr(exc, "error_code", type(exc).__name__)
            )
            requests_made = guard.requests_made
            status = "partial" if items_saved else "failed"

        finish_source_run(
            conn,
            run_id,
            status,
            items_seen,
            items_saved,
            errors,
            {
                "requests_made": requests_made,
                "items_pending_product_review": items_saved,
                "identity_mode": "anonymous_guest_no_cookies",
                "audience_geo_available": False,
                "network_diagnostics": network_diagnostics,
            },
        )

    return TikTokCollectionResult(
        run_id=run_id,
        status=status,
        seeds=len(seeds),
        items_seen=items_seen,
        items_saved=items_saved,
        errors=errors,
        requests_made=requests_made,
        network_diagnostics=network_diagnostics,
    )


__all__ = [
    "TikTokCollectionResult",
    "TikTokSeed",
    "TikTokSeedError",
    "collect_tiktok_embedded",
    "load_seed_plan",
]
