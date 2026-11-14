"""Strategy 2: OpenGraph / microdata meta tags and embedded product JSON."""
from __future__ import annotations

import json
from typing import Any

from scrapling.parser import Selector

from ..models import Availability, RawProduct
from .common import first_value, normalize_availability, normalize_size, to_decimal
from .dom import css_attrs, css_texts, first_attr, first_text

_TITLE_SELECTORS = (
    'meta[property="og:title"]', 'meta[name="twitter:title"]',
    'meta[property="product:title"]',
)
_PRICE_SELECTORS = (
    'meta[property="product:price:amount"]', 'meta[property="og:price:amount"]',
    'meta[itemprop="price"]', 'meta[name="price"]',
)
_CURRENCY_SELECTORS = (
    'meta[property="product:price:currency"]', 'meta[property="og:price:currency"]',
    'meta[itemprop="priceCurrency"]',
)
_ORIGINAL_SELECTORS = (
    'meta[property="product:original_price:amount"]',
    'meta[property="product:list_price:amount"]',
    'meta[name="original_price"]',
)
_AVAILABILITY_SELECTORS = (
    'meta[property="product:availability"]', 'link[itemprop="availability"]',
)
_JSON_SCRIPT_SELECTORS = (
    'script#__NEXT_DATA__', 'script#__NUXT_DATA__',
    'script[type="application/json"]',
)
_PRICE_KEYS = ("price", "currentprice", "saleprice", "pricevalue", "sellingprice", "priceamount")
_ORIGINAL_KEYS = ("originalprice", "compareatprice", "listprice", "wasprice", "msrp", "regularprice")
_NAME_KEYS = ("name", "title", "productname", "displayname")
_CURRENCY_KEYS = ("currency", "currencycode", "pricecurrency", "pricecurrencycode")
_SIZE_CONTAINER_KEYS = ("variants", "sizes", "sizeoptions", "sizevariants", "sizechart")


def _find_product_dict(payload: Any, max_nodes: int = 20000) -> dict | None:
    stack: list[Any] = [payload]
    visited = 0
    while stack:
        item = stack.pop()
        visited += 1
        if visited > max_nodes:
            return None
        if isinstance(item, dict):
            keys = {str(key).casefold() for key in item}
            if keys & set(_PRICE_KEYS) and keys & set(_NAME_KEYS):
                return item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def _sizes_from(value: Any) -> dict[str, bool]:
    sequence = value
    if isinstance(sequence, dict):
        for key in _SIZE_CONTAINER_KEYS:
            nested = first_value(sequence, (key,))
            if isinstance(nested, list):
                sequence = nested
                break
        else:
            sequence = list(sequence.values())
    if not isinstance(sequence, list):
        return {}
    result: dict[str, bool] = {}
    for entry in sequence:
        if isinstance(entry, str):
            label, in_stock = entry, True
        elif isinstance(entry, dict):
            label = str(first_value(entry, ("size", "label", "name", "value", "sizeLabel")) or "")
            raw = first_value(entry, ("available", "inStock", "isAvailable", "stock", "inventory"))
            if isinstance(raw, bool):
                in_stock = raw
            elif isinstance(raw, (int, float)):
                in_stock = raw > 0
            else:
                in_stock = normalize_availability(raw) is not Availability.OUT_OF_STOCK
        else:
            continue
        size = normalize_size(label)
        if size:
            result[size] = result.get(size, False) or in_stock
    return result


def parse_embedded(page: Selector) -> RawProduct | None:
    title = first_attr(page, _TITLE_SELECTORS, "content") or first_text(page, ("h1",))
    price_text = first_attr(page, _PRICE_SELECTORS, "content")
    original_text = first_attr(page, _ORIGINAL_SELECTORS, "content")
    currency = first_attr(page, _CURRENCY_SELECTORS, "content")
    availability_raw = first_attr(page, _AVAILABILITY_SELECTORS, "href") or first_attr(
        page, _AVAILABILITY_SELECTORS, "content"
    )

    price = to_decimal(price_text)
    original = to_decimal(original_text)
    sizes: dict[str, bool] = {}
    notes: list[str] = []
    embedded_product: dict | None = None

    for selector in _JSON_SCRIPT_SELECTORS:
        for blob in css_texts(page, selector):
            if len(blob) > 2_000_000:
                continue
            try:
                payload = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                continue
            candidate = _find_product_dict(payload)
            if candidate is None:
                continue
            embedded_product = candidate
            price = price if price is not None else to_decimal(first_value(candidate, _PRICE_KEYS))
            original = original if original is not None else to_decimal(
                first_value(candidate, _ORIGINAL_KEYS)
            )
            currency = currency or (
                str(first_value(candidate, _CURRENCY_KEYS) or "").upper() or None
            )
            title = title or str(first_value(candidate, _NAME_KEYS) or "") or None
            for key in _SIZE_CONTAINER_KEYS:
                nested = first_value(candidate, (key,))
                if nested is not None:
                    sizes.update(_sizes_from(nested))
            break
        if embedded_product is not None:
            break

    if price is None:
        for selector in ('[itemprop="price"]', '[data-price]', '[data-product-price]'):
            for raw in css_attrs(page, selector, "content") + css_texts(page, selector):
                price = to_decimal(raw)
                if price is not None:
                    break
            if price is not None:
                break

    if price is None:
        return None
    if currency is None:
        notes.append("embedded data did not declare a currency")
    return RawProduct(
        title=title,
        price=price,
        original_price=original,
        currency=currency,
        availability=normalize_availability(availability_raw),
        sizes=sizes,
        strategy="embedded",
        confidence=0.80,
        price_candidates=(price,),
        notes=tuple(notes),
        evidence={
            "meta_price": price_text,
            "meta_currency": currency,
            "embedded_json_used": embedded_product is not None,
        },
    )
