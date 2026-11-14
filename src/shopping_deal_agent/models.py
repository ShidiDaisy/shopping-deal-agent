"""Domain types shared by every layer.

The single most important invariant lives on ``Observation.can_alert``:
nothing may reach a notification channel unless the data came from a fresh,
same-run product page (or a permitted official endpoint) and passed
verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"
    ERROR = "error"


class SourceKind(str, Enum):
    """Where the bytes came from. Only product pages / official APIs are alertable."""

    PRODUCT_PAGE = "product_page"
    OFFICIAL_API = "official_api"
    FIXTURE = "fixture"
    SEARCH_RESULT = "search_result"
    CACHE = "cache"
    THIRD_PARTY = "third_party"


ALERTABLE_SOURCES = frozenset({SourceKind.PRODUCT_PAGE, SourceKind.OFFICIAL_API})


class Availability(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class Condition(str, Enum):
    PRICE_TARGET = "price_le_target"
    DISCOUNT = "discount_ge_threshold"
    PROMOTION = "additional_promotion"
    RESTOCK = "size_restocked"
    PRICE_DROP = "meaningful_price_drop"


@dataclass(frozen=True)
class WatchItem:
    id: int | None
    url: str
    site: str
    target_price: Decimal | None
    target_currency: str
    sizes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    min_discount_pct: Decimal = Decimal("30")
    min_drop_pct: Decimal = Decimal("5")
    min_drop_abs: Decimal = Decimal("2.00")
    enabled: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int | None
    html: str
    headers: Mapping[str, str] = field(default_factory=dict)
    fetched_at: datetime | None = None
    source_kind: SourceKind = SourceKind.PRODUCT_PAGE
    transport: str = "http"
    from_cache: bool = False
    blocked_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Promotion:
    description: str
    kind: str = "other"
    code: str | None = None
    value: Decimal | None = None
    applicable: bool = False
    reason: str = ""
    valid_until: str | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.kind}:{self.code or ''}:{self.description.casefold().strip()}"


@dataclass(frozen=True)
class RawProduct:
    """Parser output before verification. Every field is still untrusted."""

    title: str | None = None
    price: Decimal | None = None
    original_price: Decimal | None = None
    currency: str | None = None
    availability: Availability = Availability.UNKNOWN
    sizes: Mapping[str, bool] = field(default_factory=dict)
    colors: tuple[str, ...] = ()
    promotions: tuple[Promotion, ...] = ()
    strategy: str = "none"
    confidence: float = 0.0
    price_candidates: tuple[Decimal, ...] = ()
    notes: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    watch_id: int
    url: str
    status: VerificationStatus
    source_kind: SourceKind
    transport: str
    fetched_at: datetime
    is_fresh: bool
    from_cache: bool
    title: str | None = None
    price: Decimal | None = None
    original_price: Decimal | None = None
    currency: str | None = None
    discount_pct: Decimal | None = None
    availability: Availability = Availability.UNKNOWN
    sizes: Mapping[str, bool] = field(default_factory=dict)
    colors: tuple[str, ...] = ()
    promotions: tuple[Promotion, ...] = ()
    strategy: str = "none"
    confidence: float = 0.0
    notes: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    id: int | None = None

    @property
    def is_verified(self) -> bool:
        return self.status is VerificationStatus.VERIFIED

    @property
    def can_alert(self) -> bool:
        return (
            self.is_verified
            and self.is_fresh
            and not self.from_cache
            and self.source_kind in ALERTABLE_SOURCES
            and self.price is not None
        )


@dataclass(frozen=True)
class AlertEvent:
    watch_id: int
    condition: Condition
    message: str
    dedupe_key: str
    observation_id: int | None = None
    created_at: datetime | None = None
    delivered: bool = False
    channel: str | None = None
    id: int | None = None
