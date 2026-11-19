from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import NoneType
from typing import Callable, TypeVar, get_args

from pydantic import ValidationError

from .db import connect
from .models import (
    AudienceInsightInput,
    ContentExperimentInput,
    ExperimentSnapshotInput,
    ManualAssessmentInput,
    MetricSnapshotInput,
    ProductInput,
    SearchSnapshotInput,
    SignalInput,
)

T = TypeVar("T")


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _clean(row: dict[str, str], model: type[T]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in row.items():
        normalized = value.strip() if isinstance(value, str) else value
        field = model.model_fields.get(key)
        if normalized == "" and field is not None:
            if NoneType in get_args(field.annotation):
                cleaned[key] = None
                continue
            if not field.is_required():
                continue
        cleaned[key] = normalized
    return cleaned


def _import(path: str | Path, model: type[T], handler: Callable, db_path: str | Path) -> tuple[int, list[str]]:
    inserted = 0
    errors: list[str] = []
    rows = _read_csv(path)
    with connect(db_path) as conn:
        for index, raw in enumerate(rows, start=2):
            try:
                item = model.model_validate(_clean(raw, model))
                handler(conn, item)
                inserted += 1
            except (ValidationError, ValueError, KeyError, sqlite3.IntegrityError) as exc:
                errors.append(f"row {index}: {exc}")
    return inserted, errors


def import_products(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: ProductInput) -> None:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name,
               style_code,aliases,colorway,material,audience_tag,style_tags,release_date,identity_confidence)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id) DO UPDATE SET category=excluded.category,subcategory=excluded.subcategory,
               brand=excluded.brand,model_name=excluded.model_name,canonical_name=excluded.canonical_name,
               style_code=excluded.style_code,aliases=excluded.aliases,colorway=excluded.colorway,
               material=excluded.material,audience_tag=excluded.audience_tag,style_tags=excluded.style_tags,
               release_date=excluded.release_date,identity_confidence=excluded.identity_confidence,
               updated_at=CURRENT_TIMESTAMP""",
            (item.product_id,item.category,item.subcategory,item.brand,item.model_name,item.canonical_name,
             item.style_code,item.aliases,item.colorway,item.material,item.audience_tag,item.style_tags,
             item.release_date.isoformat() if item.release_date else None,item.identity_confidence),
        )
    return _import(path, ProductInput, save, db_path)


def import_signals(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: SignalInput) -> None:
        now = item.observed_at or datetime.now(timezone.utc)
        conn.execute(
            """INSERT INTO social_signals(signal_id,product_id,platform,market_code,external_content_id,
               content_url,published_at,observed_at,content_type,caption,hashtags,creator_key,source_method,
               data_quality,is_synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(signal_id) DO UPDATE SET product_id=excluded.product_id,platform=excluded.platform,
               market_code=excluded.market_code,content_url=excluded.content_url,published_at=excluded.published_at,
               observed_at=excluded.observed_at,content_type=excluded.content_type,caption=excluded.caption,
               hashtags=excluded.hashtags,creator_key=excluded.creator_key,source_method=excluded.source_method,
               data_quality=excluded.data_quality,is_synthetic=excluded.is_synthetic""",
            (item.signal_id,item.product_id,item.platform,item.market_code,item.external_content_id,
             item.content_url,item.published_at.isoformat(),now.isoformat(),item.content_type,item.caption,
             item.hashtags,item.creator_key,item.source_method,item.data_quality,int(item.is_synthetic)),
        )
    return _import(path, SignalInput, save, db_path)


def import_metrics(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: MetricSnapshotInput) -> None:
        conn.execute(
            """INSERT INTO metric_snapshots(signal_id,snapshot_at,views,likes,comments,shares,saves,
               creator_followers,metric_source,is_synthetic) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(signal_id,snapshot_at) DO UPDATE SET views=excluded.views,likes=excluded.likes,
               comments=excluded.comments,shares=excluded.shares,saves=excluded.saves,
               creator_followers=excluded.creator_followers,metric_source=excluded.metric_source,
               is_synthetic=excluded.is_synthetic""",
            (item.signal_id,item.snapshot_at.isoformat(),item.views,item.likes,item.comments,item.shares,
             item.saves,item.creator_followers,item.metric_source,int(item.is_synthetic)),
        )
    return _import(path, MetricSnapshotInput, save, db_path)


def import_search(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: SearchSnapshotInput) -> None:
        captured = item.captured_at or datetime.now(timezone.utc)
        conn.execute(
            """INSERT INTO search_snapshots(product_id,market_code,keyword,period_start,period_end,
               interest_index,source,captured_at,is_synthetic,comparison_group)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id,market_code,keyword,period_start,period_end,source,comparison_group)
               DO UPDATE SET interest_index=excluded.interest_index,captured_at=excluded.captured_at,
               is_synthetic=excluded.is_synthetic""",
            (item.product_id,item.market_code,item.keyword,item.period_start.isoformat(),item.period_end.isoformat(),
             item.interest_index,item.source,captured.isoformat(),int(item.is_synthetic),
             item.comparison_group),
        )
    return _import(path, SearchSnapshotInput, save, db_path)


def import_insights(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: AudienceInsightInput) -> None:
        observed = item.observed_at or datetime.now(timezone.utc)
        conn.execute(
            """INSERT INTO audience_insights(product_id,market_code,topic,intent_type,mention_count,
               sample_size,source_url,confidence,observed_at,is_synthetic) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id,market_code,topic,intent_type,source_url,observed_at) DO UPDATE SET
               mention_count=excluded.mention_count,sample_size=excluded.sample_size,
               confidence=excluded.confidence,is_synthetic=excluded.is_synthetic""",
            (item.product_id,item.market_code,item.topic,item.intent_type,item.mention_count,item.sample_size,
             item.source_url,item.confidence,observed.isoformat(),int(item.is_synthetic)),
        )
    return _import(path, AudienceInsightInput, save, db_path)


def import_assessments(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: ManualAssessmentInput) -> None:
        assessed = item.assessed_at or datetime.now(timezone.utc)
        conn.execute(
            """INSERT INTO manual_assessments(product_id,market_code,visual_potential,angle_richness,
               production_ease,competition_saturation,notes,assessed_at,is_synthetic) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id,market_code,assessed_at) DO UPDATE SET
               visual_potential=excluded.visual_potential,angle_richness=excluded.angle_richness,
               production_ease=excluded.production_ease,competition_saturation=excluded.competition_saturation,
               notes=excluded.notes,is_synthetic=excluded.is_synthetic""",
            (item.product_id,item.market_code,item.visual_potential,item.angle_richness,item.production_ease,
             item.competition_saturation,item.notes,assessed.isoformat(),int(item.is_synthetic)),
        )
    return _import(path, ManualAssessmentInput, save, db_path)


def import_experiments(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: ContentExperimentInput) -> None:
        conn.execute(
            """INSERT INTO content_experiments(experiment_id,product_id,platform,market_code,content_angle,
               hook,post_url,published_at,video_length_seconds,format,notes,is_synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(experiment_id) DO UPDATE SET product_id=excluded.product_id,platform=excluded.platform,
               market_code=excluded.market_code,content_angle=excluded.content_angle,hook=excluded.hook,
               post_url=excluded.post_url,published_at=excluded.published_at,
               video_length_seconds=excluded.video_length_seconds,format=excluded.format,notes=excluded.notes,
               is_synthetic=excluded.is_synthetic""",
            (item.experiment_id,item.product_id,item.platform,item.market_code,item.content_angle,item.hook,
             item.post_url,item.published_at.isoformat() if item.published_at else None,
             item.video_length_seconds,item.format,item.notes,int(item.is_synthetic)),
        )
    return _import(path, ContentExperimentInput, save, db_path)


def import_experiment_snapshots(path: str | Path, db_path: str | Path) -> tuple[int, list[str]]:
    def save(conn, item: ExperimentSnapshotInput) -> None:
        conn.execute(
            """INSERT INTO experiment_snapshots(experiment_id,snapshot_at,views,likes,comments,shares,saves,
               profile_visits,follows,metric_source,is_synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(experiment_id,snapshot_at) DO UPDATE SET views=excluded.views,likes=excluded.likes,
               comments=excluded.comments,shares=excluded.shares,saves=excluded.saves,
               profile_visits=excluded.profile_visits,follows=excluded.follows,metric_source=excluded.metric_source,
               is_synthetic=excluded.is_synthetic""",
            (item.experiment_id,item.snapshot_at.isoformat(),item.views,item.likes,item.comments,item.shares,
             item.saves,item.profile_visits,item.follows,item.metric_source,int(item.is_synthetic)),
        )
    return _import(path, ExperimentSnapshotInput, save, db_path)
