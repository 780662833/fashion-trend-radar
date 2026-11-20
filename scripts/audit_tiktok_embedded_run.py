from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIKTOK_SOURCE_NAME = "tiktok_dtk_embedded"


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


def find_latest_run_id(
    conn: sqlite3.Connection,
    source_name: str = TIKTOK_SOURCE_NAME,
) -> str | None:
    """Return the newest TikTok embedded run, including failed/partial attempts."""
    row = conn.execute(
        """
        SELECT run_id
        FROM source_runs
        WHERE source_name = ?
        ORDER BY datetime(started_at) DESC, run_id DESC
        LIMIT 1
        """,
        (source_name,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _valid_tiktok_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.tiktok.com"
        and parsed.path.startswith("/")
    )


def audit_run(
    conn: sqlite3.Connection,
    run_id: str,
    sample_limit: int = 10,
) -> dict[str, Any]:
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required_tables = {
        "source_runs",
        "api_request_log",
        "tiktok_content_candidates",
        "social_signals",
        "metric_snapshots",
    }
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
    if run["source_name"] != TIKTOK_SOURCE_NAME:
        return {
            "status": "source_mismatch",
            "run_id": run_id,
            "source_name": run["source_name"],
            "expected_source_name": TIKTOK_SOURCE_NAME,
        }

    metadata = _decode_json(run.get("metadata_json"), {})
    if not isinstance(metadata, dict):
        metadata = {}

    candidate_summary_row = conn.execute(
        """
        SELECT
            COUNT(*) AS candidate_count,
            SUM(CASE WHEN link_status = 'needs_review' THEN 1 ELSE 0 END)
                AS needs_review_count,
            SUM(CASE WHEN link_status = 'approved' THEN 1 ELSE 0 END)
                AS approved_count,
            SUM(CASE WHEN link_status = 'rejected' THEN 1 ELSE 0 END)
                AS rejected_count,
            SUM(CASE WHEN market_basis = 'research_context'
                       AND audience_market_code IS NULL THEN 1 ELSE 0 END)
                AS research_context_count
        FROM tiktok_content_candidates
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    candidate_summary = {
        key: int(candidate_summary_row[key] or 0)
        for key in (
            "candidate_count",
            "needs_review_count",
            "approved_count",
            "rejected_count",
            "research_context_count",
        )
    }

    candidate_rows = conn.execute(
        """
        SELECT candidate_id, market_code, market_basis, audience_market_code,
               seed_type, seed_value, suggested_product_id, link_status,
               external_content_id, content_url, published_at, views, likes,
               comments, shares, saves, observed_at
        FROM tiktok_content_candidates
        WHERE run_id = ?
        ORDER BY observed_at, market_code, external_content_id
        """,
        (run_id,),
    ).fetchall()
    invalid_urls = [
        {
            "candidate_id": row["candidate_id"],
            "external_content_id": row["external_content_id"],
            "content_url": row["content_url"],
        }
        for row in candidate_rows
        if not _valid_tiktok_url(row["content_url"])
    ]

    duplicate_rows = conn.execute(
        """
        SELECT market_code, external_content_id, COUNT(*) AS duplicate_count
        FROM tiktok_content_candidates
        WHERE run_id = ?
        GROUP BY market_code, external_content_id
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC
        LIMIT 100
        """,
        (run_id,),
    ).fetchall()

    attribution_violations = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM tiktok_content_candidates
            WHERE run_id = ?
              AND (market_basis != 'research_context'
                   OR audience_market_code IS NOT NULL)
            """,
            (run_id,),
        ).fetchone()[0]
    )

    unreviewed_signal_count = int(
        conn.execute(
            """
            SELECT COUNT(DISTINCT c.candidate_id)
            FROM tiktok_content_candidates AS c
            JOIN social_signals AS s
              ON s.platform = 'tiktok'
             AND s.market_code = c.market_code
             AND s.external_content_id = c.external_content_id
            WHERE c.run_id = ?
              AND c.link_status = 'needs_review'
            """,
            (run_id,),
        ).fetchone()[0]
    )
    unreviewed_metric_count = int(
        conn.execute(
            """
            SELECT COUNT(DISTINCT m.snapshot_id)
            FROM tiktok_content_candidates AS c
            JOIN social_signals AS s
              ON s.platform = 'tiktok'
             AND s.market_code = c.market_code
             AND s.external_content_id = c.external_content_id
            JOIN metric_snapshots AS m ON m.signal_id = s.signal_id
            WHERE c.run_id = ?
              AND c.link_status = 'needs_review'
            """,
            (run_id,),
        ).fetchone()[0]
    )

    request_row = conn.execute(
        """
        SELECT
            COUNT(*) AS request_count,
            SUM(CASE WHEN succeeded = 1 THEN 1 ELSE 0 END) AS succeeded_count,
            SUM(CASE WHEN succeeded = 0 THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN succeeded IS NULL THEN 1 ELSE 0 END) AS unfinished_count,
            COUNT(DISTINCT provider) AS provider_count
        FROM api_request_log
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    request_summary = {
        key: int(request_row[key] or 0)
        for key in (
            "request_count",
            "succeeded_count",
            "failed_count",
            "unfinished_count",
            "provider_count",
        )
    }
    request_methods = [
        dict(row)
        for row in conn.execute(
            """
            SELECT provider, method, COUNT(*) AS request_count,
                   SUM(CASE WHEN succeeded = 1 THEN 1 ELSE 0 END) AS succeeded_count,
                   SUM(CASE WHEN succeeded = 0 THEN 1 ELSE 0 END) AS failed_count,
                   SUM(CASE WHEN succeeded IS NULL THEN 1 ELSE 0 END) AS unfinished_count
            FROM api_request_log
            WHERE run_id = ?
            GROUP BY provider, method
            ORDER BY provider, method
            """,
            (run_id,),
        ).fetchall()
    ]

    expected_requests = metadata.get("requests_made")
    try:
        expected_requests = int(expected_requests)
    except (TypeError, ValueError):
        expected_requests = None

    items_seen = int(run.get("items_seen") or 0)
    items_saved = int(run.get("items_saved") or 0)
    candidate_count = candidate_summary["candidate_count"]
    request_count = request_summary["request_count"]

    checks = {
        "source_run_finished": bool(run.get("finished_at")),
        "run_uses_anonymous_guest_mode": (
            metadata.get("identity_mode") == "anonymous_guest_no_cookies"
        ),
        "candidate_count_matches_source_run_items_saved": (
            candidate_count == items_saved
        ),
        "items_saved_not_greater_than_items_seen": items_saved <= items_seen,
        "request_count_matches_recorded_requests_made": (
            expected_requests is not None and request_count == expected_requests
        ),
        "at_least_one_provider_request_logged": request_count > 0,
        "all_logged_requests_finalized": (
            request_summary["unfinished_count"] == 0
            and request_summary["succeeded_count"] + request_summary["failed_count"]
            == request_count
        ),
        "all_logged_requests_succeeded": (
            request_summary["failed_count"] == 0
            and request_summary["unfinished_count"] == 0
            and request_summary["succeeded_count"] == request_count
        ),
        "all_request_providers_match_source": (
            request_summary["provider_count"] <= 1
            and (
                request_summary["provider_count"] == 0
                or conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM api_request_log
                    WHERE run_id = ? AND provider != ?
                    """,
                    (run_id, TIKTOK_SOURCE_NAME),
                ).fetchone()[0] == 0
            )
        ),
        "all_candidate_urls_are_tiktok_https_urls": len(invalid_urls) == 0,
        "no_duplicate_market_content_identity_groups": len(duplicate_rows) == 0,
        "all_candidates_keep_market_as_research_context": (
            attribution_violations == 0
            and candidate_summary["research_context_count"] == candidate_count
        ),
        "unreviewed_candidates_not_in_social_signals": (
            unreviewed_signal_count == 0
        ),
        "unreviewed_candidates_have_no_metric_snapshots": (
            unreviewed_metric_count == 0
        ),
        "source_run_status_success": run.get("status") == "success",
    }

    status = "checks_consistent" if all(checks.values()) else "mismatch"
    samples = [
        {
            key: row[key]
            for key in (
                "candidate_id",
                "market_code",
                "market_basis",
                "seed_type",
                "seed_value",
                "suggested_product_id",
                "link_status",
                "external_content_id",
                "content_url",
                "published_at",
                "views",
                "likes",
                "comments",
                "shares",
                "saves",
            )
        }
        for row in candidate_rows[:sample_limit]
    ]

    return {
        "status": status,
        "run_id": run_id,
        "source_run": {
            "source_name": run["source_name"],
            "market_code": run.get("market_code"),
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "seed_count": int(run.get("query_count") or 0),
            "items_seen": items_seen,
            "items_saved": items_saved,
            "errors": _decode_json(run.get("errors_json"), []),
            "metadata": metadata,
        },
        "candidate_audit": {
            **candidate_summary,
            "invalid_candidate_urls": invalid_urls,
            "duplicate_identity_groups": [dict(row) for row in duplicate_rows],
            "market_attribution_violations": attribution_violations,
            "unreviewed_candidate_social_signal_matches": unreviewed_signal_count,
            "unreviewed_candidate_metric_snapshot_matches": unreviewed_metric_count,
        },
        "api_request_log": {
            **request_summary,
            "methods": request_methods,
        },
        "checks": checks,
        "samples": samples,
        "limitations": [
            "market_code is the seed research context, not verified creator or viewer geography.",
            "Candidate rows remain a manual-review queue and are not product-score evidence until a separate approved promotion workflow exists.",
            "A successful run does not establish platform-wide representativeness or ongoing endpoint availability.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of one TikTok embedded guest collection run. "
            "No schema migration, network request or data write is performed."
        )
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--run-id", help="Audit this exact TikTok source run ID.")
    selection.add_argument(
        "--latest",
        action="store_true",
        help="Audit the latest TikTok embedded run, including failed or partial runs.",
    )
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()

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
        db_uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            selected_run_id = (
                find_latest_run_id(conn) if args.latest else args.run_id
            )
            if selected_run_id is None:
                result = {
                    "status": "not_found",
                    "run_id": None,
                    "error": (
                        "No source_runs row was found for "
                        f"{TIKTOK_SOURCE_NAME!r}."
                    ),
                }
            else:
                result = audit_run(
                    conn,
                    selected_run_id,
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
