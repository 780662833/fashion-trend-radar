# Data source collection and validation

Updated: 2026-11-16

This document distinguishes working collection paths from manual research and unavailable/unapproved integrations. Do not describe a source as automated until the connector has been implemented and run against an authorized credential.

## Source status

| Source | First-release method | Automated status | Validation rule |
|---|---|---|---|
| YouTube | YouTube Data API v3 search.list + videos.list | Implemented; requires user API key | Query region, request time, source URL and metric snapshot are recorded |
| TikTok Creative Center | Human capture by country and time window | Manual capture supported | Save market, ranking period, visible metric, page URL and at least one supporting video URL |
| Google Trends | Trends UI CSV/manual capture | Manual capture supported | Same query group, country, date window and search type must be preserved |
| Reddit | No connector in this phase | Not implemented | OAuth and written use-case review required before enabling |
| Instagram | Human observation or licensed provider import | Not automated | Do not use unofficial scraping |
| Pinterest Trends | Human observation or approved API access | Not automated | API access and historical limitations must be checked first |

## YouTube collector

The collector uses:
- search.list for video discovery with type=video, regionCode, relevanceLanguage, order=date and publishedAfter.
- videos.list for public snippet and statistics data in batches of up to 50 IDs.
- The returned public statistics are attached only to the individual video reference; this project does not calculate product-level or cross-channel scores from YouTube API data.

regionCode describes the search request's regional context. It does **not** prove the audience, creator residence, or viewer location. The market_code stored on a signal must therefore be interpreted as the region used for the search.

### Quota controls

Google's current documentation describes a separate search.list quota bucket (100 calls/day by default) and 10,000 units/day for other endpoints; quotas reset at midnight Pacific Time. Each search.list request costs one unit, and additional pages incur additional cost. videos.list costs one unit per request.

This project deliberately applies lower safety budgets by default:
- YOUTUBE_SEARCH_DAILY_BUDGET=80
- YOUTUBE_OTHER_DAILY_BUDGET=9000

Failed attempts are logged too. The collector fetches one search page per query and does not silently paginate. Change budgets only after reviewing actual quota allocation in Google Cloud Console.

### Configure and run (Windows PowerShell)

1. Create a Google Cloud project, enable YouTube Data API v3, and create an API key. Restrict the key to the YouTube Data API and suitable application restrictions.
2. Keep the key out of Git. Set it in the current PowerShell session:

   $env:YOUTUBE_API_KEY = "YOUR_API_KEY"

3. Create configs/youtube_queries.csv from configs/youtube_queries_template.csv. Example rows:

   product_id,market_code,query,language

   YOUR-SHOE-ID,US,Air Jordan 4 sneaker outfit,en

   YOUR-SHOE-ID,CA,Air Jordan 4 sneaker outfit,en

   YOUR-BAG-ID,GB,womens shoulder bag styling,en

4. Ensure the product IDs exist in the local SQLite registry.
5. Validate the plan, without using quota:

   python scripts/validate_sources.py --db data/registry.sqlite --queries configs/youtube_queries.csv

6. Run collection:

   python scripts/collect_youtube.py --db data/registry.sqlite --queries configs/youtube_queries.csv --days-back 30 --max-results 25

The collector writes individual rows to youtube_video_references plus source_runs and api_request_log. It does not insert YouTube API results into social_signals or metric_snapshots, so those records cannot enter product scoring. Channel identifiers are not stored. Missing public statistics remain NULL; shares and saves are not inferred. Each reference expires 30 days after collection and must be purged or refreshed before then.

To explicitly test the API credential with one read-only API request, add --probe-youtube to validate_sources.py. This consumes one search.list quota call and is logged.


## YouTube API data retention

YouTube API Data is treated separately from first-party/manual observations:
- Individual video references only; no product-level aggregation or cross-platform scoring from YouTube API Data.
- No audiovisual content is downloaded or cached.
- Public video metadata and non-authorized statistics are set to expire 30 days after collection.
- Run the scheduled/manual purge script regularly: python scripts/purge_expired_youtube_data.py --db data/registry.sqlite --apply
- The default purge mode is dry-run. Review the number of rows before applying.
- Additional derived metrics and extended statistical retention require an explicitly accepted YouTube policy amendment / approved analytics use case. This project does not assume that approval.

The YouTube developer policy says non-authorized API Data may only be temporarily stored for up to 30 calendar days, and it restricts aggregation of API Data across unrelated channels. See the official policy links below before changing this design.

## TikTok Creative Center capture procedure

For each product or search term:
1. Open the official Creative Center trend page.
2. Select the market explicitly: United States, Canada, or United Kingdom.
3. Select one fixed time range (7, 30, or 90 days) and category where appropriate.
4. Record the exact keyword/hashtag, metric label and displayed value. Do not mix posts, views, rank and growth percentage in one metric.
5. Save the source page URL, capture timestamp and at least one representative public video URL when available.
6. Import the completed template:

   python scripts/import_source_observations.py --db data/registry.sqlite --file configs/manual_research/tiktok_trends.csv

A trend-page aggregate is stored as a source_observation, not fabricated into a per-video social_signal.

## Google Trends capture procedure

For each market:
1. Use the same date range and search type for all terms in a comparison.
2. Keep the comparison group stable and name it, e.g. US_2026Q4_sneaker_terms_web_90d.
3. Export the CSV from Trends and capture its source URL and timestamp.
4. Record the index as interest_index with unit index_0_100.
5. Do not compare raw 0-100 values across independent requests or comparison groups.

When product_id is supplied, a validated Google Trends row is also inserted into search_snapshots, retaining comparison_group.

Google announced its Trends API as an alpha program with application-based early access. Do not assume general API access is available.

## Run-level validation and data quality

Every collection run should be evaluated on:
- Authentication / API key configuration.
- Query plan shape and product ID references.
- Market code and time window validity.
- Number of search requests versus quota budget.
- API errors and partial detail-fetch failures.
- Items found, items stored, and duplicate IDs.
- Null metrics; null means unavailable, not zero.
- Original content URL and query provenance.
- Synthetic flag. Real data imports must be explicitly non-synthetic.

A successful HTTP/API response is not proof of trend representativeness. The system stores observed records and sample limitations; it does not claim to measure total platform activity.

## Official references

- YouTube search.list: https://developers.google.com/youtube/data/reference/rest/v3/search/list
- YouTube videos.list: https://developers.google.com/youtube/data/reference/rest/v3/videos/list
- YouTube quota calculator: https://developers.google.com/youtube/data/determine_quota_cost
- YouTube Developer Policies (data aggregation and storage): https://developers.google.com/youtube/terms/developer-policies
- YouTube additional derived metrics policy (approval required for the permitted analytics exceptions): https://developers.google.com/youtube/terms/derived-metrics-policy
- TikTok Creative Center trends: https://ads.tiktok.com/creative/creativeCenter/trends?region=US
- Google Trends API alpha: https://developers.google.com/search/apis/trends
- Reddit Data API policy: https://redditinc.com/policies/data-api-terms
- Reddit Data API wiki: https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
- Pinterest Trends API: https://developers.pinterest.com/docs/analytics-and-reports/trends/
