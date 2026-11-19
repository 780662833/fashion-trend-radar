from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


BLOCKED_SOCIAL_DOMAINS = {
    "tiktok.com", "instagram.com", "facebook.com", "youtube.com",
}


def load_seeds(path: str | Path) -> list[dict[str, str]]:
    seed_path = Path(path)
    if not seed_path.is_file():
        raise FileNotFoundError(f"Seed CSV not found: {seed_path.resolve()}")
    with seed_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"source_name", "source_url", "allowed_domain"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Seed CSV missing columns: {', '.join(sorted(missing))}")
        rows = []
        for line, raw in enumerate(reader, start=2):
            row = {key: (value or "").strip() for key, value in raw.items()}
            if not row.get("source_url"):
                continue
            parsed = urlparse(row["source_url"])
            domain = row.get("allowed_domain", "").lower().strip().lstrip(".")
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not host or not domain:
                raise ValueError(f"row {line}: source_url must be HTTPS and allowed_domain is required")
            if host != domain and not host.endswith("." + domain):
                raise ValueError(
                    f"row {line}: source host {host!r} is outside allowlisted domain {domain!r}"
                )
            if any(
                domain == blocked or domain.endswith("." + blocked)
                for blocked in BLOCKED_SOCIAL_DOMAINS
            ):
                raise ValueError(
                    f"row {line}: social-platform crawling is not supported; use authorized APIs"
                )
            market = row.get("market_code", "").upper()
            if market and market not in {"US", "CA", "GB"}:
                raise ValueError(f"row {line}: market_code must be US, CA, GB or blank")
            row["market_code"] = market
            rows.append(row)
    if not rows:
        raise ValueError("Seed CSV contains no public page URLs to fetch")
    return rows


def run_crawl(seeds: list[dict[str, str]], output_path: str | Path) -> None:
    try:
        import scrapy
        from scrapy.crawler import CrawlerProcess
    except ImportError as exc:
        raise RuntimeError(
            'Scrapy is not installed. Run: pip install -e ".[research-crawl]"'
        ) from exc

    domains = sorted({row["allowed_domain"].lower().lstrip(".") for row in seeds})
    captured_at = datetime.now(timezone.utc).isoformat()

    class PublicSeedSpider(scrapy.Spider):
        name = "fashion_radar_public_seed_pages"
        allowed_domains = domains

        def start_requests(self):
            for row in seeds:
                yield scrapy.Request(
                    row["source_url"],
                    callback=self.parse_seed,
                    errback=self.capture_failure,
                    cb_kwargs={"seed": row},
                    meta={"handle_httpstatus_all": True},
                    dont_filter=False,
                )

        def parse_seed(self, response, seed):
            title = response.css("title::text").get(default="").strip()
            description = (
                response.css('meta[name="description"]::attr(content)').get()
                or response.css('meta[property="og:description"]::attr(content)').get()
                or ""
            ).strip()
            published = (
                response.css('meta[property="article:published_time"]::attr(content)').get()
                or response.css('meta[name="date"]::attr(content)').get()
                or response.css('time[datetime]::attr(datetime)').get()
                or ""
            ).strip()
            canonical = response.css('link[rel="canonical"]::attr(href)').get() or ""
            article_text = response.css("article ::text").getall()
            if not article_text:
                article_text = response.css("main ::text").getall()
            excerpt = " ".join(" ".join(article_text).split())[:3000]
            yield {
                "source_name": seed.get("source_name", ""),
                "market_code": seed.get("market_code", ""),
                "product_id": seed.get("product_id", ""),
                "keyword": seed.get("keyword", ""),
                "source_url": response.url,
                "canonical_url": canonical,
                "page_title": title,
                "meta_description": description,
                "published_at": published,
                "excerpt": excerpt,
                "http_status": response.status,
                "crawled_at": captured_at,
                "robots_obeyed": True,
            }

        def capture_failure(self, failure):
            request = failure.request
            seed = request.cb_kwargs["seed"]
            yield {
                "source_name": seed.get("source_name", ""),
                "market_code": seed.get("market_code", ""),
                "product_id": seed.get("product_id", ""),
                "keyword": seed.get("keyword", ""),
                "source_url": request.url,
                "page_title": "",
                "meta_description": "",
                "excerpt": "",
                "http_status": None,
                "crawled_at": captured_at,
                "robots_obeyed": True,
                "fetch_error": failure.getErrorMessage(),
            }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    process = CrawlerProcess(
        settings={
            "BOT_NAME": "fashion_trend_radar",
            "USER_AGENT": (
                "FashionTrendRadarResearchBot/0.1 "
                "(+https://github.com/780662833/fashion-trend-radar)"
            ),
            "ROBOTSTXT_OBEY": True,
            "DOWNLOAD_DELAY": 2.0,
            "CONCURRENT_REQUESTS": 8,
            "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
            "AUTOTHROTTLE_ENABLED": True,
            "AUTOTHROTTLE_START_DELAY": 2.0,
            "AUTOTHROTTLE_MAX_DELAY": 30.0,
            "COOKIES_ENABLED": False,
            "TELNETCONSOLE_ENABLED": False,
            "RETRY_ENABLED": True,
            "LOG_LEVEL": "INFO",
            "FEEDS": {
                str(output.resolve()): {
                    "format": "jsonlines",
                    "encoding": "utf-8",
                    "overwrite": True,
                }
            },
        }
    )
    process.crawl(PublicSeedSpider)
    process.start()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch only explicitly allowlisted public page URLs. "
            "The spider obeys robots.txt and does not follow links."
        )
    )
    parser.add_argument("--seeds", default="configs/manual_research/public_page_seeds.csv")
    parser.add_argument("--out", default="data/raw/public_pages/crawl.jsonl")
    args = parser.parse_args()
    try:
        seeds = load_seeds(args.seeds)
        run_crawl(seeds, args.out)
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "status": "completed",
        "seed_count": len(seeds),
        "output": str(Path(args.out).resolve()),
        "followed_links": False,
        "robots_obeyed": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
