"""Shared offline test helpers. No network access is ever needed."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from shopping_deal_agent.clock import utcnow
from shopping_deal_agent.config import Settings, WatchSpec
from shopping_deal_agent.models import FetchResult, SourceKind

FIXTURE_DIR = Path(__file__).parent / "fixtures"
URL = "https://www.adidas.com.sg/3-stripes-french-terry-shorts/IY2158.html"
SITE = "www.adidas.com.sg"


def load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def make_settings(**overrides) -> Settings:
    base = dict(
        database_path=Path(":memory:"),
        log_path=None,
        dry_run=True,
        allow_fixture_alerts=True,
        alert_cooldown_minutes=0,
        min_request_interval_seconds=0,
        robots_strict=True,
    )
    base.update(overrides)
    return Settings(**base)


def make_watch_spec(**overrides) -> WatchSpec:
    base = dict(
        url=URL,
        target_price=Decimal("45.00"),
        target_currency="SGD",
        sizes=["XS", "S"],
        colors=[],
        min_discount_pct=Decimal("30"),
        min_drop_pct=Decimal("5"),
        min_drop_abs=Decimal("2.00"),
    )
    base.update(overrides)
    return WatchSpec(**base)


def make_fetch(html: str = "", **overrides) -> FetchResult:
    base = dict(
        url=URL,
        final_url=URL,
        status_code=200,
        html=html,
        fetched_at=utcnow(),
        source_kind=SourceKind.FIXTURE,
        transport="fixture",
    )
    base.update(overrides)
    return FetchResult(**base)


class SequenceFetcher:
    """Returns results in order and repeats the last one afterwards."""

    def __init__(self, results: list[FetchResult]) -> None:
        self.results = list(results)
        self.calls = 0

    def fetch(self, url: str) -> FetchResult:
        index = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[index]
