"""Scrapling HTTP fetcher plus an optional plain-Playwright JS renderer.

Deliberately NOT used: ``StealthyFetcher``, Cloudflare solving, proxy rotation,
fingerprint spoofing or any CAPTCHA handling. Those constitute bypassing access
controls and are out of scope. The browser here only renders JavaScript for
pages that permit automated access; a blocked browser response is a blocked
response, full stop.
"""
from __future__ import annotations

import re
from typing import Callable

from ..clock import utcnow
from ..config import Settings
from ..models import FetchResult, SourceKind
from .compliance import (
    DomainRateLimiter,
    RobotsGuard,
    blocked_result,
    detect_access_block,
    retry_fetch,
)

_STRUCTURED_MARKERS = re.compile(
    r"application/ld\+json|itemprop=[\"']price|__next_data__|__nuxt__|data-product-json",
    re.IGNORECASE,
)


def _html_from_response(page: object) -> str:
    body = getattr(page, "body", None)
    if isinstance(body, bytes) and body:
        encoding = getattr(page, "encoding", None) or "utf-8"
        try:
            return body.decode(encoding, errors="replace")
        except (LookupError, TypeError):
            return body.decode("utf-8", errors="replace")
    if isinstance(body, str) and body:
        return body
    return str(getattr(page, "html_content", "") or "")


def _headers_from_response(page: object) -> dict[str, str]:
    try:
        return {str(k): str(v) for k, v in dict(getattr(page, "headers", {}) or {}).items()}
    except Exception:
        return {}


def needs_browser_rendering(html: str) -> bool:
    """A page without machine-readable product data may need JS to render it."""
    return bool(html) and not _STRUCTURED_MARKERS.search(html)


class ScraplingFetcher:
    """Compliance-gated fetcher. Network calls are injectable for tests."""

    def __init__(
        self,
        settings: Settings,
        robots: RobotsGuard | None = None,
        limiter: DomainRateLimiter | None = None,
        http_call: Callable[[str], FetchResult] | None = None,
        browser_call: Callable[[str], FetchResult] | None = None,
    ) -> None:
        self.settings = settings
        self.robots = robots or RobotsGuard(
            settings.user_agent,
            ttl_seconds=settings.robots_ttl_seconds,
            strict=settings.robots_strict,
        )
        self.limiter = limiter or DomainRateLimiter(settings.min_request_interval_seconds)
        self._http_call = http_call or self._http
        self._browser_call = browser_call or self._browser

    def fetch(self, url: str) -> FetchResult:
        allowed, reason = self.robots.check(url)
        if not allowed:
            return blocked_result(url, reason or "robots.txt disallows this path")

        self.limiter.wait(url)
        result = retry_fetch(
            lambda: self._http_call(url),
            max_retries=self.settings.max_retries,
            base_delay=self.settings.retry_base_delay_seconds,
        )
        if result.blocked_reason or result.error or not result.html:
            return result

        if self.settings.browser_fallback and needs_browser_rendering(result.html):
            rendered = self._browser_call(url)
            if rendered.error and not rendered.html:
                return result  # renderer unavailable; keep the verified HTTP result
            return rendered
        return result

    def _http(self, url: str) -> FetchResult:
        try:
            from scrapling.fetchers import Fetcher
        except ImportError as exc:
            return FetchResult(
                url=url, final_url=url, status_code=None, html="",
                fetched_at=utcnow(), transport="http",
                error=f"scrapling fetchers unavailable: {exc}",
            )
        try:
            page = Fetcher.get(
                url,
                timeout=self.settings.request_timeout_seconds,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept-Language": "en-SG,en;q=0.9",
                },
                follow_redirects=True,
                retries=0,
                # Scrapling defaults to stealthy_headers=True (forged browser
                # headers + Google referer) and a Chrome TLS impersonation.
                # Both obscure the bot identity. Send a declared UA only.
                stealthy_headers=False,
                impersonate=None,
            )
        except Exception as exc:
            return FetchResult(
                url=url, final_url=url, status_code=None, html="",
                fetched_at=utcnow(), transport="http", error=str(exc),
            )
        status = getattr(page, "status", None)
        html = _html_from_response(page)
        return FetchResult(
            url=url,
            final_url=str(getattr(page, "url", None) or url),
            status_code=int(status) if isinstance(status, int) else None,
            html=html,
            headers=_headers_from_response(page),
            fetched_at=utcnow(),
            source_kind=SourceKind.PRODUCT_PAGE,
            transport="http",
            blocked_reason=detect_access_block(
                status if isinstance(status, int) else None, html
            ),
        )

    def _browser(self, url: str) -> FetchResult:
        """Render JS with a plain headless Chromium. No stealth, no solving."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            return FetchResult(
                url=url, final_url=url, status_code=None, html="",
                fetched_at=utcnow(), transport="browser",
                error=f"playwright unavailable ({exc}); run: playwright install chromium",
            )
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=self.settings.user_agent, locale="en-SG"
                )
                page = context.new_page()
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.browser_timeout_ms,
                )
                html = page.content()
                status = response.status if response is not None else None
                final_url = page.url
                context.close()
                browser.close()
        except Exception as exc:
            return FetchResult(
                url=url, final_url=url, status_code=None, html="",
                fetched_at=utcnow(), transport="browser", error=str(exc),
            )
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=status,
            html=html,
            fetched_at=utcnow(),
            source_kind=SourceKind.PRODUCT_PAGE,
            transport="browser",
            blocked_reason=detect_access_block(status, html),
        )
