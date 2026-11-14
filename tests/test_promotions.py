from helpers import load_fixture
from shopping_deal_agent.parse.dom import make_selector, visible_text
from shopping_deal_agent.parse.promotions import extract_promotions


def test_unrestricted_promotion_is_applicable():
    promotions = extract_promotions(
        visible_text(make_selector(load_fixture("adidas_verified_deal.html")))
    )
    assert promotions
    assert any(promo.applicable for promo in promotions)
    assert any(promo.kind == "percentage_off" for promo in promotions)


def test_excluded_promotion_is_not_applicable():
    promotions = extract_promotions(
        "Extra 20% off with code SAVE20. Members only, exclusions apply."
    )
    assert promotions
    assert all(not promo.applicable for promo in promotions)


def test_unstated_scope_is_not_applicable():
    promotions = extract_promotions("Use code SAVE20 for an extra 20% off today.")
    assert promotions
    assert all(not promo.applicable for promo in promotions)


def test_no_promotions_on_plain_text():
    assert extract_promotions("Add to bag. Free returns within 30 days.") == ()
