from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


class QuotaBudgetExceeded(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_source_tables(conn) -> None:
    """Idempotent additive migrations for source collection and provenance."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(search_snapshots)")}
    if "comparison_group" not in columns:
        conn.execute("ALTER TABLE search_snapshots ADD COLUMN comparison_group TEXT NOT NULL DEFAULT ''")
    signal_columns = {row["name"] for row in conn.execute("PRAGMA table_info(social_signals)")}
    if "source_query" not in signal_columns:
        conn.execute("ALTER TABLE social_signals ADD COLUMN source_query TEXT NOT NULL DEFAULT ''")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            run_id TEXT PRIMARY KEY, source_name TEXT NOT NULL, market_code TEXT,
            status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            query_count INTEGER NOT NULL DEFAULT 0, items_seen INTEGER NOT NULL DEFAULT 0,
            items_saved INTEGER NOT NULL DEFAULT 0, errors_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_source_runs_source_time ON source_runs(source_name, started_at);
        CREATE TABLE IF NOT EXISTS dataforseo_tasks (
            task_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES source_runs(run_id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
            keywords_json TEXT NOT NULL,
            comparison_group TEXT NOT NULL,
            search_type TEXT NOT NULL DEFAULT 'web',
            date_from TEXT NOT NULL,
            date_to TEXT NOT NULL,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'submitted',
            submitted_at TEXT NOT NULL,
            fetched_at TEXT,
            result_points INTEGER NOT NULL DEFAULT 0,
            missing_points INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL,
            error_message TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_dataforseo_tasks_run_status
            ON dataforseo_tasks(run_id,status,submitted_at);
        CREATE TABLE IF NOT EXISTS api_request_log (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES source_runs(run_id) ON DELETE CASCADE,
            provider TEXT NOT NULL, method TEXT NOT NULL, quota_bucket TEXT NOT NULL,
            quota_date_pt TEXT NOT NULL, quota_units INTEGER NOT NULL DEFAULT 1,
            attempted_at TEXT NOT NULL, finished_at TEXT, succeeded INTEGER,
            error_code TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_day
            ON api_request_log(provider, quota_bucket, quota_date_pt);
        CREATE TABLE IF NOT EXISTS youtube_video_references (
            reference_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
            video_id TEXT NOT NULL,
            search_query TEXT NOT NULL,
            content_url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            UNIQUE(product_id, market_code, video_id)
        );
        CREATE INDEX IF NOT EXISTS idx_youtube_refs_expiry ON youtube_video_references(expires_at);
        CREATE INDEX IF NOT EXISTS idx_youtube_refs_product_market
            ON youtube_video_references(product_id, market_code);
        CREATE TABLE IF NOT EXISTS source_observations (
            observation_id TEXT PRIMARY KEY, source_name TEXT NOT NULL,
            observation_type TEXT NOT NULL, market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
            product_id TEXT REFERENCES products(product_id) ON DELETE SET NULL,
            keyword TEXT NOT NULL, metric_name TEXT NOT NULL, metric_value REAL NOT NULL,
            metric_unit TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
            comparison_group TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL,
            supporting_content_url TEXT NOT NULL DEFAULT '', captured_at TEXT NOT NULL,
            collection_method TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
            is_synthetic INTEGER NOT NULL DEFAULT 0, validation_status TEXT NOT NULL DEFAULT 'accepted',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_name, market_code, keyword, metric_name, period_start,
                   period_end, comparison_group, source_url)
        );
        CREATE INDEX IF NOT EXISTS idx_source_observations_market_period
            ON source_observations(market_code, period_end);
        CREATE TABLE IF NOT EXISTS product_evidence (
            evidence_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            evidence_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, evidence_type, source_url)
        );
        CREATE INDEX IF NOT EXISTS idx_product_evidence_product
            ON product_evidence(product_id, is_primary);
        CREATE TABLE IF NOT EXISTS tiktok_content_candidates (
            candidate_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES source_runs(run_id) ON DELETE CASCADE,
            market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
            market_basis TEXT NOT NULL DEFAULT 'research_context',
            audience_market_code TEXT,
            seed_type TEXT NOT NULL,
            seed_value TEXT NOT NULL,
            suggested_product_id TEXT REFERENCES products(product_id) ON DELETE SET NULL,
            link_status TEXT NOT NULL DEFAULT 'needs_review'
                CHECK(link_status IN ('needs_review','approved','rejected')),
            external_content_id TEXT NOT NULL,
            content_url TEXT NOT NULL,
            published_at TEXT,
            content_type TEXT NOT NULL DEFAULT 'unknown',
            caption TEXT NOT NULL DEFAULT '',
            hashtags_json TEXT NOT NULL DEFAULT '[]',
            creator_key TEXT,
            creator_handle TEXT NOT NULL DEFAULT '',
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            saves INTEGER,
            observed_at TEXT NOT NULL,
            UNIQUE(run_id, market_code, external_content_id)
        );
        CREATE INDEX IF NOT EXISTS idx_tiktok_candidates_market_time
            ON tiktok_content_candidates(market_code, observed_at);
        CREATE INDEX IF NOT EXISTS idx_tiktok_candidates_product_review
            ON tiktok_content_candidates(suggested_product_id, link_status);
        """
    )
    observation_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(source_observations)")
    }
    if "product_id" not in observation_columns:
        conn.execute(
            "ALTER TABLE source_observations ADD COLUMN product_id TEXT "
            "REFERENCES products(product_id) ON DELETE SET NULL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_observations_product_market "
        "ON source_observations(product_id, market_code, period_end)"
    )


