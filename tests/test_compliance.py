import random

import scrapling.fetchers as scrapling_fetchers
from helpers import URL, load_fixture, make_fetch
from shopping_deal_agent.config import Settings
from shopping_deal_agent.fetch.compliance import (
    DomainRateLimiter,
    RobotsGuard,
    detect_access_block,
    is_retryable,
    retry_fetch,
)
from shopping_deal_agent.fetch.scrapling_fetcher import ScraplingFetcher


def test_hard_block_status():
    assert detect_access_block(403, "<html>ok</html>") is not None
    assert detect_access_block(401, "<html>ok</html>") is not None
    assert detect_access_block(200, "<h1>Shorts</h1><span>S$45.00</span>") is None


def test_access_barrier_text():
    html = load_fixture("adidas_blocked.html")
    assert "unable to give you access" in detect_access_block(200, html)


def test_retryable_classification():
    assert is_retryable(make_fetch("", status_code=503)) is True
    assert is_retryable(make_fetch("", status_code=429)) is True
    assert is_retryable(make_fetch("", status_code=None, error="timeout")) is True
    assert is_retryable(make_fetch("", status_code=403, blocked_reason="blocked")) is False
    assert is_retryable(make_fetch("<p>ok</p>", status_code=200)) is False


def test_retry_recovers_from_transient_failure():
    sequence = [make_fetch("", status_code=503), make_fetch("<p>ok</p>")]
    delays = []
    result = retry_fetch(
        lambda: sequence.pop(0),
        max_retries=2,
        base_delay=0.1,
        sleeper=delays.append,
        rng=random.Random(0),
    )
    assert result.status_code == 200
    assert len(delays) == 1


def test_blocked_response_is_never_retried():
    calls = []

    def call():
        calls.append(1)
        return make_fetch("", status_code=403, blocked_reason="access denied")

    retry_fetch(call, max_retries=3, base_delay=0, sleeper=lambda _: None, rng=random.Random(0))
    assert len(calls) == 1


def test_robots_disallow():
    guard = RobotsGuard(
        "TestBot", loader=lambda _: "User-agent: *\nDisallow: /3-stripes"
    )
    allowed, reason = guard.check(URL)
    assert allowed is False
    assert "disallow" in reason


def test_robots_allow():
    guard = RobotsGuard("TestBot", loader=lambda _: "User-agent: *\nAllow: /")
    assert guard.check(URL)[0] is True


def test_robots_unavailable_is_refused_in_strict_mode():
    strict = RobotsGuard("TestBot", strict=True, loader=lambda _: None)
    assert strict.check(URL)[0] is False
    lax = RobotsGuard("TestBot", strict=False, loader=lambda _: None)
    assert lax.check(URL)[0] is True


def test_rate_limiter_spaces_requests_per_host():
    now = [0.0]
    sleeps = []

    def sleeper(delay):
        sleeps.append(delay)
        now[0] += delay

    limiter = DomainRateLimiter(
        3.0, clock=lambda: now[0], sleeper=sleeper
    )
    assert limiter.wait(URL) == 0.0
    assert limiter.wait(URL) == 3.0
    assert limiter.wait("https://www.macys.com/x") == 0.0
    assert sleeps == [3.0]


def test_http_fetch_declares_identity_without_stealth(monkeypatch):
    """Scrapling defaults add forged browser headers + a Google referer."""
    captured: dict = {}

    class FakePage:
        status = 200
        url = URL
        body = b"<html><body>ok</body></html>"
        headers = {"content-type": "text/html"}
        html_content = "<html><body>ok</body></html>"

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return FakePage()

    monkeypatch.setattr(scrapling_fetchers.Fetcher, "get", fake_get)
    result = ScraplingFetcher(Settings.from_env())._http(URL)

    assert result.status_code == 200
    assert captured.get("stealthy_headers") is False
    assert captured.get("impersonate") is None
    assert "ShoppingDealAgent" in captured["headers"]["User-Agent"]
    assert "referer" not in {k.lower() for k in captured["headers"]}
