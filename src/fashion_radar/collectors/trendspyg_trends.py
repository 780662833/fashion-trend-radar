from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from ..db import connect, initialize
from ..source_store import (
    QuotaBudgetExceeded,
    ensure_source_tables,
    finish_api_request,
    finish_source_run,
    link_source_run_observation,
    reserve_api_request,
    start_source_run,
)
from .dataforseo_trends import TrendQueryGroup, load_query_plan

SOURCE_NAME = "trendspyg_google_trends"
PROVIDER = "trendspyg"


class TrendspygError(RuntimeError):
    """Raised when trendspyg returns an unusable Google Trends response."""


class TrendspygClient:
    """Lazy, no-cookie, HTTP-only adapter around the optional trendspyg package."""

    def __init__(self, module: Any | None = None) -> None:
        self._module = module

    def _api(self):
        if self._module is None:
            try:
                import trendspyg
            except ImportError as exc:
                raise RuntimeError(
                    'trendspyg is not installed. Install with: '
                    'python -m pip install -e ".[trendspyg,dev]"'
                ) from exc
            self._module = trendspyg
        return self._module

    def ensure_available(self) -> None:
        self._api()

    def fetch_group(self, group: TrendQueryGroup) -> list[dict]:
        api = self._api()
        timeframe = f"{group.date_from} {group.date_to}"
        common = {
            "geo": group.market_code,
            "timeframe": timeframe,
            "max_retries": 1,
            "retry_wait": 1.0,
            "cache": False,
            "archive": False,
            "cookies": False,
            "engine": "http",
        }
        if len(group.keywords) == 1:
            series = api.download_google_trends_interest_over_time(
                group.keywords[0], output_format="dict", **common
            )
            if not isinstance(series, list):
                raise TrendspygError("trendspyg single-keyword response is not a list")
            output: list[dict] = []
            for point in series:
                if not isinstance(point, dict):
                    raise TrendspygError("trendspyg returned a malformed time-series point")
                output.append({
                    "date": point.get("date"),
                    "values": {group.keywords[0]: point.get("value")},
                    "is_partial": bool(point.get("is_partial", False)),
                })
            return output

        if any("," in keyword for keyword in group.keywords):
            raise ValueError(
                "trendspyg comparison cannot safely represent keywords containing commas"
            )
        envelope = api.download_google_trends_comparison(
            list(group.keywords),
            output_format="dict",
            include_geo=False,
            **common,
        )
        if not isinstance(envelope, dict):
            raise TrendspygError("trendspyg comparison response is not an object")
        points = envelope.get("interest_over_time")
        if not isinstance(points, list):
            raise TrendspygError("trendspyg comparison response has no interest_over_time list")
        return points


