"""Strategy chain with cross-strategy price conflict detection."""
from __future__ import annotations

from dataclasses import replace

from ..models import Availability, RawProduct
from . import css as css_module
from . import embedded as embedded_module
from . import jsonld as jsonld_module
from .dom import make_selector, visible_text
from .promotions import extract_promotions


# jsonld/embedded read explicit structured fields (schema.org Offer, OpenGraph
# meta tags, embedded product JSON); css is generic last-resort guessing
# (css.py's own docstring: "intentionally generic... lowest confidence, last
# resort"). Filling a gap in the structured-data price from another
# structured source is reasonable; filling it from a generic CSS guess is how
# an unrelated "was" price on the page turns into a fabricated discount.
_TRUSTED_ORIGINAL_PRICE_STRATEGIES = frozenset({"jsonld", "embedded"})


def _merge(primary: RawProduct, others: list[RawProduct]) -> RawProduct:
    title = primary.title
    currency = primary.currency
    original_price = primary.original_price
    availability = primary.availability
    sizes = dict(primary.sizes)
    colors = set(primary.colors)

    for other in others:
        title = title or other.title
        currency = currency or other.currency
        if original_price is None and other.strategy in _TRUSTED_ORIGINAL_PRICE_STRATEGIES:
            original_price = other.original_price
        if availability is Availability.UNKNOWN:
            availability = other.availability
        for size, in_stock in other.sizes.items():
            sizes.setdefault(size, in_stock)
        colors.update(other.colors)

    return replace(
        primary,
        title=title,
        currency=currency,
        original_price=original_price,
        availability=availability,
        sizes=sizes,
        colors=tuple(sorted(colors)),
    )


class ProductParser:
    """Runs JSON-LD -> embedded structured data -> CSS, then cross-checks."""

    def __init__(
        self,
        site: str,
        watch_sizes: tuple[str, ...] = (),
        watch_colors: tuple[str, ...] = (),
    ) -> None:
        self.site = site
        self.watch_sizes = watch_sizes
        self.watch_colors = watch_colors

    def parse(self, html: str) -> RawProduct:
        page = make_selector(html)
        notes: list[str] = []
        found: list[RawProduct] = []
        strategies = (
            ("jsonld", lambda: jsonld_module.parse_jsonld(page)),
            ("embedded", lambda: embedded_module.parse_embedded(page)),
            ("css", lambda: css_module.parse_css(page, self.site)),
        )
        for name, strategy in strategies:
            try:
                result = strategy()
            except Exception as exc:  # a broken strategy must not kill the run
                notes.append(f"{name} strategy error: {type(exc).__name__}: {exc}")
                continue
            if result is not None and result.price is not None:
                found.append(result)

        if not found:
            return RawProduct(
                strategy="none",
                confidence=0.0,
                notes=tuple([*notes, "no price could be extracted from this page"]),
            )

        primary = found[0]
        distinct_prices = tuple(
            sorted({result.price for result in found if result.price is not None})
        )
        if len(distinct_prices) > 1:
            notes.append(
                "conflicting prices across strategies: "
                + ", ".join(str(price) for price in distinct_prices)
            )
        skipped_originals = [
            f"{other.strategy}={other.original_price}"
            for other in found[1:]
            if primary.original_price is None
            and other.original_price is not None
            and other.strategy not in _TRUSTED_ORIGINAL_PRICE_STRATEGIES
        ]
        if skipped_originals and not any(
            other.strategy in _TRUSTED_ORIGINAL_PRICE_STRATEGIES and other.original_price is not None
            for other in found[1:]
        ):
            notes.append(
                "original price only available from a generic CSS match "
                f"({', '.join(skipped_originals)}); not used, no discount computed"
            )
        merged = _merge(primary, found[1:])
        promotions = extract_promotions(visible_text(page))
        return replace(
            merged,
            promotions=promotions,
            price_candidates=distinct_prices,
            notes=tuple([*notes, *primary.notes, *merged.notes]),
            evidence={
                **dict(merged.evidence),
                "strategies_with_price": [result.strategy for result in found],
            },
        )
