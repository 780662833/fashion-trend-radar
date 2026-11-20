from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_db_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _decode_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _rows_as_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def audit_run(
    conn: sqlite3.Connection,
    run_id: str,
    expected_points_per_keyword: int | None = None,
    sample_limit: int = 10,
) -> dict[str, Any]:
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required_tables = {"source_runs", "source_observations", "api_request_log"}
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        raise RuntimeError(
            "Database is missing required tables: " + ", ".join(missing_tables)
        )

    run_row = conn.execute(
        "SELECT * FROM source_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run_row is None:
        return {
            "status": "not_found",
            "run_id": run_id,
            "error": "No matching source_runs row was found.",
        }

    run = dict(run_row)
    source_name = run["source_name"]
    started_at = run["started_at"]
    finished_at = run["finished_at"]
    observation_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(source_observations)")
    }
    direct_run_id = "run_id" in observation_columns

    # source_observations currently has no run_id. Use the source and run time
    # window as a clearly labelled approximation until a direct FK is added.
    observation_filters = ["source_name = ?"]
    observation_params: list[Any] = [source_name]
    if direct_run_id:
        observation_filters.append("run_id = ?")
        observation_params.append(run_id)
    else:
        if not started_at or not finished_at:
            raise RuntimeError(
                "The source run has no finished time; cannot perform a time-window audit."
            )
        observation_filters.append(
            "datetime(captured_at) BETWEEN datetime(?) AND datetime(?)"
        )
        observation_params.extend([started_at, finished_at])

    if run.get("market_code"):
        observation_filters.append("market_code = ?")
        observation_params.append(run["market_code"])

    where_sql = " AND ".join(observation_filters)

    observation_count = conn.execute(
        f"SELECT COUNT(*) FROM source_observations WHERE {where_sql}",
        observation_params,
    ).fetchone()[0]

    grouped = conn.execute(
        f"""
        SELECT
            keyword,
            market_code,
            comparison_group,
            metric_name,
            metric_unit,
            COUNT(*) AS row_count,
            COUNT(DISTINCT period_start) AS date_count,
            COUNT(DISTINCT period_end) AS period_end_count,
            MIN(period_start) AS first_period,
            MAX(period_end) AS last_period,
            MIN(metric_value) AS min_value,
            MAX(metric_value) AS max_value,
            SUM(CASE WHEN metric_value = 0 THEN 1 ELSE 0 END) AS zero_count,
            SUM(CASE WHEN is_synthetic = 1 THEN 1 ELSE 0 END) AS synthetic_count,
            SUM(
                CASE WHEN metric_value < 0 OR metric_value > 100
                THEN 1 ELSE 0 END
            ) AS out_of_range_count
        FROM source_observations
        WHERE {where_sql}
        GROUP BY keyword, market_code, comparison_group, metric_name, metric_unit
        ORDER BY comparison_group, keyword
        """,
        observation_params,
    ).fetchall()
    keyword_groups = _rows_as_dicts(grouped)

    duplicates = conn.execute(
        f"""
        SELECT
            source_name,
            market_code,
            keyword,
            metric_name,
            period_start,
            period_end,
            comparison_group,
            source_url,
            COUNT(*) AS duplicate_count
        FROM source_observations
        WHERE {where_sql}
        GROUP BY
            source_name, market_code, keyword, metric_name,
            period_start, period_end, comparison_group, source_url
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC
        LIMIT 100
        """,
        observation_params,
    ).fetchall()

    api_rows = conn.execute(
        """
        SELECT
            COUNT(*) AS request_count,
            SUM(CASE WHEN succeeded = 1 THEN 1 ELSE 0 END) AS succeeded_count,
            SUM(CASE WHEN succeeded = 0 THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN succeeded IS NULL THEN 1 ELSE 0 END) AS unfinished_count
        FROM api_request_log
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    api_summary = {
        key: int(api_rows[key] or 0)
        for key in (
            "request_count",
            "succeeded_count",
            "failed_count",
            "unfinished_count",
        )
    }

    snapshot_summary: dict[str, int | None] = {
        "matching_rows": None,
    }
    if "search_snapshots" in tables and started_at and finished_at:
        snapshot_summary["matching_rows"] = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM search_snapshots
                WHERE source = ?
                  AND datetime(captured_at) BETWEEN datetime(?) AND datetime(?)
                """,
                (source_name, started_at, finished_at),
            ).fetchone()[0]
        )

    sample_rows = conn.execute(
        f"""
        SELECT
            observation_id, source_name, observation_type, market_code,
            product_id, keyword, metric_name, metric_value, metric_unit,
            period_start, period_end, comparison_group, source_url,
            captured_at, collection_method, is_synthetic, validation_status
        FROM source_observations
        WHERE {where_sql}
        ORDER BY captured_at, comparison_group, keyword, period_start
        LIMIT ?
        """,
        [*observation_params, sample_limit],
    ).fetchall()

    saved_expected = int(run.get("items_saved") or 0)
    query_expected = int(run.get("query_count") or 0)
    checks = {
        "observation_count_matches_source_run_items_saved":
            observation_count == saved_expected,
        "api_request_count_matches_source_run_query_count":
            api_summary["request_count"] == query_expected,
        "all_logged_requests_succeeded":
            api_summary["failed_count"] == 0
            and api_summary["unfinished_count"] == 0
            and api_summary["succeeded_count"] == api_summary["request_count"],
        "no_duplicate_observation_identity_groups": len(duplicates) == 0,
        "no_synthetic_observations": sum(
            int(item["synthetic_count"] or 0) for item in keyword_groups
        ) == 0,
        "all_interest_values_within_0_100": sum(
            int(item["out_of_range_count"] or 0) for item in keyword_groups
        ) == 0,
    }

    if expected_points_per_keyword is not None:
        checks["each_keyword_has_expected_number_of_points"] = bool(
            keyword_groups
        ) and all(
            int(item["row_count"]) == expected_points_per_keyword
            and int(item["date_count"]) == expected_points_per_keyword
            for item in keyword_groups
        )

    if snapshot_summary["matching_rows"] is not None:
        checks["search_snapshot_count_matches_observation_count"] = (
            snapshot_summary["matching_rows"] == observation_count
        )

    return {
        "status": "checks_consistent" if all(checks.values()) else "mismatch",
        "run_id": run_id,
        "source_run": {
            "source_name": source_name,
            "market_code": run.get("market_code"),
            "status": run.get("status"),
            "started_at": started_at,
            "finished_at": finished_at,
            "query_count": query_expected,
            "items_seen": int(run.get("items_seen") or 0),
            "items_saved": saved_expected,
            "errors": _decode_json(run.get("errors_json"), []),
            "metadata": _decode_json(run.get("metadata_json"), {}),
        },
        "observation_audit": {
            "association_method": (
                "direct_run_id" if direct_run_id else "source_and_time_window"
            ),
            "source_observations_has_run_id": direct_run_id,
            "matching_observation_count": int(observation_count),
            "keyword_group_count": len(keyword_groups),
            "keyword_groups": keyword_groups,
            "possible_duplicate_identity_groups": _rows_as_dicts(duplicates),
        },
        "api_request_log": api_summary,
        "search_snapshots": snapshot_summary,
        "checks": checks,
        "samples": _rows_as_dicts(sample_rows),
        "limitations": (
            []
            if direct_run_id
            else [
                "source_observations has no run_id column; observations are associated "
                "by source_name and captured_at within the run window.",
                "Time-window association is not a foreign-key proof and can include "
                "another concurrent run from the same source.",
            ]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of a completed trendspyg Google Trends source run. "
            "No schema migration or data writes are performed."
        )
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--expected-points-per-keyword",
        type=int,
        help="Optional expected point count for every keyword/comparison-group series.",
    )
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()

    if args.expected_points_per_keyword is not None and args.expected_points_per_keyword < 1:
        parser.error("--expected-points-per-keyword must be >= 1")
    if args.sample_limit < 0:
        parser.error("--sample-limit must be >= 0")

    db_path = _resolve_db_path(args.db)
    if not db_path.is_file():
        print(json.dumps({
            "status": "database_not_found",
            "database": str(db_path),
            "note": "Read-only mode; the script will not create an empty database.",
        }, ensure_ascii=False, indent=2))
        return 2

    try:
        # Path.as_uri handles Windows drive letters and spaces safely.
        db_uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            result = audit_run(
                conn,
                args.run_id,
                expected_points_per_keyword=args.expected_points_per_keyword,
                sample_limit=args.sample_limit,
            )
        finally:
            conn.close()
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        result = {
            "status": "audit_error",
            "database": str(db_path),
            "error": f"{type(exc).__name__}: {exc}",
        }

    result["database"] = str(db_path.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if result["status"] == "not_found":
        return 1
    return 0 if result["status"] == "checks_consistent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
