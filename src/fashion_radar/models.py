from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MarketCode = Literal["US", "CA", "GB"]
Category = Literal["sneaker", "bag"]


class ProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_id: str = Field(min_length=2, max_length=80)
    category: Category
    subcategory: str = Field(min_length=2, max_length=80)
    brand: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=180)
    canonical_name: str = Field(min_length=1, max_length=220)
    style_code: str | None = None
    aliases: str = ""
    colorway: str | None = None
    material: str | None = None
    audience_tag: str = "unisex"
    style_tags: str = ""
    release_date: date | None = None
    identity_confidence: float = Field(default=1.0, ge=0, le=1)


class SignalInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    signal_id: str = Field(min_length=3, max_length=160)
    product_id: str
    platform: str = Field(min_length=2, max_length=40)
    market_code: MarketCode
    external_content_id: str | None = None
    content_url: str
    published_at: datetime
    observed_at: datetime | None = None
    content_type: str = "unknown"
    caption: str = ""
    hashtags: str = ""
    creator_key: str | None = None
    source_method: str = "manual"
    data_quality: float = Field(default=1.0, ge=0, le=1)
    is_synthetic: bool = False

    @field_validator("published_at", "observed_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class MetricSnapshotInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    signal_id: str
    snapshot_at: datetime
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    creator_followers: int | None = Field(default=None, ge=0)
    metric_source: str = "manual"
    is_synthetic: bool = False
    is_synthetic: bool = False

    @field_validator("snapshot_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class SearchSnapshotInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_id: str
    market_code: MarketCode
    keyword: str
    period_start: date
    period_end: date
    interest_index: float = Field(ge=0, le=100)
    source: str = "google_trends_manual"
    comparison_group: str = ""
    captured_at: datetime | None = None
    is_synthetic: bool = False

    @model_validator(mode="after")
    def check_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot be earlier than period_start")
        return self

    @field_validator("captured_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class AudienceInsightInput(BaseModel):
    product_id: str
    market_code: MarketCode
    topic: str
    intent_type: Literal["price_question", "purchase_intent", "comparison", "objection", "usage_feedback", "other"]
    mention_count: int = Field(ge=0)
    sample_size: int = Field(ge=0)
    source_url: str = ""
    confidence: float = Field(default=0.7, ge=0, le=1)
    observed_at: datetime | None = None
    is_synthetic: bool = False

    @model_validator(mode="after")
    def check_counts(self):
        if self.mention_count > self.sample_size:
            raise ValueError("mention_count cannot exceed sample_size")
        return self

    @field_validator("observed_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ManualAssessmentInput(BaseModel):
    product_id: str
    market_code: MarketCode
    visual_potential: int | None = Field(default=None, ge=0, le=100)
    angle_richness: int | None = Field(default=None, ge=0, le=100)
    production_ease: int | None = Field(default=None, ge=0, le=100)
    competition_saturation: int | None = Field(default=None, ge=0, le=100)
    notes: str = ""
    assessed_at: datetime | None = None
    is_synthetic: bool = False

    @field_validator("assessed_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ScoreResult(BaseModel):
    product_id: str
    market_code: MarketCode
    score_date: date
    model_version: str
    total_score: float
    data_coverage: float
    evidence_grade: Literal["A", "B", "C"]
    recommendation: str
    component_scores: dict[str, float | None]
    reasons: list[str]
    limitations: list[str]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContentExperimentInput(BaseModel):
    experiment_id: str = Field(min_length=3, max_length=160)
    product_id: str
    platform: str
    market_code: MarketCode
    content_angle: str
    hook: str = ""
    post_url: str = ""
    published_at: datetime | None = None
    video_length_seconds: int | None = Field(default=None, ge=1)
    format: str = "short_video"
    notes: str = ""
    is_synthetic: bool = False

    @field_validator("published_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ExperimentSnapshotInput(BaseModel):
    experiment_id: str
    snapshot_at: datetime
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    profile_visits: int | None = Field(default=None, ge=0)
    follows: int | None = Field(default=None, ge=0)
    metric_source: str = "manual"
    is_synthetic: bool = False

    @field_validator("snapshot_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
