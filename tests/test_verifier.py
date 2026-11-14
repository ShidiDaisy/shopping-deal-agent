from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from helpers import SITE, load_fixture, make_fetch, make_settings
from shopping_deal_agent.clock import utcnow
from shopping_deal_agent.models import RawProduct, SourceKind, VerificationStatus
from shopping_deal_agent.parse import ProductParser
from shopping_deal_agent.verify import verify

VERIFIED_HTML = load_fixture("adidas_verified_deal.html")


def _verify(html, watch, settings=None, **fetch_overrides):
    result = make_fetch(html, **fetch_overrides)
    raw = ProductParser(SITE).parse(html)
    return verify(result, raw, watch, settings or make_settings())


def test_verified_deal_is_alertable(watch):
    observation = _verify(VERIFIED_HTML, watch)
    assert observation.status is VerificationStatus.VERIFIED
    assert observation.is_fresh is True
    assert observation.can_alert is True
    assert observation.price == Decimal("42.00")
    assert observation.discount_pct == Decimal("35.38")
    assert observation.source_kind is SourceKind.OFFICIAL_API


def test_http_403_is_blocked(watch):
    observation = _verify(load_fixture("adidas_blocked.html"), watch, status_code=403)
    assert observation.status is VerificationStatus.BLOCKED
    assert observation.can_alert is False
    assert any("403" in note for note in observation.notes)


def test_access_barrier_in_body_is_blocked(watch):
    observation = _verify(load_fixture("adidas_blocked.html"), watch)
    assert observation.status is VerificationStatus.BLOCKED
    assert observation.can_alert is False


def test_cached_response_is_unverified(watch):
    observation = _verify(VERIFIED_HTML, watch, from_cache=True)
    assert observation.status is VerificationStatus.UNVERIFIED
    assert observation.can_alert is False


def test_search_results_and_third_party_are_unverified(watch):
    for kind in (SourceKind.SEARCH_RESULT, SourceKind.CACHE, SourceKind.THIRD_PARTY):
        observation = _verify(VERIFIED_HTML, watch, source_kind=kind)
        assert observation.status is VerificationStatus.UNVERIFIED
        assert observation.can_alert is False


def test_stale_page_is_unverified(watch):
    observation = _verify(
        VERIFIED_HTML, watch, fetched_at=utcnow() - timedelta(seconds=3600)
    )
    assert observation.status is VerificationStatus.UNVERIFIED
    assert observation.is_fresh is False


def test_conflicting_prices_are_unverified(watch):
    observation = _verify(load_fixture("adidas_ambiguous.html"), watch)
    assert observation.status is VerificationStatus.UNVERIFIED
    assert any("conflicting price candidates" in note for note in observation.notes)


def test_currency_mismatch_is_unverified(watch):
    observation = _verify(VERIFIED_HTML, replace(watch, target_currency="USD"))
    assert observation.status is VerificationStatus.UNVERIFIED


def test_fixture_data_requires_explicit_opt_in(watch):
    observation = _verify(
        VERIFIED_HTML, watch, settings=make_settings(allow_fixture_alerts=False)
    )
    assert observation.status is VerificationStatus.UNVERIFIED


def test_unavailable_product_is_unverified(watch):
    observation = _verify(load_fixture("adidas_out_of_stock.html"), watch)
    assert observation.status is VerificationStatus.UNVERIFIED
    assert observation.sizes["XS"] is False


def test_low_confidence_is_unverified(watch):
    low = RawProduct(
        price=Decimal("42"), currency="SGD", confidence=0.2, strategy="guess"
    )
    observation = verify(make_fetch("<p>x</p>"), low, watch, make_settings())
    assert observation.status is VerificationStatus.UNVERIFIED


def test_fetch_error_is_error_status(watch):
    observation = verify(
        make_fetch("", status_code=None, error="connection reset"),
        RawProduct(price=Decimal("42"), currency="SGD", confidence=0.9),
        watch,
        make_settings(),
    )
    assert observation.status is VerificationStatus.ERROR
    assert observation.can_alert is False
