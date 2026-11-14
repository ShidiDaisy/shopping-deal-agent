import pytest

from helpers import make_settings, make_watch_spec
from shopping_deal_agent.store import Store


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def store():
    instance = Store(":memory:")
    instance.initialize()
    yield instance
    instance.close()


@pytest.fixture
def watch(store):
    watch_id = store.upsert_watch(make_watch_spec())
    return store.get_watch(watch_id)
