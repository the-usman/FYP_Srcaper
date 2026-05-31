"""
UrduPoint search scraper — Google CSE on search.php (optimized).

One Playwright pass collects all CSE pages; articles use fast in-page fetch.
"""

from __future__ import annotations

import json
from urllib.parse import quote, urlparse

import scrapy
from scrapy.http import Request

from FYP_Scraper.content_utils import extract_urdupoint_body, parse_urdupoint_date
from FYP_Scraper.items import NewsArticleItem


class UrduPointSearchSpider(scrapy.Spider):
    name = "urdupoint_search"
    allowed_domains = ["urdupoint.com", "www.urdupoint.com"]
    search_base = "https://www.urdupoint.com/daily/search.php"
    playwright_bypass = True

    custom_settings = {
        "PLAYWRIGHT_BYPASS_ENABLED": True,
        "PLAYWRIGHT_WAIT_MS": 2500,
        "PLAYWRIGHT_FAST_WAIT_MS": 300,
        "COOKIES_ENABLED": True,
        "CONCURRENT_REQUESTS": 8,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 8,
        "DOWNLOAD_DELAY": 0,
        "RETRY_TIMES": 1,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429],
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
            "scrapy.downloadermiddlewares.retry.RetryMiddleware": 500,
            "scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware": None,
            "FYP_Scraper.middlewares.RandomProxyMiddleware": None,
            "FYP_Scraper.playwright_bypass.PlaywrightBypassMiddleware": 580,
            "FYP_Scraper.middlewares.RandomUserAgentMiddleware": None,
        },
    }

    def __init__(self, query: str = "زیادتی", max_pages: str = "all", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.query = query
        mp = str(max_pages).strip().lower()
        # 0 / all / empty = every Google CSE page until no more results
        self.max_pages = 0 if mp in ("all", "0", "") else int(max_pages)
        self.seen_urls: set[str] = set()

    def start_requests(self):
        # num=10 is default CSE page size; keeps pagination predictable (10 pages × 10 = 100 max)
        search_url = f"{self.search_base}?q={quote(self.query)}&num=10"
        yield Request(
            url=search_url,
            callback=self.parse_search_collected,
            meta={
                "dont_proxy": True,
                "playwright_search_collect": True,
                "cse_max_pages": self.max_pages,
            },
            dont_filter=True,
        )

    def parse_search_collected(self, response):
        if "Just a moment" in response.text:
            self.logger.warning("Search blocked by Cloudflare")
            return

        try:
            data = json.loads(response.text)
            links = data.get("links", [])
        except json.JSONDecodeError:
            links = response.css("a.gs-title::attr(href)").getall()

        count = 0
        for href in links:
            url = response.urljoin(href.strip()) if isinstance(href, str) else href
            if not self._is_article_url(url):
                continue
            if url in self.seen_urls:
                continue
            self.seen_urls.add(url)
            count += 1
            yield Request(
                url=url,
                callback=self.parse_article,
                meta={
                    "url": url,
                    "date": "N/A",
                    "reported_time": "N/A",
                    "category": "ziyadati",
                    "dont_proxy": True,
                    "playwright_fast": True,
                },
                dont_filter=True,
            )

        pages_label = "all" if self.max_pages == 0 else str(self.max_pages)
        self.logger.info(
            f"Collected {len(links)} links (CSE pages={pages_label}), queued {count} articles"
        )

    def _is_article_url(self, url: str) -> bool:
        if "urdupoint.com" not in url:
            return False
        if "search.php" in url:
            return False
        path = urlparse(url).path
        return "/daily/" in path and path.endswith(".html")

    def parse_article(self, response):
        if response.status in (403, 503) or "Just a moment" in response.text:
            self.logger.warning(f"Blocked article: {response.meta['url']}")
            return

        url = response.meta["url"]
        title = (response.css("h1.urdu::text").get() or "N/A").strip()
        content = extract_urdupoint_body(response)

        if not content or len(content) < 50:
            if "livenews" in url and not response.meta.get("playwright_full_retry"):
                self.logger.info(f"Retrying livenews with full page load: {url}")
                yield Request(
                    url=url,
                    callback=self.parse_article,
                    meta={
                        **response.meta,
                        "dont_proxy": True,
                        "playwright_fast": False,
                        "playwright_full_retry": True,
                    },
                    dont_filter=True,
                )
                return
            self.logger.warning(f"Short/empty content: {url}")
            return

        date, reported_time = parse_urdupoint_date(response, url=url)

        item = NewsArticleItem()
        item["url"] = url
        item["date"] = date
        item["title"] = title
        item["content"] = content
        item["source"] = "urdupoint"
        item["reported_time"] = reported_time
        item["category"] = response.meta["category"]
        yield item
