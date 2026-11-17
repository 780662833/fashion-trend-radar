from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from ..db import connect, initialize
from ..source_store import (
    QuotaBudgetExceeded, ensure_source_tables, finish_api_request,
    finish_source_run, reserve_api_request, start_source_run,
)

API_ROOT = "https://www.googleapis.com/youtube/v3"
USER_AGENT = "fashion-trend-radar/0.2 (+https://github.com/780662833/fashion-trend-radar)"
ALLOWED_MARKETS = {"US", "CA", "GB"}
HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]{2,60}")


class YouTubeApiError(RuntimeError):
    def __init__(self, message: str, error_code: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code


class ApiClient(Protocol):
    def request(self, resource: str, params: dict[str, str]) -> dict: ...


class YouTubeApiClient:
    """Small stdlib-only client. API keys are never included in exception text."""

    def __init__(self, api_key: str, timeout: float = 25.0) -> None:
        if not api_key:
            raise ValueError("YOUTUBE_API_KEY is not configured")
        self._api_key = api_key
        self.timeout = timeout

    def request(self, resource: str, params: dict[str, str]) -> dict:
        if resource not in {"search", "videos"}:
            raise ValueError(f"Unsupported YouTube resource: {resource}")
        query = dict(params)
        query["key"] = self._api_key
        url = f"{API_ROOT}/{resource}?{urlencode(query)}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("error", {})
                errors = detail.get("errors", [])
                reason = errors[0].get("reason", "") if errors else ""
                message = detail.get("message", exc.reason)
            except (ValueError, AttributeError, TypeError):
                reason, message = "", str(exc.reason)
            raise YouTubeApiError(f"HTTP {exc.code}: {message}", reason or str(exc.code)) from None
        except URLError as exc:
            raise YouTubeApiError(f"Network error: {exc.reason}", "network_error") from None
        except (TimeoutError, OSError) as exc:
            raise YouTubeApiError(f"Network error: {type(exc).__name__}", "network_error") from None
        except json.JSONDecodeError:
            raise YouTubeApiError("YouTube API returned invalid JSON", "invalid_json") from None

        if not isinstance(payload, dict):
            raise YouTubeApiError("YouTube API returned an unexpected response", "invalid_response")
        if "error" in payload:
            err = payload["error"]
            errors = err.get("errors", []) if isinstance(err, dict) else []
            reason = errors[0].get("reason", "") if errors else ""
            message = err.get("message", "YouTube API error") if isinstance(err, dict) else "YouTube API error"
            raise YouTubeApiError(str(message), reason)
        return payload


@dataclass(frozen=True)
class QuerySpec:
    product_id: str
    market_code: str
    query: str
    language: str = "en"


@dataclass
class CollectionResult:
    run_id: str
    status: str
    queries: int
    items_seen: int
    items_saved: int
    errors: list[str]


def load_query_plan(path: str | Path, db_path: str | Path | None = None) -> list[QuerySpec]:
    rows: list[QuerySpec] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"product_id", "market_code", "query"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Query CSV missing columns: {', '.join(sorted(missing))}")
        seen: set[tuple[str, str, str]] = set()
        for line_number, raw in enumerate(reader, start=2):
            product_id = (raw.get("product_id") or "").strip()
            market = (raw.get("market_code") or "").strip().upper()
            query = (raw.get("query") or "").strip()
            language = (raw.get("language") or "en").strip().lower()
            if not product_id or not query:
                raise ValueError(f"Query CSV row {line_number}: product_id and query are required")
            if market not in ALLOWED_MARKETS:
                raise ValueError(f"Query CSV row {line_number}: invalid market_code {market!r}")
            if language not in {"en", "fr"}:
                raise ValueError(f"Query CSV row {line_number}: language must be en or fr")
            key = (product_id, market, query.casefold())
            if key in seen:
                continue
            seen.add(key)
            rows.append(QuerySpec(product_id, market, query, language))
    if not rows:
        raise ValueError("Query CSV contains no query rows")
    if db_path is not None:
        with connect(db_path) as conn:
            known = {row["product_id"] for row in conn.execute("SELECT product_id FROM products")}
        unknown = sorted({row.product_id for row in rows} - known)
        if unknown:
            raise ValueError(f"Unknown product_id values in query CSV: {', '.join(unknown)}")
    return rows


def _daily_budget(env_name: str, default: int) -> int:
    raw = os.getenv(env_name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{env_name} must be >= 1")
    return value


def _tracked_request(conn, client: ApiClient, run_id: str, resource: str,
                     params: dict[str, str]) -> dict:
    if resource == "search":
        bucket, budget, method = "search_queries", _daily_budget("YOUTUBE_SEARCH_DAILY_BUDGET", 80), "search.list"
    else:
        bucket, budget, method = "other_methods", _daily_budget("YOUTUBE_OTHER_DAILY_BUDGET", 9000), "videos.list"
    request_id = reserve_api_request(conn, run_id, "youtube", method, bucket, budget, 1)
    try:
        payload = client.request(resource, params)
    except Exception as exc:
        code = exc.error_code if isinstance(exc, YouTubeApiError) else type(exc).__name__
        finish_api_request(conn, request_id, False, str(code))
        raise
    finish_api_request(conn, request_id, True)
    return payload


def _stable_creator_key(channel_id: str | None) -> str | None:
    if not channel_id:
        return None
    digest = hashlib.sha256(channel_id.encode("utf-8")).hexdigest()[:20]
    return f"ytc_{digest}"


def _signal_id(market: str, product_id: str, video_id: str) -> str:
    digest = hashlib.sha256(f"{market}|{product_id}|{video_id}".encode("utf-8")).hexdigest()[:28]
    return f"YT-{digest}"


def _as_nonnegative_int(value) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _parse_published_at(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _persist_video(conn: sqlite3.Connection, spec: QuerySpec, video: dict,
                   search_snippet: dict, snapshot_at: str) -> bool:
    """Store one individual reference only; do not aggregate API data into product scores."""
    video_id = str(video.get("id") or "")
    if not video_id:
        return False
    snippet = video.get("snippet") or search_snippet or {}
    published = snippet.get("publishedAt") or search_snippet.get("publishedAt")
    if not published:
        return False
    published_at = _parse_published_at(published)
    observed = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
    expires_at = (observed + timedelta(days=30)).astimezone(timezone.utc).isoformat()
    title = str(snippet.get("title") or "")
    description = str(snippet.get("description") or "")
    reference_id = _signal_id(spec.market_code, spec.product_id, video_id)
    statistics = video.get("statistics") or {}
    conn.execute(
        """INSERT INTO youtube_video_references(reference_id,product_id,market_code,video_id,
           search_query,content_url,published_at,observed_at,expires_at,title,description,
           view_count,like_count,comment_count)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(product_id,market_code,video_id) DO UPDATE SET
           reference_id=excluded.reference_id,search_query=excluded.search_query,
           content_url=excluded.content_url,published_at=excluded.published_at,
           observed_at=excluded.observed_at,expires_at=excluded.expires_at,
           title=excluded.title,description=excluded.description,
           view_count=excluded.view_count,like_count=excluded.like_count,
           comment_count=excluded.comment_count""",
        (reference_id, spec.product_id, spec.market_code, video_id, spec.query,
         f"https://www.youtube.com/watch?v={video_id}", published_at, snapshot_at,
         expires_at, title[:500], description[:4000],
         _as_nonnegative_int(statistics.get("viewCount")),
         _as_nonnegative_int(statistics.get("likeCount")),
         _as_nonnegative_int(statistics.get("commentCount"))),
    )
    return True

def collect_youtube(db_path: str | Path, query_file: str | Path, published_after: str,
                    max_results: int = 25, api_key: str | None = None,
                    client: ApiClient | None = None) -> CollectionResult:
    if not 1 <= max_results <= 50:
        raise ValueError("max_results must be between 1 and 50")
    if not published_after:
        raise ValueError("published_after is required; use a bounded research window")
    initialize(db_path)
    queries = load_query_plan(query_file, db_path)
    from ..source_store import load_local_env
    load_local_env()
    run_id = str(uuid4())
    api = client or YouTubeApiClient(api_key or os.getenv("YOUTUBE_API_KEY", ""))
    errors: list[str] = []
    items_seen = 0
    items_saved = 0
    snapshot_at = datetime.now(timezone.utc).isoformat()

    with connect(db_path) as conn:
        ensure_source_tables(conn)
        start_source_run(
            conn, run_id, "youtube_data_api", None, len(queries),
            {"query_file": str(query_file), "published_after": published_after,
             "max_results": max_results, "markets": sorted({q.market_code for q in queries})},
        )
        for spec in queries:
            try:
                search_response = _tracked_request(
                    conn, api, run_id, "search",
                    {"part": "snippet", "type": "video", "q": spec.query,
                     "regionCode": spec.market_code, "relevanceLanguage": spec.language,
                     "order": "date", "publishedAfter": published_after,
                     "maxResults": str(max_results)},
                )
                search_items = search_response.get("items") or []
                items_seen += len(search_items)
                video_ids = [
                    item.get("id", {}).get("videoId")
                    for item in search_items
                    if item.get("id", {}).get("videoId")
                ]
                details_by_id: dict[str, dict] = {}
                if video_ids:
                    try:
                        detail_response = _tracked_request(
                            conn, api, run_id, "videos",
                            {"part": "snippet,statistics", "id": ",".join(video_ids[:50])},
                        )
                        details_by_id = {
                            str(item.get("id")): item
                            for item in detail_response.get("items", [])
                            if item.get("id")
                        }
                    except (YouTubeApiError, QuotaBudgetExceeded) as exc:
                        errors.append(f"{spec.market_code} {spec.product_id}: video detail lookup failed: {exc}")
                for item in search_items:
                    video_id = item.get("id", {}).get("videoId")
                    if not video_id:
                        continue
                    fallback_snippet = item.get("snippet") or {}
                    detail = details_by_id.get(video_id, {"id": video_id, "snippet": fallback_snippet})
                    if _persist_video(conn, spec, detail, fallback_snippet, snapshot_at):
                        items_saved += 1
            except (YouTubeApiError, QuotaBudgetExceeded, ValueError) as exc:
                errors.append(f"{spec.market_code} {spec.product_id} [{spec.query}]: {exc}")
                if isinstance(exc, QuotaBudgetExceeded):
                    break

        status = "success" if not errors else ("partial" if items_saved else "failed")
        finish_source_run(
            conn, run_id, status, items_seen, items_saved, errors,
            {"markets": sorted({q.market_code for q in queries}),
             "note": "regionCode is search context, not proof of viewer or creator residence"},
        )
    return CollectionResult(run_id, status, len(queries), items_seen, items_saved, errors)


def probe_youtube_api(db_path: str | Path, api_key: str | None = None) -> dict:
    """Make one explicitly requested read-only API call and record its quota use."""
    initialize(db_path)
    from ..source_store import load_local_env
    load_local_env()
    run_id = str(uuid4())
    api = YouTubeApiClient(api_key or os.getenv("YOUTUBE_API_KEY", ""))
    with connect(db_path) as conn:
        ensure_source_tables(conn)
        start_source_run(conn, run_id, "youtube_data_api_probe", "US", 1, {"probe": True})
        try:
            response = _tracked_request(
                conn, api, run_id, "search",
                {"part": "snippet", "type": "video", "q": "sneaker style",
                 "regionCode": "US", "maxResults": "1"},
            )
            count = len(response.get("items", []))
            finish_source_run(conn, run_id, "success", count, 0, [],
                              {"probe_only": True, "no_product_rows_written": True})
            return {"status": "success", "run_id": run_id, "items_returned": count,
                    "quota_requests": 1}
        except Exception as exc:
            code = exc.error_code if isinstance(exc, YouTubeApiError) else type(exc).__name__
            finish_source_run(conn, run_id, "failed", 0, 0, [str(exc)], {"probe_only": True})
            return {"status": "failed", "run_id": run_id, "error": str(exc), "error_code": code}
