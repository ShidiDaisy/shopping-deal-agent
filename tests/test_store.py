from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from helpers import SITE, load_fixture, make_fetch, make_watch_spec, make_settings
from shopping_deal_agent.clock import utcnow
from shopping_deal_agent.models import AlertEvent, Condition, VerificationStatus
from shopping_deal_agent.parse import ProductParser
from shopping_deal_agent.verify import verify


def _observation(watch, html=load_fixture("adidas_verified_deal.html")):
    return verify(
        make_fetch(html),
        ProductParser(SITE).parse(html),
        watch,
        make_settings(),
    )


def test_upsert_watch_is_idempotent(store):
    first = store.upsert_watch(make_watch_spec())
    second = store.upsert_watch(make_watch_spec(target_price=Decimal("40")))
    assert first == second
    assert len(store.list_watches()) == 1
    assert store.get_watch(first).target_price == Decimal("40")


def test_observation_roundtrip_preserves_relations(store, watch):
    observation = _observation(watch)
    observation_id = store.save_observation(observation)
    loaded = store.latest_observation(watch.id, verified_only=True)
    assert loaded.id == observation_id
    assert loaded.price == Decimal("42.00")
    assert loaded.original_price == Decimal("65.00")
    assert loaded.discount_pct == Decimal("35.38")
    assert loaded.sizes == {"XS": True, "S": True, "M": False}
    assert loaded.colors == ("Black",)
    assert any(promo.applicable for promo in loaded.promotions)


def test_verified_only_filter_is_pushed_to_sql(store, watch):
    unverified = replace(_observation(watch), status=VerificationStatus.UNVERIFIED)
    store.save_observation(unverified)
    assert store.latest_observation(watch.id, verified_only=True) is None
    assert store.latest_observation(watch.id, verified_only=False) is not None


def test_previous_sizes_excludes_current_observation(store, watch):
    out = _observation(watch, load_fixture("adidas_out_of_stock.html"))
    store.save_observation(out)
    current_id = store.save_observation(_observation(watch))
    previous = store.previous_sizes(watch.id, exclude_id=current_id)
    assert previous == {"XS": False, "S": False, "M": False}


def test_alert_dedupe(store, watch):
    event = AlertEvent(
        watch_id=watch.id,
        condition=Condition.PRICE_TARGET,
        message="price hit target",
        dedupe_key="fixed-key",
    )
    assert store.save_alert(event) is True
    assert store.save_alert(event) is False
    assert store.alert_exists("fixed-key") is True
    assert len(store.list_alerts()) == 1


def test_price_history_limit_and_order(store, watch):
    base = _observation(watch)
    for index, price in enumerate(("70", "60", "50")):
        store.save_observation(
            replace(
                base,
                price=Decimal(price),
                fetched_at=utcnow() + timedelta(seconds=index),
            )
        )
    rows = store.price_history(watch.id, limit=2)
    assert len(rows) == 2
    assert rows[0].price == Decimal("50")
    assert rows[1].price == Decimal("60")


def test_fetch_log(store, watch):
    store.log_fetch(watch.id, watch.url, 403, "http", "blocked", "access denied")
    with store._lock:
        row = store._conn.execute("SELECT outcome FROM fetch_log").fetchone()
    assert row["outcome"] == "blocked"


def test_last_fetch_at_is_none_before_any_check(store):
    assert store.last_fetch_at() is None


def test_last_fetch_at_reflects_the_most_recent_log_entry(store, watch):
    store.log_fetch(watch.id, watch.url, 200, "http", "ok", None)
    last = store.last_fetch_at()
    assert last is not None
    assert (utcnow() - last).total_seconds() < 5


def test_delete_watch_cascades_to_all_dependent_rows(store, watch):
    store.save_observation(_observation(watch))
    store.save_alert(
        AlertEvent(
            watch_id=watch.id,
            condition=Condition.PRICE_TARGET,
            message="price hit target",
            dedupe_key="remove-key",
        )
    )
    store.log_fetch(watch.id, watch.url, 200, "http", "ok", None)

    assert store.delete_watch(watch.id) is True
    assert store.list_watches() == []
    assert store.latest_observation(watch.id) is None
    assert store.list_alerts() == []
    assert store.delete_watch(watch.id) is False
    with store._lock:
        for table in (
            "observations",
            "observation_sizes",
            "observation_promotions",
            "alerts",
            "fetch_log",
        ):
            count = store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, table


def test_delete_watch_keeps_other_watches(store, watch):
    other = store.upsert_watch(
        make_watch_spec(url="https://www.aloyoga.com/products/another-item")
    )
    assert store.delete_watch(watch.id) is True
    assert [item.id for item in store.list_watches()] == [other]
