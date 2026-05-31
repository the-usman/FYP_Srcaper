"""
Playwright Cloudflare bypass — async worker with fast fetch + single-pass search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

from scrapy.exceptions import NotConfigured
from scrapy.http import HtmlResponse, Request

logger = logging.getLogger(__name__)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

FAST_FETCH_JS = """
async (url) => {
  const r = await fetch(url, { credentials: 'include', redirect: 'follow' });
  return { status: r.status, text: await r.text() };
}
"""

GRAB_CSE_LINKS_JS = """
() => {
  const out = [];
  document.querySelectorAll('a.gs-title').forEach((a) => {
    const h = a.href || '';
    if (h.includes('urdupoint.com') && h.endsWith('.html') && !h.includes('search.php'))
      out.push(h);
  });
  return out;
}
"""

CSE_CLICK_NEXT_JS = """
() => {
  const cur = document.querySelector('.gsc-cursor-current-page');
  if (!cur) return { ok: false, reason: 'no-current' };

  const isPage = (el) => el && el.classList && el.classList.contains('gsc-cursor-page');

  let next = cur.nextElementSibling;
  while (next && !isPage(next)) next = next.nextElementSibling;

  if (!next) {
    const pages = [...document.querySelectorAll('.gsc-cursor-page')];
    const idx = pages.indexOf(cur);
    if (idx >= 0 && idx + 1 < pages.length) next = pages[idx + 1];
  }

  if (!next) {
    next = document.querySelector('.gsc-cursor-next-page, .gsc-cursor-next');
  }

  if (!next || next === cur) return { ok: false, reason: 'no-next' };

  const label = (next.innerText || next.textContent || '').trim();
  next.click();
  return { ok: true, label };
}
"""

_ARTICLE_URL_RE = re.compile(
    r"https?://(?:www\.)?urdupoint\.com/daily/[^\s\"'<>]+\.html",
    re.IGNORECASE,
)

BLOCK_RESOURCE_TYPES = {"image", "media", "font"}
BLOCK_URL_PARTS = (
    "googletagmanager",
    "google-analytics",
    "doubleclick",
    "googlesyndication",
    "facebook.net",
    "twitter.com",
    "adservice",
    "ads.",
    "cse.google.com/ads",
)


@dataclass
class _FetchJob:
    request: Request
    referer: str
    future: Future


def _windows_event_loop_policy():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


class _PlaywrightAsyncWorker:
    def __init__(self, headless: bool, wait_ms: int, fast_wait_ms: int):
        self.headless = headless
        self.wait_ms = wait_ms
        self.fast_wait_ms = fast_wait_ms
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._jobs: asyncio.Queue | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._stop = False
        self._cf_cleared = False

    def start(self):
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="playwright-async")
        self._thread.start()
        if not self._ready.wait(timeout=120):
            raise RuntimeError("Playwright worker failed to start within 120s")
        if self._error:
            raise self._error

    def stop(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout=30)
        if self._thread:
            self._thread.join(timeout=30)

    def fetch(self, request: Request, referer: str, timeout: int = 120) -> HtmlResponse:
        if not self._loop:
            raise RuntimeError("Playwright worker not started")
        future: Future = Future()
        job = _FetchJob(request=request, referer=referer, future=future)
        asyncio.run_coroutine_threadsafe(self._jobs.put(job), self._loop).result(timeout=10)
        return future.result(timeout=timeout)

    def _thread_main(self):
        _windows_event_loop_policy()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as exc:
            self._error = exc
            logger.exception("Playwright worker crashed: %s", exc)
        finally:
            self._ready.set()
            self._loop.close()

    async def _route_handler(self, route):
        if route.request.resource_type in BLOCK_RESOURCE_TYPES:
            await route.abort()
            return
        url = route.request.url.lower()
        if any(p in url for p in BLOCK_URL_PARTS):
            await route.abort()
            return
        await route.continue_()

    async def _async_main(self):
        from playwright.async_api import async_playwright

        self._jobs = asyncio.Queue()
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            self._context = await self._browser.new_context(
                user_agent=CHROME_UA,
                locale="ur-PK",
                viewport={"width": 1280, "height": 720},
            )
            await self._context.route("**/*", self._route_handler)
            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
            self._page = await self._context.new_page()
            logger.info("Playwright Chromium started (fast mode)")
            self._ready.set()

            while not self._stop:
                try:
                    job = await asyncio.wait_for(self._jobs.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    result = await self._fetch_async(job.request, job.referer)
                    job.future.set_result(result)
                except Exception as exc:
                    job.future.set_exception(exc)
        except Exception as exc:
            self._error = exc
            self._ready.set()
            raise
        finally:
            await self._cleanup()

    async def _shutdown(self):
        self._stop = True
        await self._cleanup()

    async def _cleanup(self):
        if self._page and not self._page.is_closed():
            await self._page.close()
        self._page = None
        if self._context:
            await self._context.close()
        self._context = None
        if self._browser:
            await self._browser.close()
        self._browser = None
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None

    async def _get_page(self):
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
        return self._page

    async def _wait_cloudflare(self, page, short: bool = False):
        if self._cf_cleared and short:
            await page.wait_for_timeout(self.fast_wait_ms)
            return
        for _ in range(6):
            title = await page.title()
            if "Just a moment" not in (title or ""):
                self._cf_cleared = True
                break
            await page.wait_for_timeout(1500)
        await page.wait_for_timeout(self.fast_wait_ms if short else self.wait_ms)

    async def _wake_lazy_scripts(self, page):
        await page.evaluate(
            """() => {
                ['mousemove', 'keydown', 'touchstart'].forEach((e) =>
                    window.dispatchEvent(new Event(e, { bubbles: true }))
                );
            }"""
        )
        await page.wait_for_timeout(800)

    async def _find_cse_context(self, page):
        """Frame or Page that hosts Google CSE result links."""
        if await page.locator("a.gs-title").count() > 0:
            return page
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                if await frame.locator("a.gs-title").count() > 0:
                    return frame
            except Exception:
                continue
        return page

    async def _grab_cse_links(self, ctx, seen: set[str]) -> int:
        hrefs = await ctx.evaluate(GRAB_CSE_LINKS_JS)
        added = 0
        for h in hrefs:
            if h not in seen:
                seen.add(h)
                added += 1
        return added

    def _capture_urls_from_body(self, seen: set[str], body: str) -> int:
        added = 0
        for match in _ARTICLE_URL_RE.finditer(body):
            url = match.group(0).split("&")[0].rstrip("\\")
            if "search.php" in url:
                continue
            if url not in seen:
                seen.add(url)
                added += 1
        return added

    async def _cse_click_next(self, ctx) -> tuple[bool, str]:
        result = await ctx.evaluate(CSE_CLICK_NEXT_JS)
        return bool(result.get("ok")), str(result.get("label") or result.get("reason") or "")

    async def _wait_cse_page_change(self, ctx, page, prev_label: str) -> None:
        try:
            await ctx.wait_for_function(
                """(prev) => {
                  const cur = document.querySelector('.gsc-cursor-current-page');
                  if (!cur) return false;
                  const t = (cur.innerText || cur.textContent || '').trim();
                  return t !== prev;
                }""",
                arg=prev_label,
                timeout=15000,
            )
        except Exception:
            await page.wait_for_timeout(2800)
        else:
            await page.wait_for_timeout(700)

    async def _collect_search_links(self, page, request: Request, max_pages: int) -> HtmlResponse:
        url = request.url
        seen: set[str] = set()

        async def on_response(response):
            try:
                if response.status != 200:
                    return
                host = urlparse(response.url).hostname or ""
                if not any(
                    x in host
                    for x in ("google", "urdupoint", "gstatic", "googleapis", "cse")
                ):
                    return
                body = await response.text()
                self._capture_urls_from_body(seen, body)
            except Exception:
                pass

        page.on("response", on_response)

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self._wait_cloudflare(page)
        await self._wake_lazy_scripts(page)
        try:
            await page.wait_for_selector("a.gs-title, .gsc-result", timeout=20000)
        except Exception:
            logger.warning("Google CSE slow load; collecting anyway")

        ctx = await self._find_cse_context(page)
        scrape_all = not max_pages or max_pages <= 0
        # Google CSE returns at most 100 results (10 pages × 10 links)
        max_rounds = 12 if scrape_all else max(1, max_pages)
        await self._grab_cse_links(ctx, seen)

        stale_streak = 0
        for round_idx in range(max_rounds - 1):
            before = len(seen)
            prev_label = await ctx.evaluate(
                """() => {
                  const cur = document.querySelector('.gsc-cursor-current-page');
                  return cur ? (cur.innerText || cur.textContent || '').trim() : '';
                }"""
            )

            ok, label = await self._cse_click_next(ctx)
            if not ok:
                logger.info("CSE pagination ended: %s (round %d)", label, round_idx + 1)
                break

            await self._wait_cse_page_change(ctx, page, prev_label)
            try:
                await ctx.wait_for_selector("a.gs-title", timeout=12000)
            except Exception:
                pass

            await self._grab_cse_links(ctx, seen)
            added = len(seen) - before
            logger.debug("CSE round %d (%s): +%d links, total %d", round_idx + 2, label, added, len(seen))

            if added == 0:
                stale_streak += 1
                if stale_streak >= 2:
                    logger.info("CSE: no new links for %d rounds; stopping at %d", stale_streak, len(seen))
                    break
            else:
                stale_streak = 0

        links = list(seen)
        pages_label = "all" if scrape_all else str(max_pages)
        logger.info(
            "Collected %d CSE links (max %d Google pages; mode=%s)",
            len(links),
            max_rounds,
            pages_label,
        )
        if scrape_all and len(links) < 100:
            logger.info(
                "Google CSE caps at 100 results per query; got %d unique article URLs",
                len(links),
            )
        body = json.dumps({"links": links}, ensure_ascii=False).encode("utf-8")
        return HtmlResponse(url=url, status=200, body=body, encoding="utf-8", request=request)

    async def _fast_fetch(self, page, url: str, request: Request) -> HtmlResponse:
        result = await page.evaluate(FAST_FETCH_JS, url)
        await page.wait_for_timeout(100)
        return HtmlResponse(
            url=url,
            status=result["status"],
            body=result["text"].encode("utf-8"),
            encoding="utf-8",
            request=request,
        )

    async def _fetch_async(self, request: Request, referer_header: str) -> HtmlResponse:
        page = await self._get_page()

        if request.meta.get("playwright_search_collect"):
            max_pages = int(request.meta.get("cse_max_pages", 5))
            return await self._collect_search_links(page, request, max_pages)

        if request.meta.get("playwright_fast"):
            return await self._fast_fetch(page, request.url, request)

        if request.method.upper() == "POST":
            referer = referer_header or request.meta.get("referer", "")
            if referer and page.url != referer:
                await page.goto(referer, wait_until="domcontentloaded", timeout=60000)
                await self._wait_cloudflare(page)
            form = dict(parse_qsl(request.body.decode("utf-8", errors="replace")))
            result = await page.evaluate(
                """async ({ url, form, referer }) => {
                  const body = new URLSearchParams(form);
                  const resp = await fetch(url, {
                    method: 'POST', body, credentials: 'include',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    referrer: referer,
                  });
                  return { status: resp.status, text: await resp.text() };
                }""",
                {"url": request.url, "form": form, "referer": referer or page.url},
            )
            return HtmlResponse(
                url=request.url,
                status=result["status"],
                body=result["text"].encode("utf-8"),
                encoding="utf-8",
                request=request,
            )

        await page.goto(request.url, wait_until="domcontentloaded", timeout=60000)
        await self._wait_cloudflare(page, short=True)
        html = await page.content()
        return HtmlResponse(
            url=page.url,
            status=200,
            body=html.encode("utf-8"),
            encoding="utf-8",
            request=request,
        )


class PlaywrightBypassMiddleware:
    def __init__(self, domains: tuple[str, ...], headless: bool, wait_ms: int, fast_wait_ms: int):
        self.domains = domains
        self.headless = headless
        self.wait_ms = wait_ms
        self.fast_wait_ms = fast_wait_ms
        self._worker: _PlaywrightAsyncWorker | None = None

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool("PLAYWRIGHT_BYPASS_ENABLED", False):
            raise NotConfigured("PLAYWRIGHT_BYPASS_ENABLED is False")
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError as exc:
            raise NotConfigured(
                "pip install playwright && playwright install chromium"
            ) from exc

        domains = crawler.settings.getlist("PLAYWRIGHT_BYPASS_DOMAINS") or [
            "urdupoint.com",
            "www.urdupoint.com",
        ]
        mw = cls(
            domains=tuple(domains),
            headless=crawler.settings.getbool("PLAYWRIGHT_HEADLESS", True),
            wait_ms=crawler.settings.getint("PLAYWRIGHT_WAIT_MS", 2500),
            fast_wait_ms=crawler.settings.getint("PLAYWRIGHT_FAST_WAIT_MS", 300),
        )
        from scrapy import signals

        crawler.signals.connect(mw.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(mw.spider_closed, signal=signals.spider_closed)
        return mw

    def spider_opened(self, spider):
        self._worker = _PlaywrightAsyncWorker(self.headless, self.wait_ms, self.fast_wait_ms)
        self._worker.start()
        spider.logger.info("Playwright async worker ready (fast mode)")

    def spider_closed(self, spider):
        if self._worker:
            self._worker.stop()
            self._worker = None

    def _domain_match(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in self.domains)

    def _should_bypass(self, request: Request, spider) -> bool:
        if request.meta.get("dont_playwright"):
            return False
        if not spider.settings.getbool("PLAYWRIGHT_BYPASS_ENABLED", False):
            if not getattr(spider, "playwright_bypass", False):
                return False
        return self._domain_match(request.url)

    def _header_get(self, request: Request, name: str) -> str:
        val = request.headers.get(name) or request.headers.get(name.encode())
        if not val:
            return ""
        raw = val[0] if isinstance(val, list) else val
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    def _do_fetch(self, request: Request) -> HtmlResponse:
        if not self._worker:
            raise RuntimeError("Playwright worker not started")
        return self._worker.fetch(request, self._header_get(request, "Referer"))

    def process_request(self, request, spider):
        if not self._should_bypass(request, spider):
            return None
        if not request.meta.get("playwright_fast") and not request.meta.get("playwright_search_collect"):
            spider.logger.debug(f"Playwright: {request.method} {request.url}")
        try:
            response = self._do_fetch(request)
            response.flags.append("playwright")
            return response
        except Exception as exc:
            spider.logger.error(f"Playwright failed: {request.url} — {exc}")
            return None

    def process_response(self, request, response, spider):
        if response.flags and "playwright" in response.flags:
            return response
        if response.status not in (403, 503) or not self._should_bypass(request, spider):
            return response
        request.meta["playwright_fast"] = True
        try:
            pw_response = self._do_fetch(request)
            pw_response.flags.append("playwright")
            return pw_response
        except Exception as exc:
            spider.logger.error(f"Playwright retry failed: {exc}")
            return response
