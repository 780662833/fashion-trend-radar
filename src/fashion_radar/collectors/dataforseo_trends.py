from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from ..db import connect, initialize
from ..source_store import (
    QuotaBudgetExceeded,
    ensure_source_tables,
    finish_api_request,
    finish_source_run,
    reserve_api_request,
    start_source_run,
)

API_ROOT = "https://api.dataforseo.com/v3"
POST_ENDPOINT = "/keywords_data/google_trends/explore/task_post"
GET_ENDPOINT = "/keywords_data/google_trends/explore/task_get"
SOURCE_NAME = "dataforseo_google_trends"
MARKET_LOCATIONS = {
    "US": "United States",
    "CA": "Canada",
    "GB": "United Kingdom",
}
ALLOWED_TYPES = {"web", "news", "images", "youtube", "froogle"}
FORBIDDEN_KEYWORD_CHARS = set('<>|"-+=~!:*()[]{}')


class DataForSEOError(RuntimeError):
    """Raised when the provider request or payload cannot be safely processed."""


class DataForSEOApiClient:
    """Small stdlib-only client. Credentials are never included in error messages."""

    def __init__(self, login: str, password: str, timeout: float = 45.0) -> None:
        if not login or not password:
            raise ValueError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are required")
        token = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
        self._authorization = f"Basic {token}"
        self.timeout = timeout

    def _request(self, method: str, endpoint: str, payload: Any = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{API_ROOT}{endpoint}",
            data=body,
            method=method,
            headers={
                "Authorization": self._authorization,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "fashion-trend-radar/0.2",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                details = json.loads(exc.read().decode("utf-8", errors="replace"))
                message = details.get("status_message") or details.get("message") or exc.reason
            except (ValueError, AttributeError, TypeError):
                message = exc.reason
            raise DataForSEOError(f"DataForSEO HTTP {exc.code}: {message}") from None
        except URLError as exc:
            raise DataForSEOError(f"DataForSEO network error: {exc.reason}") from None
        except (TimeoutError, OSError) as exc:
            raise DataForSEOError(f"DataForSEO network error: {type(exc).__name__}") from None
        except json.JSONDecodeError:
            raise DataForSEOError("DataForSEO returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise DataForSEOError("DataForSEO returned an unexpected response shape")
        return decoded

    def post(self, endpoint: str, payload: list[dict]) -> dict:
        return self._request("POST", endpoint, payload)

    def get(self, endpoint: str) -> dict:
        return self._request("GET", endpoint)


@dataclass(frozen=True)
class TrendQueryGroup:
    product_id: str
    market_code: str
    keywords: tuple[str, ...]
    date_from: str
    date_to: str
    search_type: str
    language_code: str
    comparison_set: str
    comparison_group: str

    def request_payload(self, tag: str) -> dict:
        return {
            "location_name": MARKET_LOCATIONS[self.market_code],
            "language_code": self.language_code,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "type": self.search_type,
            "item_types": ["google_trends_graph"],
            "keywords": list(self.keywords),
            "tag": tag[:255],
        }


def _comparison_group(
    market: str, search_type: str, date_from: str, date_to: str,
    comparison_set: str, keywords: tuple[str, ...]
) -> str:
    normalized = "|".join(keyword.casefold() for keyword in keywords)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    safe_set = "".join(ch if ch.isalnum() else "_" for ch in comparison_set.casefold())[:24]
    return f"dataforseo_{market}_{search_type}_{date_from}_{date_to}_{safe_set}_{digest}"


def load_query_plan(path: str | Path, db_path: str | Path) -> list[TrendQueryGroup]:
    input_path = Path(path)
    if not input_path.is_file():
        raise FileNotFoundError(f"DataForSEO query plan does not exist: {input_path.resolve()}")

    grouped: dict[tuple[str, str, str, str, str, str], list[str]] = defaultdict(list)
    seen: set[tuple[str, str, str, str, str, str, str]] = set()
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "product_id", "market_code", "keyword", "date_from", "date_to",
            "search_type", "language_code",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"DataForSEO query CSV missing columns: {', '.join(sorted(missing))}")
        for line, row in enumerate(reader, start=2):
            product_id = (row.get("product_id") or "").strip()
            market = (row.get("market_code") or "").strip().upper()
            keyword = (row.get("keyword") or "").strip()
            search_type = (row.get("search_type") or "web").strip().lower()
            language = (row.get("language_code") or "en").strip().lower()
            try:
                date_from = date.fromisoformat((row.get("date_from") or "").strip()).isoformat()
                date_to = date.fromisoformat((row.get("date_to") or "").strip()).isoformat()
            except ValueError:
                raise ValueError(f"row {line}: date_from/date_to must be YYYY-MM-DD") from None
            if not product_id or not keyword:
                raise ValueError(f"row {line}: product_id and keyword are required")
            if market not in MARKET_LOCATIONS:
                raise ValueError(f"row {line}: market_code must be US, CA or GB")
            if date_to < date_from:
                raise ValueError(f"row {line}: date_to is earlier than date_from")
            if search_type not in ALLOWED_TYPES:
                raise ValueError(f"row {line}: unsupported search_type {search_type!r}")
            if len(keyword) < 2 or len(keyword) > 100 or any(
                char in FORBIDDEN_KEYWORD_CHARS for char in keyword
            ):
                raise ValueError(f"row {line}: keyword violates DataForSEO keyword limits")
            if not language or len(language) > 8:
                raise ValueError(f"row {line}: invalid language_code")
            comparison_set = (row.get("comparison_set") or "default").strip() or "default"
            key = (
                product_id, market, date_from, date_to, search_type, language, comparison_set
            )
            row_key = (*key, keyword.casefold())
            if row_key in seen:
                continue
            seen.add(row_key)
            grouped[key].append(keyword)

    if not grouped:
        raise ValueError("DataForSEO query CSV contains no data rows")

    with connect(db_path) as conn:
        known_products = {
            row["product_id"] for row in conn.execute("SELECT product_id FROM products")
        }
    unknown = sorted({key[0] for key in grouped} - known_products)
    if unknown:
        raise ValueError(f"Unknown product_id values: {', '.join(unknown)}")

    groups: list[TrendQueryGroup] = []
    for key, keywords in sorted(grouped.items()):
        product_id, market, date_from, date_to, search_type, language, comparison_set = key
        for offset in range(0, len(keywords), 5):
            batch = tuple(keywords[offset:offset + 5])
            groups.append(
                TrendQueryGroup(
                    product_id=product_id,
                    market_code=market,
                    keywords=batch,
                    date_from=date_from,
                    date_to=date_to,
                    search_type=search_type,
                    language_code=language,
                    comparison_set=comparison_set,
                    comparison_group=_comparison_group(
                        market, search_type, date_from, date_to, comparison_set, batch
                    ),
                )
            )
    return groups


def build_provider_task_tag(group: TrendQueryGroup) -> str:
    return f"ftr-{hashlib.sha1(group.comparison_group.encode('utf-8')).hexdigest()[:20]}"


def _assert_provider_ok(payload: dict, operation: str) -> None:
    status_code = payload.get("status_code")
    if status_code != 20000:
        raise DataForSEOError(
            f"DataForSEO {operation} failed ({status_code}): "
            f"{payload.get('status_message', 'unknown provider error')}"
        )


def submit_trend_tasks(
    db_path: str | Path,
    query_file: str | Path,
    *,
    client: DataForSEOApiClient | Any | None = None,
    max_tasks: int | None = None,
) -> dict:
    initialize(db_path)
    groups = load_query_plan(query_file, db_path)
    budget = max_tasks if max_tasks is not None else int(
        os.getenv("DATAFORSEO_DAILY_TASK_BUDGET", "5")
    )
    if budget < 1:
        raise ValueError("DATAFORSEO_DAILY_TASK_BUDGET must be >= 1")
    if len(groups) > budget:
        raise ValueError(
            f"Query plan contains {len(groups)} billable tasks, exceeding the daily safety "
            f"budget of {budget}; split the plan or explicitly raise the budget."
        )
    api = client or DataForSEOApiClient(
        os.getenv("DATAFORSEO_LOGIN", ""),
        os.getenv("DATAFORSEO_PASSWORD", ""),
    )
    run_id = str(uuid4())
    submitted: list[dict] = []
    errors: list[str] = []
    with connect(db_path) as conn:
        ensure_source_tables(conn)
        start_source_run(
            conn,
            run_id,
            SOURCE_NAME,
            groups[0].market_code if len({g.market_code for g in groups}) == 1 else None,
            len(groups),
            {
                "query_file": str(Path(query_file).resolve()),
                "method": "standard_task_post",
                "max_tasks": budget,
                "comparison_groups": [group.comparison_group for group in groups],
            },
        )
        for group in groups:
            request_id = None
            try:
                request_id = reserve_api_request(
                    conn,
                    run_id,
                    "dataforseo",
                    "google_trends.explore.task_post",
                    "billable_task_posts",
                    budget,
                    1,
                )
                response = api.post(
                    POST_ENDPOINT,
                    [group.request_payload(build_provider_task_tag(group))],
                )
                _assert_provider_ok(response, "task_post")
                tasks = response.get("tasks") or []
                if not tasks:
                    raise DataForSEOError("DataForSEO task_post returned no task entry")
                task = tasks[0]
                task_code = int(task.get("status_code") or 0)
                if task_code >= 40000:
                    raise DataForSEOError(
                        f"DataForSEO task_post task error ({task_code}): "
                        f"{task.get('status_message', 'unknown task error')}"
                    )
                task_id = str(task.get("id") or "")
                if not task_id:
                    raise DataForSEOError("DataForSEO task_post did not return a task id")
                cost = task.get("cost")
                conn.execute(
                    """INSERT INTO dataforseo_tasks(
                       task_id,run_id,product_id,market_code,keywords_json,comparison_group,
                       search_type,date_from,date_to,request_json,status,submitted_at,cost_usd)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(task_id) DO NOTHING""",
                    (
                        task_id,
                        run_id,
                        group.product_id,
                        group.market_code,
                        json.dumps(list(group.keywords), ensure_ascii=False),
                        group.comparison_group,
                        group.search_type,
                        group.date_from,
                        group.date_to,
                        json.dumps(group.request_payload(build_provider_task_tag(group)),
                                   ensure_ascii=False, sort_keys=True),
                        "submitted",
                        datetime.now(timezone.utc).isoformat(),
                        float(cost) if cost is not None else None,
                    ),
                )
                finish_api_request(conn, request_id, True)
                submitted.append(
                    {"task_id": task_id, "comparison_group": group.comparison_group,
                     "keywords": list(group.keywords), "cost_usd": cost}
                )
            except Exception as exc:
                if request_id is not None:
                    finish_api_request(conn, request_id, False, type(exc).__name__)
                errors.append(f"{group.comparison_group}: {exc}")
                if isinstance(exc, QuotaBudgetExceeded):
                    break
        status = "pending_results" if submitted and not errors else (
            "partial" if submitted else "failed"
        )
        finish_source_run(
            conn,
            run_id,
            status,
            items_seen=len(submitted),
            items_saved=0,
            errors=errors,
            metadata={"submitted_tasks": submitted, "pending_results": len(submitted)},
        )
    return {
        "status": status,
        "run_id": run_id,
        "planned_tasks": len(groups),
        "submitted_tasks": submitted,
        "error_count": len(errors),
        "errors": errors,
        "note": "Standard tasks are asynchronous; fetch completed results separately.",
    }


def parse_trend_task_result(
    task: dict, task_metadata: dict, captured_at: str | None = None
) -> tuple[list[dict], int]:
    """Normalize Google Trends graph values into one row per keyword and period."""
    keywords = json.loads(task_metadata["keywords_json"])
    market = task_metadata["market_code"]
    product_id = task_metadata["product_id"]
    comparison_group = task_metadata["comparison_group"]
    task_id = task.get("id", "")
    results = task.get("result") or []
    rows: list[dict] = []
    missing_count = 0
    check_url = ""
    capture_time = captured_at or datetime.now(timezone.utc).isoformat()
    for result in results:
        check_url = result.get("check_url") or check_url
        result_keywords = result.get("keywords") or keywords
        for item in result.get("items") or []:
            if item.get("type") != "google_trends_graph":
                continue
            item_keywords = item.get("keywords") or result_keywords
            for point in item.get("data") or []:
                values = point.get("values")
                if not isinstance(values, list):
                    missing_count += len(item_keywords)
                    continue
                if len(values) != len(item_keywords):
                    raise DataForSEOError(
                        f"DataForSEO task {task_id} returned {len(values)} values for "
                        f"{len(item_keywords)} keywords"
                    )
                for keyword, raw_value in zip(item_keywords, values):
                    if point.get("missing_data") or raw_value is None:
                        missing_count += 1
                        continue
                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError):
                        raise DataForSEOError("DataForSEO returned a non-numeric trend value") from None
                    if not 0 <= value <= 100:
                        raise DataForSEOError(f"DataForSEO trend value outside 0-100: {value}")
                    period_start = date.fromisoformat(point["date_from"]).isoformat()
                    period_end = date.fromisoformat(point["date_to"]).isoformat()
                    note_parts = [
                        "provider=DataForSEO",
                        f"task_id={task_id}",
                        f"search_type={task_metadata.get('search_type', 'web')}",
                    ]
                    if value == 0:
                        note_parts.append(
                            "possible_below_reporting_threshold=true; provider defines 0 as "
                            "insufficient data for the term"
                        )
                    result_url = check_url or (
                        "https://trends.google.com/trends/explore?"
                        + urlencode({
                            "geo": market,
                            "date": f"{task_metadata['date_from']} {task_metadata['date_to']}",
                            "q": ",".join(keywords),
                        })
                    )
                    rows.append(
                        {
                            "source_name": SOURCE_NAME,
                            "observation_type": "trend",
                            "market_code": market,
                            "product_id": product_id,
                            "keyword": keyword,
                            "metric_name": "interest_index",
                            "metric_value": value,
                            "metric_unit": "index_0_100",
                            "period_start": period_start,
                            "period_end": period_end,
                            "comparison_group": comparison_group,
                            "source_url": result_url,
                            "captured_at": capture_time,
                            "collection_method": "authorized_provider_api",
                            "notes": "; ".join(note_parts),
                            "is_synthetic": 0,
                        }
                    )
    return rows, missing_count


def _persist_observations(conn: sqlite3.Connection, rows: list[dict]) -> int:
    saved = 0
    for item in rows:
        identity = "|".join(
            str(item[key] or "") for key in (
                "source_name", "market_code", "product_id", "keyword", "metric_name",
                "period_start", "period_end", "comparison_group", "source_url",
            )
        )
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


def fetch_trend_task_results(
    db_path: str | Path,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    client: DataForSEOApiClient | Any | None = None,
) -> dict:
    if not run_id and not task_id:
        raise ValueError("Provide run_id or task_id")
    initialize(db_path)
    api = client or DataForSEOApiClient(
        os.getenv("DATAFORSEO_LOGIN", ""),
        os.getenv("DATAFORSEO_PASSWORD", ""),
    )
    completed = 0
    pending: list[str] = []
    failed: list[str] = []
    saved_total = 0
    missing_total = 0
    touched_runs: set[str] = set()
    with connect(db_path) as conn:
        ensure_source_tables(conn)
        where = "status IN ('submitted','pending')"
        params: list[str] = []
        if task_id:
            where += " AND task_id=?"
            params.append(task_id)
        if run_id:
            where += " AND run_id=?"
            params.append(run_id)
        tasks = conn.execute(
            f"SELECT * FROM dataforseo_tasks WHERE {where} ORDER BY submitted_at",
            params,
        ).fetchall()
        result_fetch_budget = int(os.getenv("DATAFORSEO_DAILY_RESULT_FETCH_BUDGET", "2000"))
        for metadata in tasks:
            touched_runs.add(metadata["run_id"])
            request_id = reserve_api_request(
                conn,
                metadata["run_id"],
                "dataforseo",
                "google_trends.explore.task_get",
                "free_result_fetches",
                result_fetch_budget,
                1,
            )
            try:
                endpoint = f"{GET_ENDPOINT}/{metadata['task_id']}"
                response = api.get(endpoint)
                _assert_provider_ok(response, "task_get")
                task_entries = response.get("tasks") or []
                if not task_entries:
                    raise DataForSEOError("DataForSEO task_get returned no task entry")
                task = task_entries[0]
                code = int(task.get("status_code") or 0)
                if code >= 40000:
                    raise DataForSEOError(
                        f"DataForSEO task_get task error ({code}): "
                        f"{task.get('status_message', 'unknown task error')}"
                    )
                if not task.get("result"):
                    finish_api_request(conn, request_id, True)
                    conn.execute(
                        "UPDATE dataforseo_tasks SET status='pending' WHERE task_id=?",
                        (metadata["task_id"],),
                    )
                    pending.append(metadata["task_id"])
                    continue
                normalized, missing = parse_trend_task_result(task, dict(metadata))
                saved = _persist_observations(conn, normalized)
                cost = task.get("cost")
                conn.execute(
                    """UPDATE dataforseo_tasks SET status='completed',fetched_at=?,
                       result_points=?,missing_points=?,cost_usd=COALESCE(?,cost_usd),
                       error_message='' WHERE task_id=?""",
                    (
                        datetime.now(timezone.utc).isoformat(), saved, missing,
                        float(cost) if cost is not None else None, metadata["task_id"],
                    ),
                )
                finish_api_request(conn, request_id, True)
                saved_total += saved
                missing_total += missing
                completed += 1
            except Exception as exc:
                finish_api_request(conn, request_id, False, type(exc).__name__)
                conn.execute(
                    "UPDATE dataforseo_tasks SET status='failed',error_message=? WHERE task_id=?",
                    (str(exc)[:500], metadata["task_id"]),
                )
                failed.append(metadata["task_id"])
        for touched_run in touched_runs:
            counts = conn.execute(
                """SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('submitted','pending') THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                   COALESCE(SUM(result_points),0) AS saved
                   FROM dataforseo_tasks WHERE run_id=?""",
                (touched_run,),
            ).fetchone()
            run_status = "pending_results" if counts["pending"] else (
                "partial" if counts["failed"] else "success"
            )
            conn.execute(
                """UPDATE source_runs SET status=?,finished_at=?,items_seen=?,
                   items_saved=?,metadata_json=json_set(metadata_json,'$.pending_results',?,
                   '$.failed_tasks',?) WHERE run_id=?""",
                (
                    run_status, datetime.now(timezone.utc).isoformat(),
                    counts["total"], counts["saved"], counts["pending"], counts["failed"],
                    touched_run,
                ),
            )
    return {
        "status": "pending_results" if pending else ("partial" if failed else "success"),
        "completed_tasks": completed,
        "observations_saved": saved_total,
        "missing_points": missing_total,
        "pending_task_ids": pending,
        "failed_task_ids": failed,
    }
