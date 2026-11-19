# Official API automation capabilities and access roadmap

Updated: 2026-11-19

## Goal and boundaries

Fashion Trend Radar studies content opportunities for sneakers and bags in US, CA and GB. Prefer official APIs and official UI exports. Do not treat undocumented web endpoints, session-cookie scraping, or browser automation that bypasses access controls as production data sources.

Current confirmed user state:
- TikTok: ordinary Personal / Creator account; Advanced Access has not been confirmed.
- YouTube: no YouTube Data API key configured.
- Google Trends API Alpha: not approved / not yet available to this project.

Keep these data purposes separate:
- `market_discovery`: market-level signals used in product research.
- `owned_account_performance`: performance of the user's own posts.
- `ads_keyword_forecast`: advertising-tool forecasts, not observed organic search volume.
- `official_ui_export`: data exported from an official UI and then parsed automatically.

Do not mix them in a single metric or imply they have the same population.

## TikTok: access available to an ordinary Creator account

### 1. Display API — the first feasible TikTok integration

The Display API is designed to display an authorized creator's own profile and videos. It is not a public keyword-search API.

Prerequisites:
1. Register a TikTok developer app.
2. Obtain approval for both Login Kit and the TikTok API product.
3. Obtain the `user.info.basic` and `video.list` scopes.
4. Have the Creator account owner complete OAuth consent for that app.
5. Call `POST /v2/video/list/` and paginate the user's public videos.

The video object can include `view_count`, `like_count`, `comment_count`, and `share_count`, when requested and permitted. Capture these counters at repeated observation times to calculate changes; one snapshot alone is not a growth rate.

Store these records as the user's own content experiments, using `content_experiments` and `experiment_snapshots` (or an owned-content table). Do not treat them as third-party market samples or product demand evidence.

**What it cannot do:** the Display API cannot search arbitrary third-party TikTok posts for terms such as “Kobe 9”. A normal Creator account plus Display API approval does not grant global video discovery.

### 2. Discovery API — market trend discovery requires an account entitlement

TikTok's Discovery endpoint for trending search keywords documents these prerequisites:
- a TikTok account access token with the `discovery.search.words` scope;
- the TikTok account must have **Advanced Access** or be a **Verified Business Account**;
- the owner must authorize the requested permission.

The endpoint is `/open_api/v1.3/discovery/trending/search/`. It returns up to 20 trending search keywords and supports an `is_personalized` switch.

A standard General Personal / Creator account is not, by itself, proof of Advanced Access. TikTok's Accounts API overview says Advanced Access can be granted when an account is linked to a TikTok for Business account or an unverified Business Center. A Verified Business Account is a separate, higher entitlement requiring business verification.

Recommended path:
1. Consider linking the existing Creator account to a TikTok for Business account or an unverified Business Center, rather than assuming the content account must be converted to a Business Account.
2. After linking, check the documented `/business/get/` result and confirm `has_advanced_access=true`.
3. Obtain the Discovery API scope through the developer app / authorization flow.
4. Inspect the issued token and confirm `discovery.search.words` is present before making requests.

Important geographic caveat: the documented trending-search request lists `business_id` and `is_personalized`, but no explicit country parameter. Do not label the results US, CA or GB unless the API response or a verified documented account-region rule establishes that geography. Initially store these as account/global-scope keyword discoveries, not US product demand.

### 3. Search Ads recommended keywords — a different endpoint

Do not confuse Discovery trending search with the Search Ads keyword recommendation tool.

The Search Ads endpoint is `/open_api/v1.3/tool/search_keyword/recommend/` and requires an `advertiser_id`. For `search_queries`, it accepts up to five English queries (maximum 80 characters each); the current docs list only US as a supported region. Its `monthly_searches` field is a **predicted monthly-search estimate**, not a direct count of observed organic TikTok searches.

This can become a separate US-only `ads_keyword_forecast` source after advertiser authorization is actually available. It is not currently granted merely because the user has a Creator account.

