from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, date, datetime
from statistics import mean

from .models import ScoreResult

MODEL_VERSION = "rules-v1.0"
WEIGHTS = {
    "demand_evidence": 25.0,
    "trend_momentum_lifecycle": 20.0,
    "content_potential": 20.0,
    "market_spread": 15.0,
    "competition_gap": 10.0,
    "evidence_quality": 10.0,
}


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _scaled_log_growth(first: float, last: float) -> float:
    if first <= 0:
        return 50.0 if last > 0 else 0.0
    growth = math.log1p(max(0.0, last)) - math.log1p(max(0.0, first))
    return _clip(50.0 + growth * 35.0)


def _mean_available(values: list[float | None]) -> float | None:
    valid = [float(v) for v in values if v is not None]
    return mean(valid) if valid else None


def _latest_manual_assessment(conn: sqlite3.Connection, product_id: str, market: str):
    return conn.execute(
        """SELECT * FROM manual_assessments WHERE product_id=? AND market_code=?
           ORDER BY assessed_at DESC, assessment_id DESC LIMIT 1""",
        (product_id, market),
    ).fetchone()


def _score_one(conn: sqlite3.Connection, product_id: str, market: str, score_date: date) -> ScoreResult:
    product = conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
    signals = conn.execute(
        """SELECT * FROM social_signals WHERE product_id=? AND market_code=?
           AND date(published_at)<=date(?)""",
        (product_id, market, score_date.isoformat()),
    ).fetchall()
    search = conn.execute(
        """SELECT * FROM search_snapshots WHERE product_id=? AND market_code=?
           AND date(period_end)<=date(?) ORDER BY period_end""",
        (product_id, market, score_date.isoformat()),
    ).fetchall()
    insights = conn.execute(
        """SELECT * FROM audience_insights WHERE product_id=? AND market_code=?
           AND date(observed_at)<=date(?)""",
        (product_id, market, score_date.isoformat()),
    ).fetchall()
    assessment = _latest_manual_assessment(conn, product_id, market)

    components: dict[str, float | None] = {key: None for key in WEIGHTS}
    reasons: list[str] = []
    limitations: list[str] = []
    synthetic = bool(
        any(row["is_synthetic"] for row in signals + search + insights)
        or (assessment is not None and assessment["is_synthetic"])
    )

    # 1) Demand evidence: search change, independent creators and explicit intent.
    demand_parts: list[tuple[float, float]] = []
    search_by_comparison: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in search:
        comparison_group = row["comparison_group"] if "comparison_group" in row.keys() else ""
        key = (row["keyword"], row["source"], comparison_group)
        search_by_comparison.setdefault(key, []).append(row)
    search_growths: list[float] = []
    zero_points_excluded = 0
    for rows in search_by_comparison.values():
        # Google Trends documents a displayed 0 as insufficient data, not a
        # measured zero-search count. Exclude it from growth baselines.
        usable = [row for row in rows if float(row["interest_index"]) > 0]
        zero_points_excluded += len(rows) - len(usable)
        if len(usable) >= 2:
            search_growths.append(
                _scaled_log_growth(
                    float(usable[0]["interest_index"]),
                    float(usable[-1]["interest_index"]),
                )
            )
    if search_growths:
        search_score = mean(search_growths)
        demand_parts.append((search_score, 0.40))
        reasons.append(f"搜索趋势覆盖 {len(search_growths)} 个可比较关键词；记录共 {len(search)} 个。")
    elif search:
        limitations.append("搜索趋势只有单期记录（排除低于报告阈值的 0 值后）或分散在不同关键词，不能计算增长。")
    else:
        limitations.append("缺少搜索趋势数据。")
    if zero_points_excluded:
        limitations.append(
            f"有 {zero_points_excluded} 个 Google Trends 0 值被排除在增长计算之外；"
            "0 代表数据不足，不被解释为确定的零搜索。"
        )

    creators = {s["creator_key"] for s in signals if s["creator_key"]}
    if creators:
        creator_score = _clip(len(creators) / 5.0 * 100)
        demand_parts.append((creator_score, 0.30))
        reasons.append(f"观察到 {len(creators)} 个不同创作者发布相关内容。")
    elif signals:
        limitations.append("社媒内容缺少创作者标识，无法评估独立创作者数量。")

    if insights:
        total_mentions = sum(int(row["mention_count"]) for row in insights)
        sample_size = sum(int(row["sample_size"]) for row in insights)
        intent_mentions = sum(
            int(row["mention_count"])
            for row in insights
            if row["intent_type"] in {"price_question", "purchase_intent", "comparison"}
        )
        ratio = intent_mentions / max(sample_size, 1)
        intent_score = _clip(ratio * 250.0)
        demand_parts.append((intent_score, 0.30))
        reasons.append(f"评论/讨论样本 {sample_size} 条，识别到 {intent_mentions} 条购买相关信号。")
    else:
        limitations.append("缺少评论主题和购买意图样本。")

    if demand_parts:
        weight_sum = sum(weight for _, weight in demand_parts)
        components["demand_evidence"] = sum(value * weight for value, weight in demand_parts) / weight_sum

    # 2) Trend momentum and lifecycle.
    signal_ids = [s["signal_id"] for s in signals]
    metric_rows = []
    if signal_ids:
        placeholders = ",".join("?" for _ in signal_ids)
        metric_rows = conn.execute(
            f"""SELECT signal_id,snapshot_at,views FROM metric_snapshots
                WHERE signal_id IN ({placeholders}) AND date(snapshot_at)<=date(?)
                ORDER BY signal_id,snapshot_at""",
            (*signal_ids, score_date.isoformat()),
        ).fetchall()
    grouped_metrics: dict[str, list[sqlite3.Row]] = {}
    for row in metric_rows:
        grouped_metrics.setdefault(row["signal_id"], []).append(row)
    growth_scores: list[float] = []
    acceleration_scores: list[float] = []
    for rows in grouped_metrics.values():
        valid = [r for r in rows if r["views"] is not None]
        if len(valid) >= 2:
            growth_scores.append(_scaled_log_growth(float(valid[0]["views"]), float(valid[-1]["views"])))
        if len(valid) >= 3:
            first_growth = math.log1p(max(0, valid[-2]["views"])) - math.log1p(max(0, valid[-3]["views"]))
            latest_growth = math.log1p(max(0, valid[-1]["views"])) - math.log1p(max(0, valid[-2]["views"]))
            acceleration_scores.append(_clip(50 + (latest_growth - first_growth) * 60))
    trend_parts: list[tuple[float, float]] = []
    avg_growth = _mean_available(growth_scores)
    avg_acceleration = _mean_available(acceleration_scores)
    if avg_growth is not None:
        trend_parts.append((avg_growth, 0.40))
        reasons.append(f"有历史播放量快照的内容，其平均增长子分为 {avg_growth:.1f}/100。")
    if avg_acceleration is not None:
        trend_parts.append((avg_acceleration, 0.30))
    active_signal_count = len(signals)
    if active_signal_count:
        # This is a conservative lifecycle proxy, not a claim about total market volume.
        age_days = []
        for row in signals:
            try:
                published = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=UTC)
                age_days.append(max(0, (datetime.combine(score_date, datetime.min.time(), UTC) - published).days))
            except ValueError:
                continue
        if age_days:
            recent_share = sum(1 for age in age_days if age <= 14) / len(age_days)
            lifecycle_score = _clip(recent_share * 100)
            trend_parts.append((lifecycle_score, 0.30))
            reasons.append(f"现有样本中 {recent_share:.0%} 发布于最近 14 天；仅代表当前样本。")
    if trend_parts:
        weight_sum = sum(weight for _, weight in trend_parts)
        components["trend_momentum_lifecycle"] = sum(value * weight for value, weight in trend_parts) / weight_sum
    else:
        limitations.append("缺少足够的历史指标快照，无法可靠评估增长和生命周期。")

    # 3) Content potential: requires explicit editorial assessment.
    if assessment:
        vals = [assessment[field] for field in ("visual_potential", "angle_richness", "production_ease")]
        components["content_potential"] = _mean_available([float(v) if v is not None else None for v in vals])
        if components["content_potential"] is not None:
            reasons.append("内容潜力评分来自人工评估记录，不是模型自动推断。")
    else:
        limitations.append("缺少人工内容潜力评估（视觉吸引力、角度丰富度、制作难度）。")

    # 4) Cross-market spread. Presence is computed across independent market records.
    market_rows = conn.execute(
        """SELECT market_code FROM social_signals WHERE product_id=? AND date(published_at)<=date(?)
           UNION SELECT market_code FROM search_snapshots WHERE product_id=? AND date(period_end)<=date(?)""",
        (product_id, score_date.isoformat(), product_id, score_date.isoformat()),
    ).fetchall()
    observed_markets = {row["market_code"] for row in market_rows}
    if observed_markets:
        components["market_spread"] = len(observed_markets) / 3.0 * 100.0
        reasons.append(f"当前观察到 {len(observed_markets)}/3 个目标市场有数据。")
    else:
        limitations.append("缺少三个目标市场的独立记录。")

    # 5) Competition gap. Manual saturation is inverted; unknown is not treated as zero.
    if assessment and assessment["competition_saturation"] is not None:
        components["competition_gap"] = 100.0 - float(assessment["competition_saturation"])
        reasons.append("竞争空白分由人工竞争饱和度评估反向计算。")
    elif len(signals) >= 3 and creators:
        content_per_creator = len(signals) / max(len(creators), 1)
        components["competition_gap"] = _clip(100.0 - content_per_creator * 12.0)
        limitations.append("竞争空白使用样本内容密度代理值，不能替代全市场内容饱和度。")
    else:
        limitations.append("缺少竞争饱和度评估，竞争空白暂不评分。")

    # 6) Evidence quality: independent platforms, sample and collection provenance.
    platforms = {s["platform"] for s in signals}
    methods = {s["source_method"] for s in signals}
    non_synthetic_signal_count = sum(1 for s in signals if not s["is_synthetic"])
    source_score_parts: list[float] = []
    if signals or search or insights:
        source_score_parts.append(_clip(len(platforms) / 3 * 100))
        source_score_parts.append(_clip((len(signals) + len(search) + len(insights)) / 12 * 100))
        source_score_parts.append(_clip(len(methods) / 2 * 100) if methods else 0.0)
        source_score_parts.append(_clip(non_synthetic_signal_count / max(len(signals), 1) * 100))
        source_score_parts.append(
            mean(float(s["data_quality"]) for s in signals) * 100 if signals else 50.0
        )
        components["evidence_quality"] = mean(source_score_parts)
    else:
        limitations.append("没有可评估的原始证据记录。")

    available_weight = sum(WEIGHTS[k] for k, v in components.items() if v is not None)
    total_score = sum(WEIGHTS[k] * (v or 0.0) / 100.0 for k, v in components.items())
    data_coverage = available_weight
    evidence_count = len(signals) + len(search) + len(insights)
    if evidence_count >= 12 and len(platforms) >= 3 and len(observed_markets) >= 2 and not synthetic:
        grade = "A"
    elif evidence_count >= 5 and len(platforms) >= 2 and not synthetic:
        grade = "B"
    else:
        grade = "C"

    if data_coverage < 60 or grade == "C":
        recommendation = "证据不足：继续收集，不进入优先内容测试"
    elif total_score >= 80:
        recommendation = "优先进入内容测试"
    elif total_score >= 70:
        recommendation = "小规模内容测试"
    elif total_score >= 60:
        recommendation = "继续观察并补充证据"
    else:
        recommendation = "当前机会不足：暂不优先投入"

    if synthetic:
        recommendation = "模拟数据：仅用于验证程序，不代表真实市场结论"
        limitations.append("此记录包含合成演示数据，不可用于真实选品决策。")

    return ScoreResult(
        product_id=product_id,
        market_code=market,
        score_date=score_date,
        model_version=MODEL_VERSION,
        total_score=round(total_score, 2),
        data_coverage=round(data_coverage, 2),
        evidence_grade=grade,
        recommendation=recommendation,
        component_scores={k: round(v, 2) if v is not None else None for k, v in components.items()},
        reasons=reasons,
        limitations=limitations,
    )


