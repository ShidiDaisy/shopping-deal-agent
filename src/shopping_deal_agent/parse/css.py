"""Strategy 3: site-adapter CSS selectors (lowest confidence, last resort).

Selectors are intentionally generic. Prefer keeping JSON-LD / official
structured data healthy; only calibrate these per site when a site genuinely
does not expose machine-readable product data.
"""
from __future__ import annotations

from scrapling.parser import Selector

from ..money import parse_money
from ..models import Availability, RawProduct
from .common import normalize_size
from .dom import first_attr, first_text, size_elements

DEFAULT_SELECTORS: dict[str, tuple[str, ...]] = {
    "title": (
        "h1[data-testid='product-title']", "h1.product-title", "h1#product-title",
        "h1[itemprop='name']", "h1",
    ),
    "price": (
        "[data-testid='price']", "[data-testid='current-price']", ".price--sale",
        ".sale-price", "[class*='salePrice']", "[itemprop='price']", ".price",
    ),
    "original": (
        "[data-testid='original-price']", ".price--original", ".was-price",
        "[class*='originalPrice']", "s.price", "del",
    ),
    "sizes": (
        "button[data-testid*='size']", "[data-testid='size-selector'] button",
        "button[data-size]", ".size-option", "[class*='size-tile']", "li[data-size]",
    ),
    "size_out_markers": (
        "disabled", "out-of-stock", "outofstock", "sold-out", "soldout",
        "unavailable", "is-disabled", "oos",
    ),
}

SITE_SELECTORS: dict[str, dict[str, tuple[str, ...]]] = {
    "adidas.com.sg": {
        "title": ("h1[data-testid='product-title']", "h1.product-name", "h1"),
        "price": ("[data-testid='price']", ".price-value", ".product-price", "[class*='price__value']"),
        "original": ("[data-testid='original-price']", ".price-original", "s", "del"),
        "sizes": ("button[data-testid*='size']", "[data-testid='size-selector'] button", ".size-selector button"),
    },
    "macys.com": {
        "title": ("h1[data-testid='product-title']", "h1#product-title", "h1"),
        "price": ("[data-testid='price']", ".price-sale", ".price-regular"),
        "original": ("[data-testid='original-price']", ".price-original", "del"),
        "sizes": ("button[data-testid*='size']", ".size-option", "button[aria-label*='Size']"),
    },
    "ralphlauren.com": {
        "title": ("h1[data-testid='product-title']", "h1.product-name", "h1"),
        "price": ("[data-testid='price']", ".product-price", "[class*='sale-price']"),
        "original": ("[data-testid='original-price']", "[class*='original-price']", "s"),
        "sizes": ("button[data-testid*='size']", ".size-selector button", ".size-option"),
    },
    "aloyoga.com": {
        "title": ("h1[data-testid='product-title']", "h1.product-title", "h1"),
        "price": ("[data-testid='price']", ".product-price", "[itemprop='price']"),
        "original": ("[data-testid='compare-price']", ".compare-price", "s"),
        "sizes": ("button[data-testid*='size']", "[data-size]", ".size-option"),
    },
    "lululemon.com": {
        "title": ("h1[data-testid='product-title']", "h1.product-title", "h1"),
        "price": ("[data-testid='price']", ".price", "[class*='price']"),
        "original": ("[data-testid='original-price']", ".price--original", "s"),
        "sizes": ("button[data-testid*='size']", "[data-size]", ".size-option"),
    },
}

_CURRENCY_META_SELECTORS = (
    'meta[property="og:price:currency"]',
    'meta[property="product:price:currency"]',
    'meta[itemprop="priceCurrency"]',
)


def selectors_for(site: str) -> dict[str, tuple[str, ...]]:
    host = (site or "").lower()
    override = next(
        (value for domain, value in SITE_SELECTORS.items() if host.endswith(domain)),
        None,
    )
    if override is None:
        return dict(DEFAULT_SELECTORS)
    merged: dict[str, tuple[str, ...]] = {}
    for key, default in DEFAULT_SELECTORS.items():
        merged[key] = tuple(override.get(key, ())) + tuple(default)
    return merged


def parse_css(page: Selector, site: str) -> RawProduct | None:
    config = selectors_for(site)
    title = first_text(page, config["title"])
    price_text = first_text(page, config["price"])
    original_text = first_text(page, config["original"])
    currency_meta = first_attr(page, _CURRENCY_META_SELECTORS, "content")

    parsed = parse_money(price_text or "", expected_currency=currency_meta)
    price = parsed.value
    currency = parsed.currency or (currency_meta.upper() if currency_meta else None)
    original = None
    if original_text:
        original = parse_money(original_text, expected_currency=currency).value

    sizes: dict[str, bool] = {}
    for label, attributes in size_elements(page, config["sizes"]):
        size = normalize_size(label)
        if not size:
            continue
        out_of_stock = (
            any(marker in attributes for marker in config["size_out_markers"])
            or "sold out" in label.casefold()
        )
        sizes[size] = sizes.get(size, False) or not out_of_stock

    if sizes:
        availability = (
            Availability.IN_STOCK if any(sizes.values()) else Availability.OUT_OF_STOCK
        )
    else:
        availability = Availability.UNKNOWN

    notes = []
    if parsed.ambiguous and parsed.reason:
        notes.append(f"css price ambiguous: {parsed.reason}")
    if price is None:
        return None
    return RawProduct(
        title=title,
        price=price,
        original_price=original,
        currency=currency,
        availability=availability,
        sizes=sizes,
        strategy="css",
        confidence=0.60,
        price_candidates=parsed.candidates,
        notes=tuple(notes),
        evidence={
            "price_text": price_text,
            "original_text": original_text,
            "site": site,
        },
    )
