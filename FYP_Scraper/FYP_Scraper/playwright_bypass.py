"""
Playwright Cloudflare bypass — async worker with fast fetch + single-pass search.
"""

from __future__ import annotations

import asyncio
import json
import logging
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

    async def _cse_current_index(self, ctx) -> int:
        pages = ctx.locator(".gsc-cursor-page")
        n = await pages.count()
        for i in range(n):
            cls = await pages.nth(i).get_attribute("class") or ""
            if "gsc-cursor-current-page" in cls:
                return i
        return -1

    async def _collect_search_links(self, page, request: Request, max_pages: int) -> HtmlResponse:
        url = request.url
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self._wait_cloudflare(page)
        await self._wake_lazy_scripts(page)
        try:
            await page.wait_for_selector("a.gs-title, .gsc-result", timeout=20000)
        except Exception:
            logger.warning("Google CSE slow load; collecting anyway")

        ctx = await self._find_cse_context(page)
        scrape_all = not max_pages or max_pages <= 0
        max_rounds = 150 if scrape_all else max(1, max_pages)
        seen: set[str] = set()
        await self._grab_cse_links(ctx, seen)

        for round_idx in range(max_rounds - 1):
            pages = ctx.locator(".gsc-cursor-page")
            n = await pages.count()
            if n < 2:
                break

            cur_idx = await self._cse_current_index(ctx)
            next_idx = (cur_idx + 1) if cur_idx >= 0 else 1
            if next_idx >= n:
                break

            next_page = pages.nth(next_idx)
            label = (await next_page.inner_text() or "").strip()
            if label in ("...", "…"):
                if next_idx + 1 < n:
                    next_page = pages.nth(next_idx + 1)
                else:
                    break

            before = len(seen)
            current_label = ""
            if cur_idx >= 0:
                try:
                    current_label = (await pages.nth(cur_idx).inner_text() or "").strip()
                except Exception:
                    pass

            try:
                await next_page.scroll_into_view_if_needed()
                await next_page.click(timeout=10000)
            except Exception as exc:
                logger.warning("CSE pagination click failed on page %d: %s", next_idx + 1, exc)
                break

            try:
                await ctx.wait_for_function(
                    """(prev) => {
                      const cur = document.querySelector('.gsc-cursor-current-page');
                      if (!cur) return false;
                      const t = (cur.innerText || cur.textContent || '').trim();
                      return t !== prev;
                    }""",
                    arg=current_label,
                    timeout=12000,
                )
            except Exception:
                await page.wait_for_timeout(2500)
            else:
                await page.wait_for_timeout(600)

            try:
                await ctx.wait_for_selector("a.gs-title", timeout=10000)
            except Exception:
                pass

            await self._grab_cse_links(ctx, seen)
            if len(seen) == before:
                logger.info("CSE page %d: no new links; stopping", next_idx + 1)
                break

        links = list(seen)
        pages_label = "all" if scrape_all else str(max_pages)
        logger.info("Collected %d CSE links (pagination rounds=%s)", len(links), pages_label)
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
