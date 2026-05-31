"""Clean UrduPoint article body text (remove ad scripts) and parse dates."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

URDU_MONTH_MAP = {
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

_ISO_TZ_RE = re.compile(r"([+-]\d{2})(\d{2})$")
_URL_DATE_RE = re.compile(r"/(\d{4}-\d{2}-\d{2})/")

# googletag.cmd.push(function() { googletag.display('...'); });
_GOOGLETAG_PUSH = re.compile(r"googletag\.cmd\.push\s*\(", re.IGNORECASE)
_GOOGLETAG_DISPLAY = re.compile(r"googletag\.display\s*\([^)]*\)\s*;?", re.IGNORECASE)
_GPT_SNIPPETS = re.compile(
    r"(gpt-[a-z0-9-]+|div-gpt-ad-[a-z0-9-]+|adsbygoogle|google_ad)",
    re.IGNORECASE,
)


def _strip_balanced_parens(text: str, start: int) -> str:
    """Remove googletag.cmd.push( ... ); starting at start index."""
    i = start
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                i += 1
                while i < len(text) and text[i] in "; \t":
                    if text[i] == ";":
                        i += 1
                        break
                    i += 1
                return text[:start] + text[i:]
        i += 1
    return text[:start] + text[i:]


def clean_urdupoint_content(text: str) -> str:
    if not text:
        return ""

    while True:
        m = _GOOGLETAG_PUSH.search(text)
        if not m:
            break
        text = _strip_balanced_parens(text, m.start())

    text = _GOOGLETAG_DISPLAY.sub("", text)
    text = _GPT_SNIPPETS.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_urdupoint_body(response) -> str:
    """Extract article text from detail page, excluding scripts/ads."""
    parts = response.xpath(
        '//div[contains(@class,"detail_txt") and contains(@class,"urdu")]'
        "//*[not(self::script) and not(self::style)]/text()"
    ).getall()
    if not parts:
        parts = response.xpath(
            '//div[contains(@class,"detail_txt")]'
            "//*[not(self::script) and not(self::style)]/text()"
        ).getall()
    if not parts:
        parts = response.css("div.detail_txt ::text").getall()

    raw = " ".join(p.strip() for p in parts if p and p.strip())
    return clean_urdupoint_content(raw)


def _format_urdupoint_date(dt: datetime) -> tuple[str, str]:
    reported = dt.strftime("%H:%M")
    if reported == "00:00":
        reported = "N/A"
    return dt.strftime("%d %b %Y"), reported


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    if _ISO_TZ_RE.search(text):
        text = _ISO_TZ_RE.sub(r"\1:\2", text)
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(value)
    except Exception:
        return None


def _json_ld_date_candidates(data: Any) -> list[str]:
    found: list[str] = []
    if isinstance(data, dict):
        for key in ("datePublished", "dateModified", "dateCreated"):
            val = data.get(key)
            if isinstance(val, str):
                found.append(val)
        graph = data.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                found.extend(_json_ld_date_candidates(node))
    elif isinstance(data, list):
        for item in data:
            found.extend(_json_ld_date_candidates(item))
    return found


def _parse_urdu_item_date(response) -> tuple[str, str] | None:
    date_parts = response.css("div.item_date *::text").getall()
    if not date_parts:
        date_parts = response.css("span.date *::text, .item_date *::text").getall()
    date_str = " ".join(t.strip() for t in date_parts if t.strip())
    match = re.search(r"(\d{1,2})\s+(\S+)\s+(\d{4})", date_str)
    if not match:
        return None
    day, month_urdu, year = match.groups()
    month = URDU_MONTH_MAP.get(month_urdu)
    if not month:
        return None
    date_final = f"{day} {month} {year}"
    try:
        parsed = datetime.strptime(date_final, "%d %b %Y")
    except ValueError:
        return None
    if parsed < datetime(2015, 1, 1):
        return None
    reported = next((t.strip() for t in date_parts if ":" in t.strip()), "N/A")
    return date_final, reported


def parse_urdupoint_date(response, url: str | None = None) -> tuple[str, str]:
    """
    Extract article date from UrduPoint detail pages.

    Tries JSON-LD, meta tags, <time>, Urdu item_date block, then URL path.
    Returns (date, reported_time) e.g. ('30 May 2026', '18:00') or ('N/A', 'N/A').
    """
    page_url = url or getattr(response, "url", "") or ""

    for script in response.xpath('//script[@type="application/ld+json"]/text()').getall():
        try:
            data = json.loads(script.strip())
        except json.JSONDecodeError:
            continue
        for iso in _json_ld_date_candidates(data):
            dt = _parse_iso_datetime(iso)
            if dt and dt >= datetime(2015, 1, 1):
                return _format_urdupoint_date(dt)

    for selector in (
        'meta[property="article:published_time"]::attr(content)',
        'meta[name="article:published_time"]::attr(content)',
        'meta[property="og:article:published_time"]::attr(content)',
    ):
        meta_val = response.css(selector).get()
        if meta_val:
            dt = _parse_iso_datetime(meta_val)
            if dt and dt >= datetime(2015, 1, 1):
                return _format_urdupoint_date(dt)

    for dt_attr in response.css("time::attr(datetime)").getall():
        dt = _parse_iso_datetime(dt_attr)
        if dt and dt >= datetime(2015, 1, 1):
            return _format_urdupoint_date(dt)

    urdu = _parse_urdu_item_date(response)
    if urdu:
        return urdu

    url_match = _URL_DATE_RE.search(urlparse(page_url).path)
    if url_match:
        try:
            dt = datetime.strptime(url_match.group(1), "%Y-%m-%d")
            if dt >= datetime(2015, 1, 1):
                return _format_urdupoint_date(dt)
        except ValueError:
            pass

    return "N/A", "N/A"
