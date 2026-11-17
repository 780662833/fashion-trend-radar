# CSV 导入字段

所有 CSV 使用 UTF-8 或 UTF-8 BOM，第一行为英文列名。时间建议使用 ISO 8601，例如 `2026-11-16T18:30:00Z`。空值请留空，不要用 `0` 代替未知指标。

## products.csv

必需：`product_id,category,subcategory,brand,model_name,canonical_name`。

可选：`style_code,aliases,colorway,material,audience_tag,style_tags,release_date,identity_confidence`。

- `category`: `sneaker` 或 `bag`
- `aliases`: 用分号分隔，例如 `AJ4 Black Cat;Air Jordan 4 Black Cat`
- `audience_tag`: `men`、`women`、`unisex` 或 `unknown`
- `identity_confidence`: 0 到 1

## signals.csv

必需：`signal_id,product_id,platform,market_code,content_url,published_at`。

推荐：`observed_at,content_type,caption,hashtags,creator_key,source_method,data_quality,is_synthetic`。

`market_code` 只接受 `US`、`CA`、`GB`。`creator_key` 应使用稳定的匿名标识，不要导入不必要的个人资料。`is_synthetic` 为 `true/false`。

## metrics.csv

必需：`signal_id,snapshot_at`。其他字段：`views,likes,comments,shares,saves,creator_followers,metric_source,is_synthetic`。

相同内容可以有多个 `snapshot_at`。同一个 `signal_id`、同一个快照时间重复导入时会更新对应快照。

## search.csv

必需：`product_id,market_code,keyword,period_start,period_end,interest_index`。

其他字段：`source,captured_at,is_synthetic`。兴趣指数需保留来源平台的原始定义；不同查询组或不同归一化范围不可直接横向比较。

## insights.csv

必需：`product_id,market_code,topic,intent_type,mention_count,sample_size`。

`intent_type` 可选：`price_question`、`purchase_intent`、`comparison`、`objection`、`usage_feedback`、`other`。

其他字段：`source_url,confidence,observed_at,is_synthetic`。`mention_count` 不得大于 `sample_size`，汇总前应去除重复评论样本。

## assessments.csv

必需：`product_id,market_code`。

可选：`visual_potential,angle_richness,production_ease,competition_saturation,notes,assessed_at,is_synthetic`。四个分数均为 0–100；竞争饱和度越高，竞争空白分越低。人工评估应记录简短依据。

## experiments.csv

必需：`experiment_id,product_id,platform,market_code,content_angle`。

其他字段：`hook,post_url,published_at,video_length_seconds,format,notes,is_synthetic`。

## experiment_snapshots.csv

必需：`experiment_id,snapshot_at`。其他字段：`views,likes,comments,shares,saves,profile_visits,follows,metric_source,is_synthetic`。

同一内容测试可以按 24 小时、72 小时、7 天等节点导入多份快照；对比实验时应尽量采用相同统计窗口。


## Manual source observation import

TikTok Creative Center and Google Trends aggregate observations use the dedicated templates under configs/manual_research/ and are imported with scripts/import_source_observations.py. This importer stores the exact source URL, market, metric unit, capture time and period window.

For Google Trends, comparison_group is mandatory. It must identify the exact country, query comparison set, search type and time window. Values normalized in different comparison groups must never be compared as a growth series. When product_id is included, the importer also writes a search_snapshots row while preserving comparison_group.\n\n`product_id` is optional for aggregate observations and supported for TikTok, Google Trends, Pinterest, Reddit and Instagram rows. When supplied, it must match an existing product and is stored on `source_observations`, so the observation can be traced back to the product and market. Google Trends rows with a product_id additionally create/update `search_snapshots`. Leave product_id blank only when the observation is intentionally market/keyword-level rather than product-specific.
