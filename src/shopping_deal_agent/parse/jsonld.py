"""Strategy 1: schema.org Product / Offer JSON-LD (highest confidence)."""
from __future__ import annotations

import json
from typing import Any, Iterator

from scrapling.parser import Selector

from ..models import Availability, RawProduct
from .common import first_value, normalize_availability, normalize_size, to_decimal
from .dom import css_texts

_PRICE_KEYS = ("price", "currentprice", "saleprice", "pricevalue", "sellingprice")
_ORIGINAL_KEYS = (
    "originalprice", "compareatprice", "listprice", "wasprice", "msrp",
    "strikethroughprice", "regularprice", "highprice",
)
_SIZE_KEYS = ("size", "sizename", "sizelabel", "variantsize", "displaysize", "value")
_COLOR_KEYS = ("color", "colour", "colorway", "variantcolor")


def _iter_nodes(payload: Any) -> Iterator[dict]:
    if isinstance(payload, dict):
        yield payload
        for key, value in payload.items():
            if key == "@graph" or isinstance(value, (dict, list)):
                yield from _iter_nodes(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_nodes(item)


def _types(node: dict) -> set[str]:
    raw = node.get("@type") or node.get("type")
    if isinstance(raw, str):
        return {raw.casefold()}
    if isinstance(raw, list):
        return {str(item).casefold() for item in raw}
    return set()


def _offer_list(node: dict) -> list[dict]:
    offers = node.get("offers")
    if isinstance(offers, dict):
        return [offers]
    if isinstance(offers, list):
        return [item for item in offers if isinstance(item, dict)]
    return []


def _price_from(offer: dict, keys: tuple[str, ...], *, for_original: bool) -> Any:
    for key in keys:
        value = first_value(offer, (key,))
        number = to_decimal(value)
        if number is not None:
            return number
    specification = offer.get("priceSpecification")
    specs = specification if isinstance(specification, list) else [specification]
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        price_type = str(first_value(spec, ("priceType", "pricetype")) or "").casefold()
        is_list_price = any(word in price_type for word in ("list", "msrp", "original", "regular"))
        if is_list_price == for_original:
            number = to_decimal(first_value(spec, ("price", "value")))
            if number is not None:
                return number
    return None


def _variants(node: dict, offers: list[dict]) -> tuple[dict[str, bool], tuple[str, ...]]:
    candidates: list[Any] = list(offers)
    for key in ("hasVariant", "variants", "productVariants"):
        value = node.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.append(value)

    sizes: dict[str, bool] = {}
    colors: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        label = first_value(candidate, _SIZE_KEYS)
        size = normalize_size(str(label or ""))
        if size:
            status = normalize_availability(
                first_value(candidate, ("availability", "itemCondition", "inStock", "available"))
            )
            raw_in_stock = first_value(candidate, ("inStock", "available"))
            if isinstance(raw_in_stock, bool):
                in_stock = raw_in_stock
            else:
                in_stock = status is Availability.IN_STOCK
            sizes[size] = sizes.get(size, False) or in_stock
        colour = first_value(candidate, _COLOR_KEYS)
        if colour:
            colors.add(str(colour).strip())
    return sizes, tuple(sorted(colors))


def parse_jsonld(page: Selector) -> RawProduct | None:
    for blob in css_texts(page, 'script[type="application/ld+json"]'):
        try:
            payload = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _iter_nodes(payload):
            types = _types(node)
            if not any("product" in item for item in types):
                continue
            offers = _offer_list(node)
            source = offers[0] if offers else node
            price = _price_from(source, _PRICE_KEYS, for_original=False)
            original = _price_from(source, _ORIGINAL_KEYS, for_original=True)
            currency = first_value(source, ("priceCurrency", "currency", "currencyCode"))
            availability = normalize_availability(
                first_value(source, ("availability", "itemCondition", "stockStatus"))
            )
            sizes, colors = _variants(node, offers)
            notes: list[str] = []
            if currency is None:
                notes.append("json-ld did not declare a price currency")
            if price is None:
                notes.append("json-ld Product has no readable price")
            return RawProduct(
                title=str(first_value(node, ("name", "title")) or "") or None,
                price=price,
                original_price=original,
                currency=str(currency).upper() if currency else None,
                availability=availability,
                sizes=sizes,
                colors=colors,
                strategy="jsonld",
                confidence=0.95,
                price_candidates=(price,) if price is not None else (),
                notes=tuple(notes),
                evidence={
                    "jsonld_types": sorted(types),
                    "offer_price": str(price) if price is not None else None,
                    "price_currency": str(currency) if currency else None,
                },
            )
    return None
