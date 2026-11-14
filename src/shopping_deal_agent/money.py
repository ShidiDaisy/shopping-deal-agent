"""Conservative price / currency text parsing.

Ambiguity is a first-class result: a price range, multiple currencies, or a
bare ``$`` with no expected currency must never produce a confident value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_CURRENCY_CODES = {
    "S$": "SGD", "SG$": "SGD", "SGD": "SGD",
    "US$": "USD", "USD": "USD",
    "A$": "AUD", "AUD": "AUD",
    "HK$": "HKD", "HKD": "HKD",
    "RM": "MYR", "MYR": "MYR",
    "£": "GBP", "GBP": "GBP",
    "€": "EUR", "EUR": "EUR",
    "¥": "JPY", "JPY": "JPY", "CNY": "CNY", "CAD": "CAD",
    "$": None,
}

_CURRENCY_TOKEN_RE = re.compile(
    r"(S\$|SG\$|US\$|A\$|HK\$|SGD|USD|MYR|GBP|EUR|AUD|HKD|CNY|JPY|CAD|RM|£|€|\$)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"(?<![\d.])(\d{1,3}(?:[,\s]\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\d])"
)

_DISPLAY = {
    "SGD": "S$", "USD": "US$", "AUD": "A$", "HKD": "HK$",
    "MYR": "RM", "GBP": "£", "EUR": "€", "JPY": "¥", "CNY": "¥",
}


@dataclass(frozen=True)
class PriceParse:
    value: Decimal | None
    currency: str | None
    candidates: tuple[Decimal, ...]
    ambiguous: bool
    reason: str | None = None


def _to_decimal(raw: str) -> Decimal | None:
    text = raw.strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        if len(tail) == 2 and head.isdigit():
            text = f"{head}.{tail}"
        else:
            text = text.replace(",", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None


def price_candidates(text: str) -> tuple[Decimal, ...]:
    """All distinct numeric tokens, in order of appearance."""
    seen: set[Decimal] = set()
    result: list[Decimal] = []
    for match in _NUMBER_RE.finditer(text or ""):
        value = _to_decimal(match.group(1))
        if value is not None and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def detect_currency(text: str, expected: str | None = None) -> tuple[str | None, str | None]:
    """Return (currency, problem). A lone ``$`` needs an expected currency."""
    tokens = _CURRENCY_TOKEN_RE.findall(text or "")
    codes: set[str] = set()
    bare_dollar = False
    for token in tokens:
        code = _CURRENCY_CODES.get(token.upper())
        if code is None:
            bare_dollar = True
        else:
            codes.add(code)
    if len(codes) > 1:
        return None, f"multiple currencies in price text: {sorted(codes)}"
    if codes:
        code = next(iter(codes))
        if bare_dollar and code != (expected or code):
            return None, "conflicting currency markers"
        return code, None
    if bare_dollar:
        if expected:
            return expected.upper(), None
        return None, "bare '$' without an expected currency"
    if expected:
        # A plain number on a page whose currency is known from structured data.
        return expected.upper(), None
    return None, "no currency marker found"


def parse_money(text: str, expected_currency: str | None = None) -> PriceParse:
    candidates = price_candidates(text)
    currency, currency_problem = detect_currency(text, expected_currency)

    problems: list[str] = []
    if currency_problem:
        problems.append(currency_problem)
    if not candidates:
        problems.append("no numeric price found")
    if len(candidates) > 1:
        problems.append(f"multiple price candidates: {[str(c) for c in candidates]}")

    if problems:
        return PriceParse(
            value=None,
            currency=currency,
            candidates=candidates,
            ambiguous=True,
            reason="; ".join(problems),
        )
    return PriceParse(
        value=candidates[0],
        currency=currency,
        candidates=candidates,
        ambiguous=False,
        reason=None,
    )


def discount_pct(price: Decimal | None, original: Decimal | None) -> Decimal | None:
    if price is None or original is None or original <= 0 or price > original:
        return None
    return ((original - price) / original * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def format_money(amount: Decimal | None, currency: str | None) -> str:
    if amount is None:
        return "n/a"
    symbol = _DISPLAY.get((currency or "").upper())
    prefix = symbol or (currency.upper() + " " if currency else "")
    return f"{prefix}{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def currencies_match(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.upper() == right.upper())