### 4. Research API is not an option for this commercial project

TikTok's Research Tools FAQ explicitly says creators, advertisers and commercial users are not eligible for Research API access. Do not design this product around Research API approval.

## Google Trends: official API and practical fallback

### Trends API Alpha

Google is accepting applications for a limited Alpha program. The published API capabilities include:
- a rolling five-year window;
- daily, weekly, monthly and yearly aggregation;
- countries and some sub-regions;
- consistently scaled data, including the ability to merge/compare requests.

This is not a generally available public API. Apply for Alpha access; do not claim the project can call it until approval and credentials are obtained.

### Official UI CSV: automate parsing, not daily copy/paste

Until Alpha access exists, the feasible fallback is to export CSV from the official Google Trends UI and have the project parse the complete file automatically. The user should not have to paste each daily row into a custom template.

Parser requirements:
- accept the original Google Trends CSV, including leading metadata and the real column header (for example, `Day,<query>: (United States)`);
- preserve the original file and record a SHA-256, filename, import time, source URL, geography, category, search type, query and date window;
- turn each time point into a daily record, with `period_start=period_end=date` and one stable `comparison_group` for the export;
- preserve a displayed 0 as 0 and add a flag such as `possible_below_reporting_threshold=true`; Google documents that low-volume searches can display as 0;
- handle BOM, metadata lines, blank cells, repeated export headers, `<1` values and weekly/monthly exports explicitly; never turn an unknown value into 0;
- make `comparison_group` identify the country, query set, search type and time window; do not join index values from independent groups into one growth series;
- if product_id is supplied, write both `source_observations` and `search_snapshots`, preserving the comparison group and original-file provenance.

If the CSV itself does not preserve the full source URL or query configuration, a sidecar manifest must record those facts. Do not fabricate missing provenance.

### BigQuery public Trends dataset — supplemental discovery source

Google also documents a public BigQuery Trends dataset with US Top 25 overall / Top 25 Rising queries by DMA and international country/sub-region data. It can provide automated discovery of broad terms already on those lists, but it is not a substitute for querying an arbitrary product phrase such as “Kobe 9 EM Low Protro Philippines” or for the custom Explore time series.

## YouTube Data API

The repository already has an official YouTube Data API collector using `search.list` and `videos.list`, request logging and quota protection. Do not rewrite it.

- Without a key, report `blocked_missing_credential` and skip probe/collection.
- Create a Google Cloud project, enable YouTube Data API v3, create and appropriately restrict an API key, then store it locally via the existing hidden-input script in the gitignored `.env`.
- First probe one US query, then collect the single Kobe 9 pilot.
- Existing project defaults use a lower daily search budget of 80 calls. Google's documentation lists a default 100 calls/day quota bucket for `search.list`; always check the actual Cloud Console quota.
- `regionCode=US` is the search request's regional context. It does not prove that creators or viewers are US-based.
- Follow the existing 30-day API data retention policy.

## Unified collector architecture

### Adapters and storage

| Adapter | Purpose | Prerequisite | Target storage |
|---|---|---|---|
| `youtube_data_api` | Third-party public-video discovery | API key | `youtube_video_references` + run/quota logs |
| `tiktok_display_api` | User's own Creator video performance | App review + OAuth + scopes | `content_experiments` / `experiment_snapshots` |
| `tiktok_discovery_api` | Trending keyword discovery | Advanced Access or Verified Business + Discovery scope | New keyword-discovery table with scope metadata |
| `tiktok_search_ads_keyword_tool` | Ad keyword forecast | `advertiser_id` + advertiser authorization; US | Separate forecast table |
| `google_trends_api_alpha` | Custom search-interest time series | Alpha approval | `source_observations` + `search_snapshots` |
| `google_trends_ui_csv` | Official UI CSV parser | Original CSV + sidecar manifest | `source_observations` + `search_snapshots` |
| `google_trends_bigquery` | Broad top/rising query discovery | Public dataset availability | Separate ranking snapshots |

