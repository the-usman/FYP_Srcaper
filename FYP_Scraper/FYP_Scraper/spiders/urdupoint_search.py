"""
UrduPoint search scraper — Google CSE on search.php (optimized).

One Playwright pass collects all CSE pages; articles use fast in-page fetch.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import quote, urlparse

import scrapy
from scrapy.http import Request

from FYP_Scraper.content_utils import extract_urdupoint_body
from FYP_Scraper.items import NewsArticleItem

MONTH_MAP = {
    "جنوری": "Jan",
    "فروری": "Feb",
    "مارچ": "Mar",
    "اپریل": "Apr",
    "مئی": "May",
    "جون": "Jun",
    "جولائی": "Jul",
    "اگست": "Aug",
    "ستمبر": "Sep",
    "اکتوبر": "Oct",
    "نومبر": "Nov",
    "دسمبر": "Dec",
}


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

    def __init__(self, query: str = "زیادتی", max_pages: str = "5", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.query = query
        self.max_pages = int(max_pages)
        self.seen_urls: set[str] = set()

    def start_requests(self):
        search_url = f"{self.search_base}?q={quote(self.query)}"
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

        self.logger.info(
            f"Collected {len(links)} links from {self.max_pages} CSE page(s), "
            f"queued {count} articles"
        )

    def _is_article_url(self, url: str) -> bool:
        if "urdupoint.com" not in url:
            return False
        if "search.php" in url:
            return False
        path = urlparse(url).path
        return "/daily/" in path and path.endswith(".html")

    def _parse_date_from_detail(self, response) -> tuple[str, str]:
        date_parts = response.css("div.item_date *::text").getall()
        if not date_parts:
            date_parts = response.css("span.date *::text, time::text").getall()
        date_str = " ".join(t.strip() for t in date_parts if t.strip())
        match = re.search(r"(\d{1,2}) (\w+) (\d{4})", date_str)
        if match:
            day, month_urdu, year = match.groups()
            month = MONTH_MAP.get(month_urdu, "Unknown")
            date_final = f"{day} {month} {year}"
            try:
                if datetime.strptime(date_final, "%d %b %Y") >= datetime(2015, 1, 1):
                    reported = next((t.strip() for t in date_parts if ":" in t.strip()), "N/A")
                    return date_final, reported
            except ValueError:
                pass
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str.strip()):
            return date_str.strip(), "N/A"
        return "N/A", "N/A"

    def parse_article(self, response):
        if response.status in (403, 503) or "Just a moment" in response.text:
            self.logger.warning(f"Blocked article: {response.meta['url']}")
            return

        url = response.meta["url"]
        title = (response.css("h1.urdu::text").get() or "N/A").strip()
        content = extract_urdupoint_body(response)

        if not content or len(content) < 50:
            self.logger.warning(f"Short/empty content: {url}")
            return

        date, reported_time = self._parse_date_from_detail(response)
        if date == "N/A":
            date = response.meta.get("date", "N/A")
            reported_time = response.meta.get("reported_time", "N/A")

        item = NewsArticleItem()
        item["url"] = url
        item["date"] = date
        item["title"] = title
        item["content"] = content
        item["source"] = "urdupoint"
        item["reported_time"] = reported_time
        item["category"] = response.meta["category"]
        yield item
