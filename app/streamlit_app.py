from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Fashion Trend Radar", layout="wide")
st.title("Fashion Trend Radar")
st.caption("潮鞋与包袋社媒选品研究 · 规则评分不是销量或爆款预测")

DB_PATH = st.sidebar.text_input("SQLite 数据库", "data/registry.sqlite")
if not Path(DB_PATH).exists():
    st.info("数据库尚未创建。先运行：python -m fashion_radar.cli --db data/registry.sqlite init")
    st.stop()

with sqlite3.connect(DB_PATH) as conn:
    scores = pd.read_sql_query(
        """SELECT s.*,p.category,p.brand,p.canonical_name,p.subcategory,p.colorway
           FROM scores s JOIN products p USING(product_id)
           ORDER BY s.total_score DESC""", conn
    )

if scores.empty:
    st.info("还没有评分结果。先导入数据，再运行 score 命令。")
    st.stop()

markets = ["全部", "US", "CA", "GB"]
market = st.sidebar.selectbox("市场", markets)
category = st.sidebar.selectbox("品类", ["全部", "sneaker", "bag"])
view = scores.copy()
if market != "全部":
    view = view[view.market_code == market]
if category != "全部":
    view = view[view.category == category]

c1, c2, c3 = st.columns(3)
c1.metric("候选评分记录", len(view))
c2.metric("平均评分", f"{view.total_score.mean():.1f}" if len(view) else "—")
c3.metric("证据 A/B 数", int(view.evidence_grade.isin(["A", "B"]).sum()))

st.subheader("商品机会排名")
show_cols = ["canonical_name", "category", "market_code", "total_score", "data_coverage", "evidence_grade", "recommendation"]
st.dataframe(view[show_cols], use_container_width=True, hide_index=True)

labels = {
    f"{row.canonical_name} · {row.market_code} · {row.product_id}": (row.product_id, row.market_code)
    for row in view.itertuples()
}
if labels:
    selected = st.selectbox("查看评分证据", list(labels))
    product_id, selected_market = labels[selected]
    row = view[(view.product_id == product_id) & (view.market_code == selected_market)].iloc[0]
    left, right = st.columns(2)
    with left:
        st.write("**支持理由**")
        for reason in json.loads(row.reasons_json):
            st.write(f"- {reason}")
    with right:
        st.write("**数据限制**")
        for limitation in json.loads(row.limitations_json):
            st.write(f"- {limitation}")
    with st.expander("评分分项"):
        st.json(json.loads(row.component_scores_json))

st.divider()
st.subheader("内容测试表现")
with sqlite3.connect(DB_PATH) as conn:
    experiments = pd.read_sql_query(
        """SELECT e.experiment_id,p.canonical_name,e.platform,e.market_code,e.content_angle,
                  e.published_at,m.snapshot_at,m.views,m.likes,m.comments,m.shares,m.saves,
                  m.profile_visits,m.follows,e.is_synthetic,m.is_synthetic AS snapshot_is_synthetic
           FROM content_experiments e JOIN products p USING(product_id)
           LEFT JOIN experiment_snapshots m ON m.experiment_id=e.experiment_id
             AND m.snapshot_at=(SELECT MAX(m2.snapshot_at) FROM experiment_snapshots m2
                                WHERE m2.experiment_id=e.experiment_id)
           ORDER BY e.published_at DESC""", conn
    )
if experiments.empty:
    st.caption("尚无内容测试记录。导入 experiments.csv 和 experiment_snapshots.csv 后会显示在这里。")
else:
    experiments["engagement_count_partial"] = experiments[["likes", "comments", "shares", "saves"]].fillna(0).sum(axis=1)
    experiments["engagement_rate_partial_pct"] = experiments.apply(
        lambda r: round(r.engagement_count_partial / r.views * 100, 3)
        if pd.notna(r.views) and r.views > 0 else None, axis=1
    )
    experiments["synthetic"] = experiments.apply(
        lambda r: bool(r.is_synthetic or (pd.notna(r.snapshot_is_synthetic) and r.snapshot_is_synthetic)), axis=1
    )
    st.dataframe(
        experiments[["canonical_name", "platform", "market_code", "content_angle", "views",
                     "engagement_rate_partial_pct", "profile_visits", "follows", "synthetic"]],
        use_container_width=True, hide_index=True
    )
