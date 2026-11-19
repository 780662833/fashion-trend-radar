from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK(category IN ('sneaker','bag')),
    subcategory TEXT NOT NULL,
    brand TEXT NOT NULL,
    model_name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    style_code TEXT,
    aliases TEXT NOT NULL DEFAULT '',
    colorway TEXT,
    material TEXT,
    audience_tag TEXT NOT NULL DEFAULT 'unisex',
    style_tags TEXT NOT NULL DEFAULT '',
    release_date TEXT,
    identity_confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_market (
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
    currency TEXT NOT NULL,
    local_price REAL,
    market_search_terms TEXT NOT NULL DEFAULT '',
    market_status TEXT NOT NULL DEFAULT 'watching',
    source_url TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(product_id, market_code)
);
CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    platform TEXT NOT NULL,
    access_method TEXT NOT NULL,
    authorization_note TEXT NOT NULL DEFAULT '',
    terms_url TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_unique ON sources(source_name, platform, access_method);
CREATE TABLE IF NOT EXISTS social_signals (
    signal_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
    external_content_id TEXT,
    content_url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'unknown',
    caption TEXT NOT NULL DEFAULT '',
    hashtags TEXT NOT NULL DEFAULT '',
    creator_key TEXT,
    source_method TEXT NOT NULL,
    data_quality REAL NOT NULL DEFAULT 1.0,
    is_synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_signals_product_market ON social_signals(product_id, market_code);
CREATE INDEX IF NOT EXISTS idx_signals_published ON social_signals(published_at);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL REFERENCES social_signals(signal_id) ON DELETE CASCADE,
    snapshot_at TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    saves INTEGER,
    creator_followers INTEGER,
    metric_source TEXT NOT NULL DEFAULT 'manual',
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    UNIQUE(signal_id, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_metrics_signal_time ON metric_snapshots(signal_id, snapshot_at);
CREATE TABLE IF NOT EXISTS search_snapshots (
    search_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
    keyword TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    interest_index REAL NOT NULL CHECK(interest_index >= 0 AND interest_index <= 100),
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    comparison_group TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_search_product_market ON search_snapshots(product_id, market_code, period_end);
CREATE TABLE IF NOT EXISTS audience_insights (
    insight_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
    topic TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    mention_count INTEGER NOT NULL CHECK(mention_count >= 0),
    sample_size INTEGER NOT NULL CHECK(sample_size >= 0),
    source_url TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.7,
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_unique ON audience_insights(product_id,market_code,topic,intent_type,source_url,observed_at);
CREATE TABLE IF NOT EXISTS manual_assessments (
    assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
    visual_potential INTEGER,
    angle_richness INTEGER,
    production_ease INTEGER,
    competition_saturation INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    assessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assessments_unique ON manual_assessments(product_id,market_code,assessed_at);
CREATE TABLE IF NOT EXISTS scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
    score_date TEXT NOT NULL,
    model_version TEXT NOT NULL,
    total_score REAL NOT NULL,
    data_coverage REAL NOT NULL,
    evidence_grade TEXT NOT NULL CHECK(evidence_grade IN ('A','B','C')),
    recommendation TEXT NOT NULL,
    component_scores_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    UNIQUE(product_id, market_code, score_date, model_version)
);
CREATE TABLE IF NOT EXISTS content_experiments (
    experiment_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    market_code TEXT NOT NULL CHECK(market_code IN ('US','CA','GB')),
    content_angle TEXT NOT NULL,
    hook TEXT NOT NULL DEFAULT '',
    post_url TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    video_length_seconds INTEGER,
    format TEXT NOT NULL DEFAULT 'short_video',
    notes TEXT NOT NULL DEFAULT '',
    is_synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS experiment_snapshots (
    experiment_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES content_experiments(experiment_id) ON DELETE CASCADE,
    snapshot_at TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    saves INTEGER,
    profile_visits INTEGER,
    follows INTEGER,
    metric_source TEXT NOT NULL DEFAULT 'manual',
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    UNIQUE(experiment_id,snapshot_at)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(db_path: str | Path) -> None:
    source_rows = [
        ("TikTok Creative Center", "trend_discovery", "tiktok", "manual_or_permitted_access", "Use publicly available trend pages and keep the original URL; no unsupported scraping."),
        ("YouTube Data API", "social_content", "youtube", "official_api", "Use an authorized API key and respect API quota and terms."),
        ("Google Trends", "search_interest", "google_trends", "manual_csv_or_approved_api", "Relative interest index is not absolute search volume; API Alpha access is not assumed."),
        ("Reddit Data API", "community_discussion", "reddit", "oauth_api", "Use OAuth, comply with API terms, and honor deletion requirements."),
        ("Instagram", "visual_trend", "instagram", "manual_or_licensed_provider", "No unofficial scraping in the first release."),
        ("Pinterest Trends", "visual_trend", "pinterest", "manual_or_approved_api", "API availability may be restricted; manual observation is supported."),
        ("DataForSEO Google Trends", "search_interest", "google_trends", "authorized_provider_api", "Third-party Google Trends provider; preserve query group, cost metadata and source check URL."),
        ("Exolyt TikTok Analytics", "social_content", "tiktok", "authorized_provider_export", "Use vendor CSV/Sheets exports; do not assume an undocumented public API."),
        ("Public brand and sneaker news pages", "product_research", "web", "scrapy_allowlisted_public_pages", "Only crawl explicitly allowlisted public URLs; obey robots.txt and site terms."),
    ]
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        search_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(search_snapshots)")
        }
        if "comparison_group" not in search_columns:
            conn.execute(
                "ALTER TABLE search_snapshots ADD COLUMN comparison_group TEXT NOT NULL DEFAULT ''"
            )
        # A comparison group is part of the index identity. Without it, importing
        # a second independently-normalized Trends request overwrites the first.
        conn.execute("DROP INDEX IF EXISTS idx_search_unique")
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_search_unique_v2
               ON search_snapshots(product_id,market_code,keyword,period_start,period_end,
                                   source,comparison_group)"""
        )
        conn.executemany(
            "INSERT OR IGNORE INTO sources(source_name,source_type,platform,access_method,authorization_note) VALUES(?,?,?,?,?)",
            source_rows,
        )
