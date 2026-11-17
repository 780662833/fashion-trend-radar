# Scoring Model v1.0

## Intent

`rules-v1.0` ranks content opportunities for a small social account. It does not forecast sales, revenue, virality, or platform ranking. It is a transparent baseline to be evaluated against a manual-selection control group.

## Weights

| Dimension | Weight |
|---|---:|
| Demand evidence | 25 |
| Trend momentum and lifecycle | 20 |
| Content potential | 20 |
| Three-market spread | 15 |
| Competition gap | 10 |
| Evidence quality | 10 |

## Feature definitions

- Search trend: compare the first and last observed Google Trends-style index for the **same keyword and same market**. Different keyword series are not stitched together.
- Creator diversity: distinct anonymized creator keys in that product/market sample.
- Intent signal: aggregate `price_question`, `purchase_intent`, and `comparison` mentions divided by sampled audience items. Remove duplicate comments before aggregation.
- Content momentum: within each content item, compare the first and latest available views snapshots; do not compare raw view totals across creators/platforms.
- Acceleration: compare the latest snapshot interval with the prior interval when at least three snapshots exist.
- Lifecycle proxy: share of the observed posts published within the previous 14 days. It describes the collected sample, not all market activity.
- Content potential: mean of manual `visual_potential`, `angle_richness`, and `production_ease` assessments.
- Market spread: share of the three target markets (US/CA/GB) that have at least one observed signal or search record before the scoring date.
- Competition gap: 100 minus manually assessed `competition_saturation`; if unavailable, a low-confidence proxy based on observed content per creator may be shown.
- Evidence quality: platform diversity, evidence count, collection-method diversity, and proportion of non-synthetic social records.

## Missing-data policy

A missing dimension remains `null`. It is not silently filled with zero or with a model estimate. The total score is the weighted contribution of available dimensions; `data_coverage` is the sum of weights for dimensions with enough observations. A score below 60% coverage or evidence grade C cannot trigger a priority recommendation.

## Evidence grades

- A: at least 12 evidence rows, 3 platforms, 2 markets, and no synthetic evidence in the score inputs.
- B: at least 5 evidence rows, 2 platforms, and no synthetic evidence in the score inputs.
- C: all other cases.

These thresholds are conservative initial rules, not statistical confidence intervals. A high numeric score with grade C is still insufficient evidence.

## Thresholds

- 80–100: prioritize a content test, only if coverage >=60% and evidence A/B.
- 70–79.99: small content test, only if coverage >=60% and evidence A/B.
- 60–69.99: continue observation.
- below 60: do not prioritize.

## Calibration plan

After at least four weeks of comparable posts, compare system-selected products against independently selected manual controls. Compare 7-day saves/share rates, purchase-intent comments, profile visits and follows. Do not optimize on raw views alone. Revisions to weights must increment `model_version` and preserve prior scores for audit.
