from __future__ import annotations

import csv
import json
from pathlib import Path

from .db import connect


def export_report(db_path: str | Path, output_dir: str | Path, score_date: str | None = None) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    query = """SELECT s.*,p.category,p.brand,p.canonical_name,p.subcategory,p.colorway
               FROM scores s JOIN products p USING(product_id)"""
    params: tuple = ()
    if score_date:
        query += " WHERE s.score_date=?"
        params = (score_date,)
    query += " ORDER BY s.total_score DESC, s.data_coverage DESC, p.canonical_name"
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        products = [dict(row) for row in rows]

    json_path = out / "selection_report.json"
    csv_path = out / "selection_report.csv"
    md_path = out / "selection_report.md"
    json_path.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")

    if products:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=list(products[0].keys()))
            writer.writeheader()
            writer.writerows(products)
    else:
        csv_path.write_text("", encoding="utf-8")

    lines = [
        "# Fashion Trend Radar — 选品研究报告",
        "",
        "> 本报告只展示已进入评分表的记录。分数是透明规则模型的研究排序，不是销量、爆款或收入预测。",
        "",
        f"候选记录数：{len(products)}",
        "",
        "| 排名 | 商品 | 品类 | 市场 | 分数 | 数据覆盖 | 证据等级 | 建议 |",
        "|---:|---|---|---|---:|---:|:---:|---|",
    ]
    for i, row in enumerate(products, 1):
        name = f"{row['brand']} {row['canonical_name']}".replace("|", "/")
        lines.append(
            f"| {i} | {name} | {row['category']} | {row['market_code']} | "
            f"{row['total_score']:.1f} | {row['data_coverage']:.0f}% | {row['evidence_grade']} | {row['recommendation']} |"
        )
    lines += ["", "## 解释", ""]
    for row in products:
        lines.append(f"### {row['brand']} {row['canonical_name']} — {row['market_code']}")
        lines.append("")
        lines.append("评分分项：")
        components = json.loads(row["component_scores_json"])
        for key, value in components.items():
            lines.append(f"- `{key}`: {'缺失' if value is None else f'{value:.1f}/100'}")
        lines.append("")
        lines.append("支持理由：")
        reasons = json.loads(row["reasons_json"])
        lines.extend([f"- {reason}" for reason in reasons] or ["- 暂无足够支持理由。"])
        lines.append("")
        lines.append("数据限制：")
        limitations = json.loads(row["limitations_json"])
        lines.extend([f"- {limitation}" for limitation in limitations] or ["- 未记录其他限制。"])
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def export_experiment_report(db_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    """Export the latest observed outcome for each content experiment."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    query = """SELECT e.*,p.brand,p.canonical_name,p.category,
               m.snapshot_at,m.views,m.likes,m.comments,m.shares,m.saves,m.profile_visits,m.follows,
               m.metric_source AS snapshot_source,m.is_synthetic AS snapshot_is_synthetic
               FROM content_experiments e
               JOIN products p USING(product_id)
               LEFT JOIN experiment_snapshots m ON m.experiment_id=e.experiment_id
                 AND m.snapshot_at=(SELECT MAX(m2.snapshot_at) FROM experiment_snapshots m2
                                    WHERE m2.experiment_id=e.experiment_id)
               ORDER BY e.published_at DESC,e.experiment_id"""
    with connect(db_path) as conn:
        rows = [dict(row) for row in conn.execute(query).fetchall()]

    for row in rows:
        views = row.get("views")
        metrics = [row.get(k) for k in ("likes", "comments", "shares", "saves")]
        observed_metrics = [int(value) for value in metrics if value is not None]
        row["engagement_count_partial"] = sum(observed_metrics) if observed_metrics else None
        row["engagement_rate_partial"] = (
            round(sum(observed_metrics) / views * 100, 3)
            if views and observed_metrics else None
        )
        row["save_rate"] = round(row["saves"] / views * 100, 3) if views and row.get("saves") is not None else None
        row["share_rate"] = round(row["shares"] / views * 100, 3) if views and row.get("shares") is not None else None
        row["profile_visit_rate"] = (
            round(row["profile_visits"] / views * 100, 3)
            if views and row.get("profile_visits") is not None else None
        )
        row["follow_rate_from_profile_visits"] = (
            round(row["follows"] / row["profile_visits"] * 100, 3)
            if row.get("profile_visits") and row.get("follows") is not None else None
        )
        row["is_synthetic_result"] = bool(row.get("is_synthetic") or row.get("snapshot_is_synthetic"))

    json_path = out / "content_experiment_report.json"
    csv_path = out / "content_experiment_report.csv"
    md_path = out / "content_experiment_report.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    lines = [
        "# 内容实验表现报告",
        "",
        "> 指标只用于同账号、相近内容制作标准和相同观察窗口的比较。缺失指标不补零。",
        "",
        f"实验数：{len(rows)}",
        "",
        "| 商品 | 市场 | 平台 | 内容角度 | 最新播放 | 互动率（部分可见指标） | 收藏率 | 主页访问率 | 关注/主页访问 | 合成数据 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['brand']} {row['canonical_name']} | {row['market_code']} | {row['platform']} | "
            f"{row['content_angle']} | {row.get('views') if row.get('views') is not None else '缺失'} | "
            f"{row.get('engagement_rate_partial') if row.get('engagement_rate_partial') is not None else '缺失'}% | "
            f"{row.get('save_rate') if row.get('save_rate') is not None else '缺失'}% | "
            f"{row.get('profile_visit_rate') if row.get('profile_visit_rate') is not None else '缺失'}% | "
            f"{row.get('follow_rate_from_profile_visits') if row.get('follow_rate_from_profile_visits') is not None else '缺失'}% | "
            f"{'是' if row['is_synthetic_result'] else '否'} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}
