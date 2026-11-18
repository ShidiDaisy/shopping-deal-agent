"""Alert conditions. ``Observation.can_alert`` is the only gate."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

from .clock import utcnow
from .models import AlertEvent, Condition, Observation, WatchItem
from .money import format_money
from .parse.common import normalize_size


@dataclass(frozen=True)
class RuleContext:
    watch: WatchItem
    observation: Observation
    observation_id: int | None
    previous_price: Decimal | None = None
    previous_sizes: Mapping[str, bool] | None = None


def _dedupe_key(watch_id: int, condition: Condition, state: str) -> str:
    digest = hashlib.sha256(f"{watch_id}|{condition.value}|{state}".encode("utf-8"))
    return digest.hexdigest()[:32]


def _event(
    ctx: RuleContext,
    condition: Condition,
    message: str,
    state: str,
) -> AlertEvent:
    return AlertEvent(
        watch_id=ctx.watch.id or 0,
        condition=condition,
        message=message,
        dedupe_key=_dedupe_key(ctx.watch.id or 0, condition, state),
        observation_id=ctx.observation_id,
        created_at=utcnow(),
    )


def _color_matches(watch: WatchItem, observation: Observation) -> bool:
    if not watch.colors:
        return True
    if not observation.colors:
        # Colour is unknown, not disqualified; verification only concerns price.
        return True
    wanted = {colour.casefold() for colour in watch.colors}
    available = {colour.casefold() for colour in observation.colors}
    return bool(wanted & available)


def evaluate(ctx: RuleContext) -> list[AlertEvent]:
    observation = ctx.observation
    watch = ctx.watch
    if not observation.can_alert:
        return []
    if not _color_matches(watch, observation):
        return []

    price = observation.price
    assert price is not None  # guaranteed by can_alert
    currency = observation.currency
    title = observation.title or "Product"
    # Nothing here tracks shipping fees or voucher/cart-minimum requirements,
    # so a quoted price is the item price only; say so explicitly rather than
    # let the alert imply it's the total landed cost.
    suffix = (
        "\nNote: price excludes shipping and voucher codes unless stated above."
        f"\n{observation.url}"
    )
    pretty = format_money(price, currency)
    events: list[AlertEvent] = []

    if watch.target_price is not None and price <= watch.target_price:
        events.append(
            _event(
                ctx,
                Condition.PRICE_TARGET,
                f"Price alert: {title} is {pretty} "
                f"(target {format_money(watch.target_price, watch.target_currency)})."
                + suffix,
                f"price={price}",
            )
        )

    if (
        observation.discount_pct is not None
        and observation.discount_pct >= watch.min_discount_pct
    ):
        events.append(
            _event(
                ctx,
                Condition.DISCOUNT,
                f"Discount alert: {title} is {observation.discount_pct}% off "
                f"({format_money(observation.original_price, currency)} -> {pretty})."
                + suffix,
                f"price={price}|original={observation.original_price}",
            )
        )

    applicable = [promo for promo in observation.promotions if promo.applicable]
    if applicable:
        descriptions = ", ".join(promo.description for promo in applicable)
        events.append(
            _event(
                ctx,
                Condition.PROMOTION,
                f"Promotion alert: {title} - {descriptions}. Current price {pretty}."
                + suffix,
                "|".join(sorted(promo.fingerprint for promo in applicable)),
            )
        )

    previous_sizes = ctx.previous_sizes or {}
    for requested in watch.sizes:
        size = normalize_size(requested) or requested.upper()
        now_available = observation.sizes.get(size, False)
        was_available = previous_sizes.get(size)
        if now_available and was_available is False:
            events.append(
                _event(
                    ctx,
                    Condition.RESTOCK,
                    f"Restock alert: {title} size {size} is back in stock at {pretty}."
                    + suffix,
                    f"size={size}|price={price}|sizes={sorted(observation.sizes.items())}",
                )
            )

    previous_price = ctx.previous_price
    if previous_price is not None and price < previous_price:
        drop = previous_price - price
        drop_pct = (drop / previous_price * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if drop_pct >= watch.min_drop_pct and drop >= watch.min_drop_abs:
            events.append(
                _event(
                    ctx,
                    Condition.PRICE_DROP,
                    f"Price drop: {title} fell {format_money(drop, currency)} "
                    f"({drop_pct}%) from {format_money(previous_price, currency)} "
                    f"to {pretty}." + suffix,
                    f"from={previous_price}|to={price}",
                )
            )

    return events
