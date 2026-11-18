"""Promotion extraction with deliberately conservative applicability.

A promotion is only ``applicable`` when the page explicitly says it is
unrestricted (sitewide / all orders / no exclusions). Anything with exclusions,
membership gates, minimum spends, explicit end-of-promotion language, or
unstated scope is recorded but never used to trigger an alert.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..models import Promotion

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:extra|additional)\s+(\d{1,2})\s*%\s*off\b", re.I), "percentage_off"),
    (
        re.compile(
            r"\b(\d{1,2})\s*%\s*off\s+(?:with\s+)?(?:promo\s+)?code\s+([A-Z0-9][A-Z0-9-]{2,14})\b",
            re.I,
        ),
        "code",
    ),
    (re.compile(r"\b(?:promo\s+)?code\s*[:#]\s*([A-Z0-9][A-Z0-9-]{2,14})\b", re.I), "code"),
    (re.compile(r"\b(?:S\$|SGD|US\$|\$)\s*(\d+(?:\.\d{2})?)\s*off\b", re.I), "fixed_off"),
    (re.compile(r"\bfree\s+(?:standard\s+)?shipping\b", re.I), "free_shipping"),
)

# None of the patterns above capture a date, so there was never a signal that
# a matched promotion is still running rather than a leftover/stale banner
# (common with cached or templated markup). An explicit end-of-promotion
# phrase overrides any "unrestricted" marker in the same window: a page
# saying "this offer has ended" must never read as a live, applicable deal.
_EXPIRED_MARKERS = (
    "has ended", "offer ended", "offer has ended", "sale has ended",
    "sale ended", "promotion has ended", "promotion ended", "deal has ended",
    "expired", "no longer valid", "no longer available",
)
_EXCLUSION_MARKERS = (
    "members only", "member exclusive", "selected items", "selected styles",
    "exclusions apply", "excludes", "new arrivals", "app only", "minimum spend",
    "min. spend", "min spend", "while stocks last", "selected colours",
    "selected colors", "not valid on", "cannot be combined",
)
_UNRESTRICTED_MARKERS = (
    "sitewide", "site-wide", "storewide", "all orders", "all items",
    "no exclusions", "applies to all", "no minimum", "everything",
)
_WINDOW = 200


def _applicability(text: str, start: int, end: int) -> tuple[bool, str]:
    window = text[max(0, start - _WINDOW) : min(len(text), end + _WINDOW)].casefold()
    for marker in _EXPIRED_MARKERS:
        if marker in window:
            return False, f"promotion text indicates it has expired ({marker})"
    for marker in _EXCLUSION_MARKERS:
        if marker in window:
            return False, f"restricted promotion ({marker}); needs manual confirmation"
    for marker in _UNRESTRICTED_MARKERS:
        if marker in window:
            return True, f"explicitly unrestricted ({marker})"
    return False, "applicability not stated on page; treated as unverified"


def extract_promotions(text: str) -> tuple[Promotion, ...]:
    content = text or ""
    results: list[Promotion] = []
    seen: set[str] = set()
    for pattern, kind in _PATTERNS:
        for match in pattern.finditer(content):
            description = " ".join(match.group(0).split())
            fingerprint = description.casefold()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            value: Decimal | None = None
            code: str | None = None
            if kind in {"percentage_off", "fixed_off"}:
                try:
                    value = Decimal(match.group(1))
                except (InvalidOperation, IndexError):
                    value = None
            elif kind == "code":
                groups = [group for group in match.groups() if group]
                code = groups[-1] if groups else None
            applicable, reason = _applicability(content, match.start(), match.end())
            results.append(
                Promotion(
                    description=description,
                    kind=kind,
                    code=code,
                    value=value,
                    applicable=applicable,
                    reason=reason,
                )
            )
            if len(results) >= 10:
                return tuple(results)
    return tuple(results)
