"""Orchestration tests for ScraplingFetcher.fetch(): robots gate, rate limiting
across retries, and the browser-fallback branch. All network/browser calls are
injected fakes; nothing touches the real network or a real browser.
"""
from __future__ import annotations

from helpers import URL, make_settings
from shopping_deal_agent.fetch.compliance import DomainRateLimiter, RobotsGuard
from shopping_deal_agent.fetch.scrapling_fetcher import ScraplingFetcher
from shopping_deal_agent.models import FetchResult


def _fetcher(settings=None, robots=None, limiter=None, http_call=None, browser_call=None):
    return ScraplingFetcher(
        settings
        or make_settings(
            max_retries=2, retry_base_delay_seconds=0, min_request_interval_seconds=0
        ),
        robots=robots,
        limiter=limiter,
        http_call=http_call,
        browser_call=browser_call,
    )


def test_robots_block_short_circuits_before_any_http_call():
    calls: list[str] = []
    robots = RobotsGuard("TestBot", loader=lambda _: "User-agent: *\nDisallow: /")

    def http_call(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(url=url, final_url=url, status_code=200, html="<p>ok</p>")

    fetcher = _fetcher(robots=robots, http_call=http_call)
    result = fetcher.fetch(URL)

    assert result.blocked_reason is not None
    assert calls == []


def test_rate_limiter_is_consulted_on_every_retry_attempt():
    waits: list[str] = []

    class RecordingLimiter(DomainRateLimiter):
        def wait(self, url: str) -> float:
            waits.append(url)
            return super().wait(url)

    attempts = [
        FetchResult(url=URL, final_url=URL, status_code=503, html=""),
        FetchResult(url=URL, final_url=URL, status_code=200, html="<p>ok</p>"),
    ]

    def http_call(url: str) -> FetchResult:
        return attempts.pop(0)

    robots = RobotsGuard("TestBot", loader=lambda _: "User-agent: *\nAllow: /")
    limiter = RecordingLimiter(0.0, clock=lambda: 0.0, sleeper=lambda _: None)
    fetcher = _fetcher(
        settings=make_settings(max_retries=1, retry_base_delay_seconds=0),
        robots=robots,
        limiter=limiter,
        http_call=http_call,
    )

    result = fetcher.fetch(URL)

    assert result.status_code == 200
    assert waits == [URL, URL]  # once for the first attempt, once for the retry


def test_browser_fallback_triggers_when_html_lacks_structured_data():
    robots = RobotsGuard("TestBot", loader=lambda _: "User-agent: *\nAllow: /")
    limiter = DomainRateLimiter(0.0, sleeper=lambda _: None)
    browser_calls: list[str] = []

    def http_call(url: str) -> FetchResult:
        return FetchResult(
            url=url, final_url=url, status_code=200, html="<p>no structured data</p>"
        )

    def browser_call(url: str) -> FetchResult:
        browser_calls.append(url)
        return FetchResult(
            url=url, final_url=url, status_code=200,
            html='<script type="application/ld+json">{}</script>', transport="browser",
        )

    fetcher = _fetcher(
        settings=make_settings(browser_fallback=True, max_retries=0),
        robots=robots,
        limiter=limiter,
        http_call=http_call,
        browser_call=browser_call,
    )

    result = fetcher.fetch(URL)

    assert browser_calls == [URL]
    assert result.transport == "browser"


def test_browser_fallback_is_skipped_when_disabled():
    robots = RobotsGuard("TestBot", loader=lambda _: "User-agent: *\nAllow: /")
    limiter = DomainRateLimiter(0.0, sleeper=lambda _: None)
    browser_calls: list[str] = []

    def http_call(url: str) -> FetchResult:
        return FetchResult(
            url=url, final_url=url, status_code=200, html="<p>no structured data</p>"
        )

    fetcher = _fetcher(
        settings=make_settings(browser_fallback=False, max_retries=0),
        robots=robots,
        limiter=limiter,
        http_call=http_call,
        browser_call=lambda url: browser_calls.append(url),
    )

    result = fetcher.fetch(URL)

    assert browser_calls == []
    assert result.transport == "http"


def test_browser_context_is_closed_even_when_navigation_fails():
    """A failed page.goto() must not leak the context, and the shared browser
    process must survive to serve the next call."""
    fetcher = ScraplingFetcher(make_settings())

    class FakeContext:
        closed = False

        def new_page(self):
            raise RuntimeError("boom")

        def close(self):
            FakeContext.closed = True

    class FakeBrowser:
        is_connected_calls = 0

        def new_context(self, **kwargs):
            return FakeContext()

        def is_connected(self):
            FakeBrowser.is_connected_calls += 1
            return True

        def close(self):  # pragma: no cover - not expected to be called here
            raise AssertionError("shared browser must not be closed on a per-call failure")

    fetcher._browser_instance = FakeBrowser()
    fetcher._playwright = object()

    result = fetcher._browser(URL)

    assert result.error == "boom"
    assert FakeContext.closed is True
    assert fetcher._browser_instance is not None  # reused, not torn down
