"""
UrduPoint daily search scraper.

Listing: https://www.urdupoint.com/daily/search.php?q=<query>
Pagination: ajax_lmore.php (act=get_more_search_news)
Detail: article page -> title + div.detail_txt.urdu
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

import scrapy
from scrapy.http import FormRequest, Request

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

AJAX_ACTS = (
    "get_more_search_news",
    "get_search_news",
    "get_more_daily_search",
)


class UrduPointSearchSpider(scrapy.Spider):
    name = "urdupoint_search"
    allowed_domains = ["urdupoint.com", "www.urdupoint.com"]
    ajax_url = "https://www.urdupoint.com/daily/ajax_lmore.php"
    search_base = "https://www.urdupoint.com/daily/search.php"

    def __init__(self, query: str = "زیادتی", max_pages: str = "50", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.query = query
        self.max_pages = int(max_pages)
        self.page = 1
        self._ajax_act_index = 0
        self.seen_urls: set[str] = set()

    def start_requests(self):
        yield Request(
            url=f"{self.search_base}?q={quote(self.query)}",
            callback=self.parse_search_html,
            dont_filter=True,
        )
        yield from self._ajax_request()

    def _ajax_request(self):
        act = AJAX_ACTS[self._ajax_act_index]
        yield FormRequest(
            url=self.ajax_url,
            formdata={"act": act, "q": self.query, "m": str(self.page)},
            callback=self.parse_ajax_list,
            meta={"ajax_act": act},
            dont_filter=True,
        )

    def parse_search_html(self, response):
        if "Just a moment" in response.text or response.status in (403, 503):
            self.logger.warning("Search page blocked; relying on AJAX listing.")
            return
        yield from self._yield_detail_requests(response, response)

    def parse_ajax_list(self, response):
        act = response.meta.get("ajax_act", AJAX_ACTS[0])
        text = response.text.strip()

        if not text.startswith("{"):
            self.logger.warning(f"Non-JSON from {act} page {self.page}")
            if self._try_next_ajax_act():
                yield from self._ajax_request()
            return

        html = response.json().get("data", "")
        if not html.strip():
            if self._try_next_ajax_act():
                yield from self._ajax_request()
                return
            self.logger.info("No more search results.")
            return

        sel = scrapy.Selector(text=html)
        count = 0
        for req in self._yield_detail_requests(sel, response):
            count += 1
            yield req

        if count == 0:
            self.logger.info(f"No article links on page {self.page} ({act}).")
            return

        if self.page < self.max_pages:
            self.page += 1
            yield from self._ajax_request()

    def _try_next_ajax_act(self) -> bool:
        if self._ajax_act_index + 1 < len(AJAX_ACTS):
            self._ajax_act_index += 1
            self.logger.info(f"Trying AJAX act: {AJAX_ACTS[self._ajax_act_index]}")
            return True
        return False

    def _yield_detail_requests(self, sel, response):
        articles = sel.css("li.item_shadow")
        if not articles:
            articles = sel.css("ul.listing li, div.search_results li, div.item_list li")

        for article in articles:
            url = article.css("a::attr(href)").get()
            if not url:
                continue
            url = response.urljoin(url.strip())
            if "search.php" in url or "/daily/" not in url:
                continue
            if url in self.seen_urls:
                continue
            self.seen_urls.add(url)

            date_final, reported_time = self._extract_date(article)
            if not date_final:
                continue

            yield Request(
                url=url,
                callback=self.parse_article,
                meta={
                    "url": url,
                    "date": date_final,
                    "reported_time": reported_time,
                    "category": "ziyadati",
                },
                dont_filter=True,
            )

    def _extract_date(self, article) -> tuple[str | None, str]:
        date_parts = article.css("div.item_date *::text").getall()
        if not date_parts:
            date_parts = article.css("span.date *::text, .item_date::text").getall()
        date_str = " ".join(t.strip() for t in date_parts if t.strip())
        match = re.search(r"(\d{1,2}) (\w+) (\d{4})", date_str)
        if not match:
            return None, "N/A"

        day, month_urdu, year = match.groups()
        month = MONTH_MAP.get(month_urdu, "Unknown")
        date_final = f"{day} {month} {year}"
        try:
            parsed = datetime.strptime(date_final, "%d %b %Y")
        except ValueError:
            return None, "N/A"
        if parsed < datetime(2015, 1, 1):
            return None, "N/A"

        reported_time = next((t.strip() for t in date_parts if ":" in t.strip()), "N/A")
        return date_final, reported_time

    def parse_article(self, response):
        url = response.meta["url"]
        title = (response.css("h1.urdu::text").get() or "N/A").strip()
        raw_content = response.css("div.detail_txt.urdu *::text").getall()
        if not raw_content:
            raw_content = response.css("div.detail_txt *::text, article *::text").getall()
        content = " ".join(t.strip() for t in raw_content if t.strip())
        content = re.sub(r"googletag\.cmd\.push\([^)]*\);", "", content)

        if not content or len(content) < 50:
            self.logger.warning(f"Short/empty content: {url}")
            return

        item = NewsArticleItem()
        item["url"] = url
        item["date"] = response.meta["date"]
        item["title"] = title
        item["content"] = content
        item["source"] = "urdupoint"
        item["reported_time"] = response.meta["reported_time"]
        item["category"] = response.meta["category"]
        yield item