def start_source_run(conn, run_id: str, source_name: str, market_code: str | None,
                     query_count: int, metadata: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO source_runs(run_id,source_name,market_code,status,started_at,
           query_count,metadata_json) VALUES(?,?,?,?,?,?,?)""",
        (run_id, source_name, market_code, "running", utc_now().isoformat(),
         query_count, json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)),
    )


def finish_source_run(conn, run_id: str, status: str, items_seen: int = 0,
                      items_saved: int = 0, errors: list[str] | None = None,
                      metadata: dict | None = None) -> None:
    conn.execute(
        """UPDATE source_runs SET status=?,finished_at=?,items_seen=?,items_saved=?,
           errors_json=?,metadata_json=? WHERE run_id=?""",
        (status, utc_now().isoformat(), items_seen, items_saved,
         json.dumps(errors or [], ensure_ascii=False),
         json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), run_id),
    )


def reserve_api_request(conn, run_id: str, provider: str, method: str,
                        quota_bucket: str, daily_budget: int, quota_units: int = 1) -> int:
    """Reserve quota before the call; failed requests still count as attempts."""
    quota_date = utc_now().astimezone(PACIFIC).date().isoformat()
    used = conn.execute(
        """SELECT COALESCE(SUM(quota_units),0) FROM api_request_log
           WHERE provider=? AND quota_bucket=? AND quota_date_pt=?""",
        (provider, quota_bucket, quota_date),
    ).fetchone()[0]
    if int(used) + quota_units > daily_budget:
        raise QuotaBudgetExceeded(
            f"{provider} {quota_bucket} safety budget reached: {used}/{daily_budget} units "
            f"for {quota_date} Pacific Time"
        )
    cursor = conn.execute(
        """INSERT INTO api_request_log(run_id,provider,method,quota_bucket,quota_date_pt,
           quota_units,attempted_at) VALUES(?,?,?,?,?,?,?)""",
        (run_id, provider, method, quota_bucket, quota_date, quota_units, utc_now().isoformat()),
    )
    return int(cursor.lastrowid)


def finish_api_request(conn, request_id: int, succeeded: bool, error_code: str = "") -> None:
    conn.execute(
        "UPDATE api_request_log SET finished_at=?,succeeded=?,error_code=? WHERE request_id=?",
        (utc_now().isoformat(), int(succeeded), error_code[:120], request_id),
    )


def purge_expired_youtube_references(conn, now: datetime | None = None) -> int:
    """Delete public YouTube API reference rows once their 30-day retention expires."""
    instant = now or utc_now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    cutoff = instant.astimezone(timezone.utc).isoformat()
    cursor = conn.execute(
        "DELETE FROM youtube_video_references WHERE expires_at<=?",
        (cutoff,),
    )
    return int(cursor.rowcount)


def load_local_env(path: str = ".env") -> bool:
    """Load missing environment variables from a local, gitignored dotenv file.

    This deliberately supports a small KEY=value format and never overwrites an
    environment variable already supplied by the operating system or caller.
    """
    from pathlib import Path

    env_file = Path(path)
    if not env_file.is_file():
        return False
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        import os
        os.environ.setdefault(key, value)
    return True
