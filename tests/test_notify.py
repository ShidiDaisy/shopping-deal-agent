from dataclasses import replace
from types import SimpleNamespace

from helpers import URL, make_settings
from shopping_deal_agent.models import AlertEvent, Condition
from shopping_deal_agent.notify import AlertDispatcher, EmailNotifier, TelegramNotifier


def _event(dedupe_key="key-1"):
    return AlertEvent(
        watch_id=1,
        condition=Condition.PRICE_TARGET,
        message="Price alert: shorts are S$42.00",
        dedupe_key=dedupe_key,
    )


class FakeNotifier:
    name = "fake"

    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.calls = []

    def send(self, event):
        self.calls.append(event)
        return self.succeeds


def test_dry_run_never_calls_channels(store, settings, watch):
    notifier = FakeNotifier()
    dispatcher = AlertDispatcher(settings, store, [notifier])
    result = dispatcher.dispatch([_event()])
    assert len(result) == 1
    assert result[0].delivered is False
    assert result[0].channel == "dry-run"
    assert notifier.calls == []
    assert len(store.list_alerts()) == 1


def test_duplicate_alert_is_suppressed(store, settings, watch):
    dispatcher = AlertDispatcher(settings, store, [FakeNotifier()])
    assert len(dispatcher.dispatch([_event()])) == 1
    assert dispatcher.dispatch([_event()]) == []
    assert len(store.list_alerts()) == 1


def test_cooldown_suppresses_new_state(store, watch):
    settings = make_settings(dry_run=True, alert_cooldown_minutes=60)
    dispatcher = AlertDispatcher(settings, store, [FakeNotifier()])
    dispatcher.dispatch([_event("key-a")])
    assert dispatcher.dispatch([_event("key-b")]) == []


def test_real_delivery_marks_delivered(store, watch):
    settings = make_settings(dry_run=False)
    notifier = FakeNotifier()
    dispatcher = AlertDispatcher(settings, store, [notifier])
    result = dispatcher.dispatch([_event()])
    assert result[0].delivered is True
    assert result[0].channel == "fake"
    assert len(notifier.calls) == 1


def test_failed_delivery_is_recorded(store, watch):
    settings = make_settings(dry_run=False)
    dispatcher = AlertDispatcher(settings, store, [FakeNotifier(succeeds=False)])
    result = dispatcher.dispatch([_event()])
    assert result[0].delivered is False
    assert result[0].channel == "failed"


def test_telegram_notifier():
    settings = make_settings(telegram_bot_token="token", telegram_chat_id="chat")
    captured = {}

    def poster(url, json, timeout):
        captured.update(url=url, payload=json, timeout=timeout)
        return SimpleNamespace(is_success=True)

    notifier = TelegramNotifier(settings, poster=poster)
    assert notifier.send(_event()) is True
    assert captured["payload"]["chat_id"] == "chat"
    assert "parse_mode" not in captured["payload"]


def test_telegram_notifier_disabled_without_credentials():
    notifier = TelegramNotifier(
        make_settings(), poster=lambda *a, **k: SimpleNamespace(is_success=True)
    )
    assert notifier.send(_event()) is False


def test_email_notifier():
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            return None

        def login(self, user, password):
            return None

        def send_message(self, message):
            sent.append(message)

    settings = make_settings(
        smtp_host="smtp.example.test",
        email_from="alerts@example.test",
        email_to="me@example.test",
    )
    notifier = EmailNotifier(settings, smtp_factory=FakeSMTP)
    assert notifier.send(_event()) is True
    assert sent[0]["Subject"] == "[Deal Agent] price_le_target"
