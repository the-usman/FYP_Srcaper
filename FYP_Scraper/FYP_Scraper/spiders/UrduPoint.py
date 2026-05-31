import scrapy
from scrapy.http import FormRequest, Request
from datetime import datetime
from FYP_Scraper.content_utils import extract_urdupoint_body
from FYP_Scraper.items import NewsArticleItem
import re
from scrapy.exceptions import CloseSpider

class UrduPointMultiCategorySpider(scrapy.Spider):
    name = "urdupoint_multi_category"
    allowed_domains = ["urdupoint.com"]
    ajax_url = "https://www.urdupoint.com/daily/ajax_lmore.php"

    def __init__(self, selected_category=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.all_categories = [
            {"name": "murder", "tid": "MzR_-md7UkFT-dRaP7bBneWjG6l6k8ZI-PgEXnFKGi4", "keywords": ["قتل", "ہلاک", "قتل کیس", "فائرنگ", "خودکشی", "ملزم", "گرفتار", "سزائے موت"]},
            {"name": "thief", "tid": "AqpURk9gIAWriZekSAlWjFTm6-JaAIpFg21TsAL_bdc", "keywords": ["چور", "چوری"]},
            {"name": "robbery", "tid": "peViPq0AEwKA9NQMqpjkliiX1KYWdmvNYXx8rMyFo7s", "keywords": ["ڈکیتی", "چوری", "رابری"]},
            {"name": "kidnapping", "tid": "lzBfUUr4dDvlyOqI-NCCjTdubYqtIXDwZ4HrInBHhD4", "keywords": ["اغوا", "کناپنگ", "اغواء کار"]},
            {"name": "suicide", "tid": "PR_gUXYbPn_5ocdO4-Sfm2tR452QYA-RGj9AlxVTr2M", "keywords": ["خودکشی", "اپنی جان لینا"]},
            {"name": "terrorist", "tid": "-QHTdT8y3rRt9jlraC2q1zQBUwOvu6txnhCiQ76p4-k", "keywords": ["دہشت گرد", "دہشت گردی"]},
        ]

        if not selected_category:
            raise CloseSpider("No category provided. Use -a selected_category=<name>")

        self.category = next((cat for cat in self.all_categories if cat["name"] == selected_category), None)
        if not self.category:
            raise CloseSpider(f"Invalid category: {selected_category}")

        self.page = 1

        self.geopolitical_keywords = ["امریکہ", "ایران", "اسرائیل", "غزہ", "حماس", "یوکرین", "جنگ", "روس", "فلسطین", "نیتن یاہو", "طالبان", "افغانستان", "بھارت", "سرحد", "عالمی", "بیرون ملک", "بین الاقوامی"]

        self.pakistan_locations = [
            "پاکستان", "پنجاب", "سندھ", "بلوچستان", "خیبر پختونخوا", "گلگت بلتستان", "آزاد کشمیر",
            "کراچی", "لاہور", "اسلام آباد", "پشاور", "کوئٹہ", "فیصل آباد", "راولپنڈی", "ملتان",
            "گوجرانوالہ", "حیدرآباد", "سرگودھا", "سیالکوٹ", "بہاولپور", "سکھر", "ساہیوال", "اوکاڑہ",
            "مظفرآباد", "ٹوبہ ٹیک سنگھ", "خضدار", "مردان", "نوشہرہ", "خانیوال", "لیہ", "چکوال",
            "ڈیرہ غازی خان", "جہلم", "منڈی بہاؤالدین", "نوابشاہ", "مٹیاری", "لورالائی", "رحیم یار خان",
            "قصور", "شیخوپورہ", "ننکانہ صاحب", "بھکر", "چنیوٹ", "لودھراں", "وہاڑی", "بہاولنگر",
            "راجن پور", "مظفر گڑھ", "حافظ آباد", "پاکپتن", "جھنگ", "خوشاب", "میانوالی", "سرائے عالمگیر",
            "چارسدہ", "صوابی", "شانگلہ", "سوات", "دیر", "چترال", "کوہاٹ", "بنوں", "ٹانک",
            "ہنگو", "کرک", "ڈیرہ اسماعیل خان", "مانسہرہ", "ایبٹ آباد", "ہری پور", "مالاکنڈ",
            "ٹنڈو الہ یار", "ٹنڈو محمد خان", "عمرکوٹ", "بدین", "دادو", "سانگھڑ", "میرپور خاص",
            "خیرپور", "شکارپور", "لاڑکانہ", "جیکب آباد", "کندھ کوٹ", "گھوٹکی", "کشمور", "ٹھٹھہ",
            "حب", "قلات", "چمن", "ژوب", "سبی", "نصیر آباد", "جعفرآباد", "تربت", "گوادر",
            "گلگت", "سکردو", "ہنزہ", "چلاس", "باغ", "راولا کوٹ", "میرپور", "کوٹلی", "نیلم"
        ]

    def start_requests(self):
        yield FormRequest(
            url=self.ajax_url,
            formdata={"act": "get_more_tag_news", "tid": self.category["tid"], "m": str(self.page)},
            callback=self.parse_ajax,
            meta={"keywords": self.category["keywords"]},
            dont_filter=True
        )

    def parse_ajax(self, response):
        if not response.text.strip().startswith('{'):
            self.logger.warning("Non-JSON response. Skipping page.")
            return

        json_data = response.json()
        html = json_data.get("data", "")
        sel = scrapy.Selector(text=html)
        articles = sel.css("li.item_shadow")

        if not articles:
            self.logger.info("No more articles.")
            return

        for article in articles:
            url = article.css("a::attr(href)").get()
            if not url:
                continue

            date_parts = article.css("div.item_date *::text").getall()
            date_str = " ".join(t.strip() for t in date_parts if t.strip())
            match = re.search(r"(\d{1,2}) (\w+) (\d{4})", date_str)
            if not match:
                continue

            day, month_urdu, year = match.groups()
            month_map = {
                "جنوری": "Jan", "فروری": "Feb", "مارچ": "Mar", "اپریل": "Apr",
                "مئی": "May", "جون": "Jun", "جولائی": "Jul", "اگست": "Aug",
                "ستمبر": "Sep", "اکتوبر": "Oct", "نومبر": "Nov", "دسمبر": "Dec"
            }
            date_final = f"{day} {month_map.get(month_urdu, 'Unknown')} {year}"
            try:
                parsed_date = datetime.strptime(date_final, "%d %b %Y")
            except ValueError:
                continue

            if parsed_date < datetime(2015, 1, 1):
                continue

            reported_time = next((t.strip() for t in date_parts if ":" in t.strip()), "N/A")

            yield Request(
                url=url,
                callback=self.parse_article,
                meta={
                    "url": url,
                    "date": date_final,
                    "reported_time": reported_time,
                    "category": self.category["name"],
                    "keywords": self.category["keywords"]
                },
                dont_filter=True
            )

        self.page += 1
        yield from self.start_requests()

    def parse_article(self, response):
        url = response.meta["url"]
        date = response.meta["date"]
        reported_time = response.meta["reported_time"]
        category = response.meta["category"]
        keywords = response.meta["keywords"]

        title = response.css("h1.urdu::text").get(default="N/A").strip()
        content = extract_urdupoint_body(response)

        if not any(k in title or k in content for k in keywords):
            return
        if any(k in title or k in content for k in self.geopolitical_keywords):
            return
        if not any(loc in title or loc in content for loc in self.pakistan_locations):
            return

        item = NewsArticleItem()
        item["url"] = url
        item["date"] = date
        item["title"] = title
        item["content"] = content
        item["source"] = "urdupoint"
        item["reported_time"] = reported_time
        item["category"] = category
        yield item