def score_all(conn: sqlite3.Connection, score_date: date | None = None) -> list[ScoreResult]:
    scoring_date = score_date or date.today()
    pairs = conn.execute(
        """SELECT product_id,market_code FROM social_signals
           UNION SELECT product_id,market_code FROM search_snapshots
           UNION SELECT product_id,market_code FROM manual_assessments
           ORDER BY product_id,market_code"""
    ).fetchall()
    results = [_score_one(conn, row["product_id"], row["market_code"], scoring_date) for row in pairs]
    for result in results:
        synthetic = "模拟数据" in result.recommendation
        conn.execute(
            """INSERT INTO scores(product_id,market_code,score_date,model_version,total_score,data_coverage,
               evidence_grade,recommendation,component_scores_json,reasons_json,limitations_json,is_synthetic)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id,market_code,score_date,model_version) DO UPDATE SET
               total_score=excluded.total_score,data_coverage=excluded.data_coverage,evidence_grade=excluded.evidence_grade,
               recommendation=excluded.recommendation,component_scores_json=excluded.component_scores_json,
               reasons_json=excluded.reasons_json,limitations_json=excluded.limitations_json,is_synthetic=excluded.is_synthetic""",
            (result.product_id,result.market_code,result.score_date.isoformat(),result.model_version,
             result.total_score,result.data_coverage,result.evidence_grade,result.recommendation,
             json.dumps(result.component_scores, ensure_ascii=False),json.dumps(result.reasons, ensure_ascii=False),
             json.dumps(result.limitations, ensure_ascii=False),int(synthetic)),
        )
    return results
