"""Delivery with deduplication, cooldown and a safe-by-default dry-run."""
from __future__ import annotations

import logging
import smtplib
from dataclasses import replace
from datetime import timedelta
from email.message import EmailMessage
from typing import Callable, Protocol

import httpx

from .clock import utcnow
from .config import Settings
from .models import AlertEvent
from .store import Store

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    def send(self, event: AlertEvent) -> bool:  # pragma: no cover - protocol
        ...


class TelegramNotifier:
    name = "telegram"

    def __init__(self, settings: Settings, poster: Callable | None = None) -> None:
        self.settings = settings
        self._poster = poster or httpx.post

    def send(self, event: AlertEvent) -> bool:
        if not self.settings.telegram_enabled:
            return False
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        try:
            # Plain text (no parse_mode): page-derived titles are never interpreted as markup.
            response = self._poster(
                url,
                json={
                    "chat_id": self.settings.telegram_chat_id,
                    "text": event.message,
                    "disable_web_page_preview": True,
                },
                timeout=15.0,
            )
            return bool(getattr(response, "is_success", False))
        except Exception as exc:
            logger.warning("telegram delivery failed: %s", exc)
            return False


class EmailNotifier:
    name = "email"

    def __init__(self, settings: Settings, smtp_factory: Callable | None = None) -> None:
        self.settings = settings
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def send(self, event: AlertEvent) -> bool:
        if not self.settings.email_enabled:
            return False
        message = EmailMessage()
        message["Subject"] = f"[Deal Agent] {event.condition.value}"
        message["From"] = self.settings.email_from
        message["To"] = self.settings.email_to
        message.set_content(event.message)
        try:
            with self._smtp_factory(
                self.settings.smtp_host, self.settings.smtp_port, timeout=15
            ) as client:
                if self.settings.smtp_use_tls:
                    client.starttls()
                if self.settings.smtp_user and self.settings.smtp_password:
                    client.login(self.settings.smtp_user, self.settings.smtp_password)
                client.send_message(message)
            return True
        except Exception as exc:
            logger.warning("email delivery failed: %s", exc)
            return False


class AlertDispatcher:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        notifiers: list[Notifier] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.notifiers = notifiers if notifiers is not None else [
            TelegramNotifier(settings),
            EmailNotifier(settings),
        ]

    def dispatch(self, events: list[AlertEvent]) -> list[AlertEvent]:
        dispatched: list[AlertEvent] = []
        for event in events:
            if self.store.alert_exists(event.dedupe_key):
                logger.info("alert suppressed (duplicate): %s", event.condition.value)
                continue
            previous = self.store.last_alert_at(event.watch_id, event.condition)
            if previous is not None and self.settings.alert_cooldown_minutes > 0:
                elapsed = utcnow() - previous
                if elapsed < timedelta(minutes=self.settings.alert_cooldown_minutes):
                    logger.info("alert suppressed (cooldown): %s", event.condition.value)
                    continue

            if self.settings.dry_run:
                delivered, channel = False, "dry-run"
                logger.info("[dry-run] %s | %s", event.condition.value, event.message)
            else:
                results = [
                    (notifier.name, notifier.send(event)) for notifier in self.notifiers
                ]
                delivered = any(success for _, success in results)
                channel = ",".join(name for name, success in results if success) or "failed"
                if not delivered:
                    logger.warning("no channel delivered alert %s", event.condition.value)

            saved = replace(
                event, delivered=delivered, channel=channel, created_at=utcnow()
            )
            if self.store.save_alert(saved):
                dispatched.append(saved)
        return dispatched
