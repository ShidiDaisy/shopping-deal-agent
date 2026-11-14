"""Shared normalisation helpers for parsing strategies."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..models import Availability

_SIZE_CLEAN_RE = re.compile(r"^(?:size\s*:?\s*)", re.IGNORECASE)
_SOLD_OUT_RE = re.compile(r"\s*(?:sold\s*out|out\s*of\s*stock|unavailable)\s*$", re.IGNORECASE)
_SIZE_TOKEN_RE = re.compile(r"^[A-Z0-9]{1,3}(?:[.\-/][A-Z0-9]{1,3})?$")
_OUT_MARKERS = ("outofstock", "out of stock", "soldout", "sold out", "unavailable", "discontinued")
_IN_MARKERS = ("instock", "in stock", "available", "preorder", "instockforpurchase")


def normalize_size(label: str) -> str | None:
    text = _SOLD_OUT_RE.sub("", _SIZE_CLEAN_RE.sub("", (label or "").strip())).strip().upper()
    text = re.sub(r"\s+", " ", text)
    if not text or not _SIZE_TOKEN_RE.match(text):
        return None
    return text


def normalize_availability(value: object) -> Availability:
    text = str(value or "").strip().lower()
    if not text:
        return Availability.UNKNOWN
    if any(marker in text for marker in _OUT_MARKERS):
        return Availability.OUT_OF_STOCK
    if any(marker in text for marker in _IN_MARKERS):
        return Availability.IN_STOCK
    if text in {"true", "1", "yes"}:
        return Availability.IN_STOCK
    if text in {"false", "0", "no"}:
        return Availability.OUT_OF_STOCK
    return Availability.UNKNOWN


def to_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() and value >= 0 else None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = re.sub(r"[^\d.,]", "", str(value).strip())
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = f"{head}.{tail}" if len(tail) == 2 and head.isdigit() else text.replace(",", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def first_value(mapping: dict, keys: tuple[str, ...]) -> object | None:
    lowered = {str(k).casefold(): v for k, v in mapping.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if value not in (None, ""):
            return value
    return None
