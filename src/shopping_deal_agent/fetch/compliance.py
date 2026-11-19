"""Compliance gate: robots directives, per-host throttling, conservative retries.

Design rules:
* 401/403 and CAPTCHA/login walls are never retried and never routed through a
  different transport to "get around" them.
* Only transient network failures and 408/425/429/5xx are retried, with jitter.
* If robots.txt cannot be verified, strict mode refuses the fetch.
"""
from __future__ import annotations

import random
import re
import threading
import time
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from ..clock import utcnow
from ..models import FetchResult

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
HARD_BLOCK_STATUS = frozenset({401, 403})

_BLOCK_MARKERS = (
    "unable to give you access",
    "verify you are human",
    "are you a robot",
    "access denied",
    "403 - forbidden",
    "cf-challenge",
    "enable javascript and cookies to continue",
    "complete the security check",
)

_TAG_RE = re.compile(r"<[^>]+>")


def detect_access_block(status_code: int | None, html: str) -> str | None:
    """Return a reason when the response is clearly an access barrier."""
    if status_code in HARD_BLOCK_STATUS:
        return f"http {status_code}: access denied"
    snippet = _TAG_RE.sub(" ", (html or "")[:10000]).lower()
    for marker in _BLOCK_MARKERS:
        if marker in snippet:
            return f"access barrier detected: {marker}"
    if "captcha" in snippet and len(snippet) < 3000:
        return "captcha challenge detected"
    return None


def is_retryable(result: FetchResult) -> bool:
    if result.blocked_reason:
        return False
    if result.error:
        return True
    if result.status_code is None:
        return True
    return result.status_code in RETRYABLE_STATUS


def retry_fetch(
    call: Callable[[], FetchResult],
    *,
    max_retries: int = 2,
    base_delay: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> FetchResult:
    generator = rng or random
    for attempt in range(max(0, max_retries) + 1):
        result = call()
        if not is_retryable(result) or attempt == max_retries:
            return result
        delay = base_delay * (2**attempt) + generator.uniform(0, 0.25)
        sleeper(delay)
    # Reached only when max_retries < 0: max(0, max_retries) + 1 == 1, so the
    # loop runs exactly once and falls through here instead of returning
    # inside the loop body. `result` is still the one response we got, so
    # this remains correct -- it's a defensive fallback, not dead code.
    return result


class RobotsGuard:
    """Per-origin robots.txt cache with a configurable TTL."""

    def __init__(
        self,
        user_agent: str,
        ttl_seconds: int = 21600,
        strict: bool = True,
        loader: Callable[[str], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.ttl_seconds = ttl_seconds
        self.strict = strict
        self.loader = loader or self._default_loader
        self.clock = clock
        self._cache: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._lock = threading.Lock()

    def _default_loader(self, robots_url: str) -> str | None:
        try:
            request = Request(robots_url, headers={"User-Agent": self.user_agent})
            with urlopen(request, timeout=10) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception:
            return None

    def _parser(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        now = self.clock()
        with self._lock:
            cached = self._cache.get(origin)
            if cached and now - cached[0] < self.ttl_seconds:
                return cached[1]
            text = self.loader(f"{origin}/robots.txt")
            parser: RobotFileParser | None = None
            if text is not None:
                parser = RobotFileParser()
                parser.parse(text.splitlines())
            self._cache[origin] = (now, parser)
            return parser

    def check(self, url: str) -> tuple[bool, str | None]:
        parser = self._parser(url)
        if parser is None:
            if self.strict:
                return False, "robots.txt unavailable; refusing fetch in strict mode"
            return True, None
        try:
            allowed = parser.can_fetch(self.user_agent, url)
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"robots.txt parse error: {exc}"
        return (True, None) if allowed else (False, "robots.txt disallows this path")

    def crawl_delay(self, url: str) -> float | None:
        parser = self._parser(url)
        if parser is None:
            return None
        try:
            value = parser.crawl_delay(self.user_agent)
            return float(value) if value is not None else None
        except Exception:
            return None


class DomainRateLimiter:
    """Serialise requests per host and honour a minimum inter-request gap."""

    def __init__(
        self,
        min_interval_seconds: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = max(0.0, min_interval_seconds)
        self.clock = clock
        self.sleeper = sleeper
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> float:
        host = urlparse(url).netloc.lower() or "unknown"
        with self._lock:
            now = self.clock()
            earliest = self._next_slot.get(host, now)
            delay = max(0.0, earliest - now)
            self._next_slot[host] = max(now, earliest) + self.min_interval
        if delay > 0:
            self.sleeper(delay)
        return delay


def blocked_result(url: str, reason: str, transport: str = "http") -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=None,
        html="",
        fetched_at=utcnow(),
        transport=transport,
        blocked_reason=reason,
    )