def _parse_date(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrendspygError("trendspyg returned a point without a date")
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        raise TrendspygError(f"trendspyg returned an invalid point date: {value!r}") from None


def normalize_trendspyg_points(
    group: TrendQueryGroup,
    points: list[dict],
    captured_at: str | None = None,
) -> tuple[list[dict], int]:
    """Normalize one group; each group keeps its own Google 0-100 scale."""
    if not points:
        raise TrendspygError("trendspyg returned no Google Trends time-series points")
    capture_time = captured_at or datetime.now(UTC).isoformat()
    comparison_group = group.comparison_group.replace("dataforseo_", "trendspyg_", 1)
    scale_kind = "shared_comparison_group" if len(group.keywords) > 1 else "single_keyword_series"
    source_url = "https://trends.google.com/trends/explore?" + urlencode({
        "date": f"{group.date_from} {group.date_to}",
        "geo": group.market_code,
        "q": ",".join(group.keywords),
    })
    rows: list[dict] = []
    missing_count = 0
    for point in points:
        if not isinstance(point, dict):
            raise TrendspygError("trendspyg returned a malformed time-series point")
        point_date = _parse_date(point.get("date"))
        if not group.date_from <= point_date <= group.date_to:
            raise TrendspygError(
                f"trendspyg returned point {point_date} outside the requested date window"
            )
        if point.get("is_partial"):
            missing_count += len(group.keywords)
            continue
        values = point.get("values")
        if not isinstance(values, dict):
            raise TrendspygError("trendspyg point does not contain a keyword-value mapping")
        for keyword in group.keywords:
            raw_value = values.get(keyword)
            if raw_value is None:
                missing_count += 1
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                raise TrendspygError(
                    f"trendspyg returned a non-numeric value for {keyword!r}"
                ) from None
            if not 0 <= value <= 100:
                raise TrendspygError(
                    f"trendspyg value for {keyword!r} is outside 0-100: {value}"
                )
            notes = [
                "provider=trendspyg/1.9.0",
                "access=non_official_public_web",
                "engine=http",
                "cookies=false",
                f"scale={scale_kind}",
                "point_date_is_not_expanded_to_a_weekly_interval=true",
            ]
            if value == 0:
                notes.append(
                    "zero_may_mean_insufficient_search_data; do_not_treat_as_proven_zero_volume"
                )
            rows.append({
                "source_name": SOURCE_NAME,
                "observation_type": "trend",
                "market_code": group.market_code,
                "product_id": group.product_id,
                "keyword": keyword,
                "metric_name": "interest_index",
                "metric_value": value,
                "metric_unit": "index_0_100",
                "period_start": point_date,
                "period_end": point_date,
                "comparison_group": comparison_group,
                "source_url": source_url,
                "captured_at": capture_time,
                "collection_method": "non_official_public_web_library",
                "notes": "; ".join(notes),
                "is_synthetic": 0,
            })
    if not rows and not missing_count:
        raise TrendspygError("trendspyg returned no usable time-series observations")
    return rows, missing_count


def _persist_observations(conn, run_id: str, rows: list[dict]) -> int:
    saved = 0
    for item in rows:
        identity = "|".join(str(item[key] or "") for key in (
            "source_name", "market_code", "product_id", "keyword", "metric_name",
            "period_start", "period_end", "comparison_group", "source_url",
        ))
        observation_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO source_observations(
               observation_id,source_name,observation_type,market_code,product_id,keyword,
               metric_name,metric_value,metric_unit,period_start,period_end,comparison_group,
               source_url,supporting_content_url,captured_at,collection_method,notes,
               is_synthetic,validation_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'',?,?,?,0,'accepted')
               ON CONFLICT(observation_id) DO UPDATE SET
               metric_value=excluded.metric_value,metric_unit=excluded.metric_unit,
               captured_at=excluded.captured_at,collection_method=excluded.collection_method,
               notes=excluded.notes,validation_status='accepted'""",
            (
                observation_id, item["source_name"], item["observation_type"],
                item["market_code"], item["product_id"], item["keyword"], item["metric_name"],
                item["metric_value"], item["metric_unit"], item["period_start"],
                item["period_end"], item["comparison_group"], item["source_url"],
                item["captured_at"], item["collection_method"], item["notes"],
            ),
        )
        # The observation row is intentionally deduplicated across collection runs.
        # Preserve lineage separately so a refreshed observation can belong to
        # multiple source runs without overwriting its earlier run history.
        link_source_run_observation(conn, run_id, observation_id)
        conn.execute(
            """INSERT INTO search_snapshots(
               product_id,market_code,keyword,period_start,period_end,interest_index,source,
               captured_at,is_synthetic,comparison_group)
               VALUES(?,?,?,?,?,?,?,?,0,?)
               ON CONFLICT(product_id,market_code,keyword,period_start,period_end,source,
                           comparison_group)
               DO UPDATE SET interest_index=excluded.interest_index,
               captured_at=excluded.captured_at,is_synthetic=excluded.is_synthetic""",
            (
                item["product_id"], item["market_code"], item["keyword"],
                item["period_start"], item["period_end"], item["metric_value"],
                SOURCE_NAME, item["captured_at"], item["comparison_group"],
            ),
        )
        saved += 1
    return saved


def collect_trendspyg_trends(
    db_path: str | Path,
    query_file: str | Path,
    *,
    client: Any | None = None,
    max_requests: int | None = None,
    min_interval_seconds: float | None = None,
) -> dict:
    initialize(db_path)
    groups = load_query_plan(query_file, db_path)
    unsupported = sorted({group.search_type for group in groups} - {"web"})
    if unsupported:
        raise ValueError(
            "trendspyg provider supports Google web search only; unsupported search_type: "
            + ", ".join(unsupported)
        )
    if any(
        len(group.keywords) > 1 and any("," in keyword for keyword in group.keywords)
        for group in groups
    ):
        raise ValueError(
            "trendspyg comparison cannot safely represent keywords containing commas"
        )
    budget = max_requests if max_requests is not None else int(
        os.getenv("TRENDSPYG_DAILY_REQUEST_BUDGET", "5")
    )
    if budget < 1:
        raise ValueError("TRENDSPYG_DAILY_REQUEST_BUDGET must be >= 1")
    if len(groups) > budget:
        raise ValueError(
            f"Query plan contains {len(groups)} requests, exceeding the daily safety "
            f"budget of {budget}; split the plan or explicitly raise the budget."
        )
    interval = min_interval_seconds if min_interval_seconds is not None else float(
        os.getenv("TRENDSPYG_MIN_REQUEST_INTERVAL_SECONDS", "15")
    )
    if interval < 0:
        raise ValueError("TRENDSPYG_MIN_REQUEST_INTERVAL_SECONDS must be >= 0")
    api = client or TrendspygClient()
    if isinstance(api, TrendspygClient):
        api.ensure_available()

    run_id = str(uuid4())
    saved_total = 0
    missing_total = 0
    points_seen = 0
    requests_completed = 0
    errors: list[str] = []
    attempted_at = 0.0
    with connect(db_path) as conn:
        ensure_source_tables(conn)
        conn.execute(
            """INSERT OR IGNORE INTO sources(
               source_name,source_type,platform,access_method,authorization_note)
               VALUES(?,?,?,?,?)""",
            (
                SOURCE_NAME,
                "search_interest",
                "google_trends",
                "non_official_public_web_library",
                (
                    "Experimental opt-in only; not an official Google API; no login cookies, "
                    "browser fallback, retries or access-control circumvention."
                ),
            ),
        )
        start_source_run(
            conn,
            run_id,
            SOURCE_NAME,
            groups[0].market_code if len({g.market_code for g in groups}) == 1 else None,
            len(groups),
            {
                "query_file": str(Path(query_file).resolve()),
                "provider": "trendspyg",
                "engine": "http",
                "cookies": False,
                "max_requests": budget,
                "min_interval_seconds": interval,
                "comparison_groups": [
                    group.comparison_group.replace("dataforseo_", "trendspyg_", 1)
                    for group in groups
                ],
            },
        )
        for group in groups:
            request_id = None
            try:
                if attempted_at:
                    remaining = interval - (time.monotonic() - attempted_at)
                    if remaining > 0:
                        time.sleep(remaining)
                request_id = reserve_api_request(
                    conn,
                    run_id,
                    PROVIDER,
                    "google_trends.explore",
                    "public_web_requests",
                    budget,
                    1,
                )
                attempted_at = time.monotonic()
                points = api.fetch_group(group)
                normalized, missing = normalize_trendspyg_points(group, points)
                saved = _persist_observations(conn, run_id, normalized)
                finish_api_request(conn, request_id, True)
                saved_total += saved
                missing_total += missing
                points_seen += len(points)
                requests_completed += 1
            except QuotaBudgetExceeded as exc:
                errors.append(str(exc))
                break
            except Exception as exc:  # noqa: BLE001 - stop on any provider/client failure
                if request_id is not None:
                    finish_api_request(conn, request_id, False, type(exc).__name__)
                errors.append(f"{group.comparison_group}: {type(exc).__name__}: {exc}")
                # Any upstream/network/parser failure stops this run; no retries or rotation.
                break
        status = "success" if not errors else ("partial" if saved_total else "failed")
        finish_source_run(
            conn,
            run_id,
            status,
            items_seen=points_seen,
            items_saved=saved_total,
            errors=errors,
            metadata={
                "provider": "trendspyg",
                "requests_planned": len(groups),
                "requests_completed": requests_completed,
                "observations_saved": saved_total,
                "missing_points": missing_total,
            },
        )
    return {
        "status": status,
        "run_id": run_id,
        "planned_requests": len(groups),
        "requests_completed": requests_completed,
        "points_seen": points_seen,
        "observations_saved": saved_total,
        "missing_points": missing_total,
        "error_count": len(errors),
        "errors": errors,
        "note": (
            "trendspyg is a non-official public-web integration, not Google's official API. "
            "Google 0-100 values are comparable only within the same comparison_group."
        ),
    }
