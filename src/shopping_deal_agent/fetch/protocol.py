"""Transport contract. Anything implementing ``fetch`` can be injected."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import FetchResult


class FetchError(Exception):
    """Base class for fetch-layer failures."""


class AccessBlocked(FetchError):
    """403 / 401 / CAPTCHA / login wall. Never retried, never bypassed."""


class RobotsDisallowed(FetchError):
    """robots.txt disallows this path, or it could not be verified."""


@runtime_checkable
class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchResult:  # pragma: no cover - protocol
        ...
