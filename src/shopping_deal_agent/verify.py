"""The verification gate: turns untrusted parser output into an Observation.

Anything uncertain becomes UNVERIFIED / BLOCKED / ERROR. Only a fresh,
same-run, non-cached product page (or official endpoint) with an unambiguous,
currency-matching price can be VERIFIED.
"""
from __future__ import annotations

from .clock import utcnow
from .config import Settings
from .fetch.compliance import detect_access_block
from .money import currencies_match, discount_pct
from .models import (
    Availability,
    FetchResult,
    Observation,
    RawProduct,
    SourceKind,
    VerificationStatus,
    WatchItem,
)

_TRUSTED_SOURCES = frozenset(
    {SourceKind.PRODUCT_PAGE, SourceKind.OFFICIAL_API, SourceKind.FIXTURE}
)
_MIN_CONFIDENCE = 0.5


def verify(
    result: FetchResult,
    raw: RawProduct,
    watch: WatchItem,
    settings: Settings,
    *,
    now=None,
) -> Observation:
    moment = now or utcnow()
    fetched_at = result.fetched_at or moment
    age = max(0.0, (moment - fetched_at).total_seconds())
    notes = list(raw.notes)
    problems: list[str] = []
    status = VerificationStatus.VERIFIED
    source_kind = result.source_kind

    blocked = result.blocked_reason or detect_access_block(
        result.status_code, result.html
    )
    if blocked:
        status = VerificationStatus.BLOCKED
        problems.append(blocked)
    elif result.error:
        status = VerificationStatus.ERROR
        problems.append(f"fetch error: {result.error}")
    elif result.status_code is None or result.status_code >= 400:
        problems.append(f"unusable http status: {result.status_code}")
    elif not result.html.strip():
        problems.append("empty page body")

    if source_kind not in _TRUSTED_SOURCES:
        problems.append(
            f"untrusted source {source_kind.value}: cached search results and "
            "third-party listings never substantiate a discount"
        )
    if source_kind is SourceKind.FIXTURE:
        if settings.allow_fixture_alerts:
            source_kind = SourceKind.OFFICIAL_API
            notes.append("local fixture promoted to official-data stand-in (demo only)")
        else:
            problems.append("fixture data is not a live product page")
    if result.from_cache:
        problems.append("cached response cannot substantiate a price or discount")
    if age > settings.page_max_age_seconds:
        problems.append(f"stale extraction ({age:.0f}s old)")
    if raw.price is None:
        problems.append("no unambiguous current price")
    if len(raw.price_candidates) > 1:
        problems.append(
            "conflicting price candidates: "
            + ", ".join(str(value) for value in raw.price_candidates)
        )
    if raw.currency is None:
        problems.append("price currency could not be established")
    elif not currencies_match(raw.currency, watch.target_currency):
        problems.append(
            f"currency mismatch: page={raw.currency}, watch={watch.target_currency}"
        )
    if raw.confidence < _MIN_CONFIDENCE:
        problems.append(f"low parser confidence ({raw.confidence:.2f})")

    availability = raw.availability
    if availability is Availability.UNKNOWN and raw.sizes:
        availability = (
            Availability.IN_STOCK
            if any(raw.sizes.values())
            else Availability.OUT_OF_STOCK
        )
    if availability is Availability.OUT_OF_STOCK and not any(raw.sizes.values()):
        problems.append("product appears unavailable; a discount would be misleading")

    if problems and status is VerificationStatus.VERIFIED:
        status = VerificationStatus.UNVERIFIED

    is_verified = status is VerificationStatus.VERIFIED
    is_fresh = (
        is_verified
        and not result.from_cache
        and age <= settings.page_max_age_seconds
    )
    effective_price = raw.price if is_verified else None
    effective_original = raw.original_price if is_verified else None

    return Observation(
        watch_id=watch.id or 0,
        url=result.url,
        status=status,
        source_kind=source_kind,
        transport=result.transport,
        fetched_at=fetched_at,
        is_fresh=is_fresh,
        from_cache=result.from_cache,
        title=raw.title,
        price=effective_price,
        original_price=effective_original,
        currency=raw.currency,
        discount_pct=discount_pct(effective_price, effective_original),
        availability=availability,
        sizes=dict(raw.sizes),
        colors=raw.colors,
        promotions=raw.promotions,
        strategy=raw.strategy,
        confidence=raw.confidence,
        notes=tuple([*notes, *problems]),
        evidence=dict(raw.evidence),
    )
