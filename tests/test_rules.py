from dataclasses import replace
from decimal import Decimal

from helpers import SITE, load_fixture, make_fetch, make_settings
from shopping_deal_agent.models import Condition, VerificationStatus
from shopping_deal_agent.parse import ProductParser
from shopping_deal_agent.rules import RuleContext, evaluate
from shopping_deal_agent.verify import verify

VERIFIED_HTML = load_fixture("adidas_verified_deal.html")


def _observation(watch, html=VERIFIED_HTML):
    raw = ProductParser(SITE).parse(html)
    return verify(make_fetch(html), raw, watch, make_settings())


def test_price_discount_and_promotion_fire(watch):
    observation = _observation(watch)
    events = evaluate(
        RuleContext(watch=watch, observation=observation, observation_id=1)
    )
    assert {event.condition for event in events} == {
        Condition.PRICE_TARGET,
        Condition.DISCOUNT,
        Condition.PROMOTION,
    }


def test_unverified_observation_produces_no_events(watch):
    observation = replace(
        _observation(watch),
        status=VerificationStatus.UNVERIFIED,
        is_fresh=False,
    )
    assert evaluate(RuleContext(watch=watch, observation=observation, observation_id=1)) == []


def test_restock_requires_a_known_previous_out_of_stock(watch):
    observation = _observation(watch)
    context = RuleContext(
        watch=watch,
        observation=observation,
        observation_id=2,
        previous_sizes={"XS": False, "S": False},
    )
    restocks = [e for e in evaluate(context) if e.condition is Condition.RESTOCK]
    assert len(restocks) == 2
    assert len({event.dedupe_key for event in restocks}) == 2


def test_first_observation_never_reports_restock(watch):
    observation = _observation(watch)
    events = evaluate(
        RuleContext(watch=watch, observation=observation, observation_id=1, previous_sizes={})
    )
    assert Condition.RESTOCK not in {event.condition for event in events}


def test_meaningful_price_drop(watch):
    observation = _observation(watch)
    events = evaluate(
        RuleContext(
            watch=watch,
            observation=observation,
            observation_id=2,
            previous_price=Decimal("60.00"),
        )
    )
    assert Condition.PRICE_DROP in {event.condition for event in events}


def test_tiny_price_drop_is_ignored(watch):
    observation = _observation(watch)
    events = evaluate(
        RuleContext(
            watch=watch,
            observation=observation,
            observation_id=2,
            previous_price=Decimal("42.50"),
        )
    )
    assert Condition.PRICE_DROP not in {event.condition for event in events}


def test_colour_preference_filters(watch):
    observation = replace(_observation(watch), colors=("Black",))
    strict = replace(watch, colors=("Red",))
    assert evaluate(
        RuleContext(watch=strict, observation=observation, observation_id=1)
    ) == []


def test_dedupe_keys_are_stable(watch):
    observation = _observation(watch)
    context = RuleContext(watch=watch, observation=observation, observation_id=1)
    assert [e.dedupe_key for e in evaluate(context)] == [
        e.dedupe_key for e in evaluate(context)
    ]
