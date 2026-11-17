# Fashion Trend Radar

面向美国（US）、加拿大（CA）和英国（GB）的潮鞋、包袋与手袋社媒选品研究工具。第一版定位为**内容机会研究系统**，不做自动采购、利润预测或爆款保证。

> **重要：** `examples/` 中所有内容、指标和评论均为合成演示数据，URL 使用 `example.invalid`。它们只用于验证程序流程，不能作为真实市场证据或选品结论。

## 第一版能力

- 商品主数据：潮鞋/包袋分类、型号、款式、别名、配色、材质和身份匹配置信度。
- 三个市场分开存储：`US`、`CA`、`GB`。
- 社媒内容信号与历史指标快照：播放、点赞、评论、分享、收藏等；缺失值保存为 `NULL`，不当作 0。
- 搜索兴趣快照、评论主题/购买意图汇总、人工内容潜力和竞争评估。
- 透明规则评分 `rules-v1.0`：需求证据、趋势动能/生命周期、内容潜力、市场扩散、竞争空白、证据质量。
- 证据等级与数据覆盖率：数据不足时明确提示，不把低覆盖的高分当作优先推荐。
- 报告导出：JSON、CSV、Markdown。
- 内容实验记录与表现汇总：按每个视频的多次快照计算部分可见互动率、收藏率、分享率、主页访问率和关注/主页访问率。
- 分析仓库导出：Parquet + DuckDB。
- Streamlit 只读榜单页面（可选安装）。

## Windows 安装

在项目根目录执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,app,warehouse]"
```

## 运行合成演示

```powershell
.\.venv\Scripts\python.exe scripts\run_demo.py
```

演示会生成：

```text
data/demo_registry.sqlite
reports/demo/selection_report.json
reports/demo/selection_report.csv
reports/demo/selection_report.md
reports/demo/warehouse/fashion_radar.duckdb
reports/demo/warehouse/*.parquet
```

演示数据全部标记为 synthetic，报告会明确显示“模拟数据”，证据等级不会升为 A/B。

## 导入真实研究数据

先初始化独立数据库：

```powershell
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite init
```

然后依次导入 CSV。文件字段定义见 `docs/CSV_SCHEMA.md`：

```powershell
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-products --file path\products.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-signals --file path\signals.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-metrics --file path\metrics.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-search --file path\search.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-insights --file path\insights.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-assessments --file path\assessments.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-experiments --file path\experiments.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite import-experiment-snapshots --file path\experiment_snapshots.csv
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite score --date 2026-11-16
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite report --date 2026-11-16 --out reports/latest
.\.venv\Scripts\python.exe -m fashion_radar.cli --db data/registry.sqlite warehouse-refresh --out data/warehouse
```

> 上面命令中的 Python 路径以 Windows PowerShell 为例。实际日期请填写本轮数据对应的评分日期；不要把示例日期当成当前日期。`warehouse-refresh` 需要安装 `warehouse` extra；Streamlit 页面需要 `app` extra。

## 启动只读页面

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```

## CLI 命令

```text
fashion-radar --db PATH init
fashion-radar --db PATH status
fashion-radar --db PATH import-products --file FILE
fashion-radar --db PATH import-signals --file FILE
fashion-radar --db PATH import-metrics --file FILE
fashion-radar --db PATH import-search --file FILE
fashion-radar --db PATH import-insights --file FILE
fashion-radar --db PATH import-assessments --file FILE
fashion-radar --db PATH import-experiments --file FILE
fashion-radar --db PATH import-experiment-snapshots --file FILE
fashion-radar --db PATH score --date YYYY-MM-DD
fashion-radar --db PATH report --date YYYY-MM-DD --out DIR
fashion-radar --db PATH experiment-report --out DIR
fashion-radar --db PATH warehouse-refresh --out DIR
```

## 评分原则

权重定义在 `configs/scoring_v1.yaml`：

| 维度 | 权重 |
|---|---:|
| 需求证据 | 25 |
| 趋势动能与生命周期 | 20 |
| 内容潜力 | 20 |
| 三市场扩散 | 15 |
| 竞争空白 | 10 |
| 证据质量 | 10 |

这是**规则启发式模型**，不是训练出来的预测模型。当前实现有意将缺失评分保留为 `null`，总分采用未补足的权重贡献，同时显示 `data_coverage`。覆盖不足 60% 或证据等级 C 时，不会建议直接优先测试。

当前指标限制：

- 平台指标不能跨平台直接相加；当前增长子分只在同一内容的历史快照内计算。
- 搜索兴趣指数是相对指标，不代表绝对搜索量。
- 创作者数来自导入记录中的匿名化 `creator_key`，并非平台总创作者数。
- 竞争空白优先使用人工评估；样本内容密度仅为低可信代理指标。
- 跨市场得分表示当前已观察到多少目标市场，不代表该商品在未采集市场不受欢迎。
- 合成演示数据不会成为真实市场结论。

## 数据源政策

第一版只接受官方 API、授权数据服务、公开页面人工研究记录和用户导入的数据。不会内置绕过登录、访问控制或平台限制的非官方爬虫。各数据源的状态、访问方法和使用注意事项登记在 SQLite `sources` 表中。

推荐先接入：

1. YouTube Data API：官方 API 采集公开视频与指标快照，遵守配额。
2. TikTok Creative Center：研究员按 US/CA/GB 分别记录趋势、标签和案例视频。
3. Google Trends：导入按同一查询设置和同一比较组导出的兴趣指数。
4. Reddit：使用 OAuth API 和合规数据范围；处理删除内容和保留限制。
5. Instagram/Pinterest：先人工观察或使用获准的数据服务；暂不做非官方全站抓取。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests app scripts
```

## 项目状态

`v0.1.0` 是可运行的基础研究流水线：数据库、CSV 导入、规则评分、报告导出、Parquet/DuckDB 分析仓库和只读 Streamlit 页面。它还不是完整的多平台自动采集系统。下一阶段应先验证真实数据导入与商品身份匹配，再增加有明确授权的数据源连接器。


## Data source collection and validation

Configure a local YouTube API key securely with:

    python scripts/configure_youtube_api_key.py

The hidden-input prompt writes to the gitignored .env file; never commit credentials.

YouTube public video references are collected only through the official YouTube Data API. The collector stores individual references separately from the product-scoring tables; it does not aggregate YouTube API metrics into opportunity scores. API-derived video metadata and non-authorized statistics expire after 30 days.

Start with the data-source validation and collection guide in docs/DATA_SOURCE_COLLECTION.md.\nAudit real US/CA/GB trend-source coverage with `python scripts/audit_source_coverage.py --db data/registry.sqlite`; see docs/SOURCE_COVERAGE_AUDIT.md for acceptance rules.

Typical sequence after importing the product catalogue:

    python scripts/validate_sources.py --db data/registry.sqlite --queries configs/youtube_queries.csv
    python scripts/collect_youtube.py --db data/registry.sqlite --queries configs/youtube_queries.csv --days-back 30
    python scripts/purge_expired_youtube_data.py --db data/registry.sqlite

The purge command is dry-run by default. Add --apply only after reviewing the reported count. Keep YOUTUBE_API_KEY in an environment variable; never place credentials in the repository.
