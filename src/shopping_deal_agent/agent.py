"""Orchestration: fetch -> parse -> verify -> persist -> evaluate -> dispatch."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Settings
from .fetch.protocol import PageFetcher
from .models import AlertEvent, Observation, RawProduct, WatchItem
from .notify import AlertDispatcher
from .parse import ProductParser
from .rules import RuleContext, evaluate
from .store import Store
from .verify import verify

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckOutcome:
    watch_id: int
    url: str
    observation: Observation
    alerts: list[AlertEvent]


class DealAgent:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        fetcher: PageFetcher,
        dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.fetcher = fetcher
        self.dispatcher = dispatcher or AlertDispatcher(settings, store)

    def check_watch(self, watch: WatchItem) -> CheckOutcome:
        result = self.fetcher.fetch(watch.url)
        raw = (
            ProductParser(
                site=watch.site,
                watch_sizes=watch.sizes,
                watch_colors=watch.colors,
            ).parse(result.html)
            if result.html
            else RawProduct(strategy="none")
        )
        observation = verify(result, raw, watch, self.settings)
        observation_id = self.store.save_observation(observation)
        self.store.log_fetch(
            watch.id,
            watch.url,
            result.status_code,
            result.transport,
            observation.status.value,
            "; ".join(observation.notes[:4]) or result.blocked_reason or result.error,
        )

        alerts: list[AlertEvent] = []
        if not observation.can_alert:
            logger.info(
                "no alert for %s: status=%s notes=%s",
                watch.url,
                observation.status.value,
                "; ".join(observation.notes),
                extra={"sda_url": watch.url, "sda_status": observation.status.value},
            )
            return CheckOutcome(watch.id or 0, watch.url, observation, alerts)

        previous = self.store.latest_observation(
            watch.id or 0, verified_only=True, exclude_id=observation_id
        )
        context = RuleContext(
            watch=watch,
            observation=observation,
            observation_id=observation_id,
            previous_price=previous.price if previous else None,
            previous_sizes=self.store.previous_sizes(
                watch.id or 0, exclude_id=observation_id
            ),
        )
        alerts = self.dispatcher.dispatch(evaluate(context))
        logger.info(
            "checked %s: price=%s status=%s alerts=%d",
            watch.url,
            observation.price,
            observation.status.value,
            len(alerts),
            extra={"sda_url": watch.url, "sda_alerts": len(alerts)},
        )
        return CheckOutcome(watch.id or 0, watch.url, observation, alerts)

    def check_all(self) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for watch in self.store.list_watches(enabled_only=True):
            try:
                outcomes.append(self.check_watch(watch))
            except Exception as exc:
                logger.exception("check failed for %s: %s", watch.url, exc)
        return outcomes
