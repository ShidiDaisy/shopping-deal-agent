"""End-to-end runs. All traffic is local fixtures; no network is touched."""
from helpers import URL, SequenceFetcher, load_fixture, make_fetch
from shopping_deal_agent.agent import DealAgent
from shopping_deal_agent.fetch.fixture_fetcher import FixtureFetcher
from shopping_deal_agent.models import Condition, SourceKind, VerificationStatus
from shopping_deal_agent.notify import AlertDispatcher

VERIFIED_HTML = load_fixture("adidas_verified_deal.html")


def _agent(store, settings, fetcher):
    return DealAgent(settings, store, fetcher, AlertDispatcher(settings, store))


def test_verified_deal_produces_dry_run_alerts(store, settings, watch):
    fetcher = FixtureFetcher({URL: VERIFIED_HTML})
    outcomes = _agent(store, settings, fetcher).check_all()
    assert len(outcomes) == 1
    observation = outcomes[0].observation
    assert observation.status is VerificationStatus.VERIFIED
    assert observation.can_alert is True
    assert {alert.condition for alert in outcomes[0].alerts} == {
        Condition.PRICE_TARGET,
        Condition.DISCOUNT,
        Condition.PROMOTION,
    }
    assert all(not alert.delivered for alert in outcomes[0].alerts)
    assert len(store.list_alerts()) == 3


def test_blocked_page_produces_no_alert(store, settings, watch):
    fetcher = FixtureFetcher(
        {URL: load_fixture("adidas_blocked.html")}, status_code=403
    )
    outcomes = _agent(store, settings, fetcher).check_all()
    assert outcomes[0].observation.status is VerificationStatus.BLOCKED
    assert outcomes[0].alerts == []
    assert store.list_alerts() == []
    assert len(store.price_history(watch.id)) == 0


def test_cached_page_never_substantiates_a_discount(store, settings, watch):
    fetcher = SequenceFetcher(
        [make_fetch(VERIFIED_HTML, from_cache=True, source_kind=SourceKind.PRODUCT_PAGE)]
    )
    outcomes = _agent(store, settings, fetcher).check_all()
    assert outcomes[0].observation.status is VerificationStatus.UNVERIFIED
    assert outcomes[0].alerts == []


def test_cached_search_result_never_substantiates_a_discount(store, settings, watch):
    fetcher = SequenceFetcher(
        [make_fetch(VERIFIED_HTML, source_kind=SourceKind.SEARCH_RESULT)]
    )
    outcomes = _agent(store, settings, fetcher).check_all()
    assert outcomes[0].observation.status is VerificationStatus.UNVERIFIED
    assert outcomes[0].alerts == []


def test_repeated_identical_state_does_not_spam(store, settings, watch):
    agent = _agent(store, settings, FixtureFetcher({URL: VERIFIED_HTML}))
    agent.check_all()
    second = agent.check_all()
    assert second[0].alerts == []
    assert len(store.list_alerts()) == 3


def test_restock_detected_after_recorded_stockout(store, settings, watch):
    fetcher = SequenceFetcher(
        [
            make_fetch(load_fixture("adidas_out_of_stock.html")),
            make_fetch(VERIFIED_HTML),
        ]
    )
    agent = _agent(store, settings, fetcher)
    first = agent.check_all()
    assert first[0].observation.status is VerificationStatus.UNVERIFIED
    assert first[0].alerts == []

    second = agent.check_all()
    assert Condition.RESTOCK in {alert.condition for alert in second[0].alerts}


def test_price_drop_compares_against_verified_history(store, settings, watch):
    expensive = VERIFIED_HTML.replace("42.00", "60.00")
    fetcher = SequenceFetcher(
        [make_fetch(expensive), make_fetch(VERIFIED_HTML)]
    )
    agent = _agent(store, settings, fetcher)
    agent.check_all()
    second = agent.check_all()
    assert Condition.PRICE_DROP in {alert.condition for alert in second[0].alerts}