### Authorization/readiness states

Expose explicit readiness states:
- `available`
- `blocked_missing_credential`
- `needs_app_review`
- `needs_user_oauth`
- `blocked_account_entitlement`
- `needs_scope_consent`
- `needs_advertiser_authorization`
- `alpha_application_pending`
- `manual_export_fallback`
- `unsupported_for_current_account`

When a credential, scope or account entitlement is missing, the collector should report a clear blocked state and next step. Do not report it as a network failure and do not create empty observations.

### Geography and metric semantics

- Set `market_code` to US/CA/GB only when the source explicitly identifies that country/region and its meaning matches the project's market definition.
- If a TikTok Discovery response has no explicit geography, store it as global/account scope. The current `source_observations.market_code` is NOT NULL, so add an appropriately scoped keyword-discovery table instead of inserting a fabricated US value.
- Keep own-account videos, third-party market videos, keyword ranks and ad forecasts in distinct datasets.
- Forecast fields and units must explicitly say predicted/forecast.
- Keep source URL, API request_id, capture time, query, period, authorization source and retention policy auditable.

## Phased implementation

### Phase 0 — record permissions, do not call blocked endpoints
1. Record current status: ordinary Personal / Creator; Advanced Access not confirmed; no YouTube API key; Trends Alpha not approved.
2. Add source readiness diagnostics and prevent collectors from running when a required permission is blocked.
3. Confirm whether the owner is willing to link the existing TikTok account to TikTok for Business or an unverified Business Center to seek Advanced Access. Do not assume account-type conversion.

### Phase 1 — automation possible without market Discovery entitlement
1. Apply for TikTok Display API / Login Kit and the required scopes; after approval, collect the user's own public videos and repeated counters.
2. Build a Google Trends original-CSV parser and sidecar manifest. Test it on one complete, unmodified export before importing the Kobe 9 series.
3. Apply for Google Trends API Alpha; keep the official UI export parser as fallback while approval is pending.
4. Create a YouTube API key and test the existing collector with one Kobe 9 / US query.

### Phase 2 — TikTok market-discovery access
1. If approved by the account owner, link the existing TikTok account to TikTok for Business / an unverified Business Center.
2. Verify `has_advanced_access=true`.
3. Configure a Discovery API app and obtain `discovery.search.words` through owner consent.
4. Confirm the token scope, then request the non-personalized top 20 trending-search terms first.
5. Do not attribute results to US unless the source's geographic semantics have been verified.
6. Evaluate the Search Ads recommendation endpoint separately and only after an advertiser ID is available.

### Phase 3 — single-product acceptance, then 297 tasks

Use Kobe 9 EM Low Protro Philippines / US as the pilot. For each source, validate authorization, real API response or original export, correct field semantics, real geography, de-duplication and persistence. Expand to the 297 tasks only after Phase 1/2 acceptance gates pass.

## Official references

- TikTok Accounts API overview and account entitlements: https://business-api.tiktok.com/portal/docs/accounts-api-overview/v1.3
- TikTok Discovery — trending search keywords: https://business-api.tiktok.com/portal/docs/get-trending-search-keywords/v1.3
- TikTok Search Ads — recommended search keywords: https://business-api.tiktok.com/portal/docs/get-recommended-search-keywords-reference/v1.3
- TikTok Display API overview: https://developers.tiktok.com/docs/en/display-api-overview
- TikTok Display API get started: https://developers.tiktok.com/docs/en/display-api-get-started
- TikTok Display API list videos: https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list
- TikTok Research API FAQ: https://developers.tiktok.com/docs/en/research-api-faq/
- Google Trends API Alpha: https://developers.google.com/search/apis/trends
- Google Trends FAQ on normalization and low-volume zero values: https://support.google.com/trends/answer/4365533
- Google Trends BigQuery dataset: https://support.google.com/trends/answer/12764470
- YouTube Data API getting started and quota: https://developers.google.com/youtube/data/getting-started
