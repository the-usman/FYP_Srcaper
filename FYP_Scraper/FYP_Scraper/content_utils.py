"""Clean UrduPoint article body text (remove ad scripts)."""

from __future__ import annotations

import re

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
