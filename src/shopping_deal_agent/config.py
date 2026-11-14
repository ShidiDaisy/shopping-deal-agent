"""Environment-driven settings and watchlist validation."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader; process environment always wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Settings(BaseModel):
    database_path: Path = Path("data/deals.sqlite3")
    log_path: Path | None = Path("data/agent.log")
    log_level: str = "INFO"
    user_agent: str = "ShoppingDealAgent/0.1 (+mailto:you@example.com)"

    request_timeout_seconds: float = 20.0
    min_request_interval_seconds: float = 3.0
    robots_ttl_seconds: int = 21600
    robots_strict: bool = True
    max_retries: int = 2
    retry_base_delay_seconds: float = 1.0
    page_max_age_seconds: int = 300

    browser_fallback: bool = False
    browser_timeout_ms: int = 15000

    dry_run: bool = True
    alert_cooldown_minutes: int = 60
    allow_fixture_alerts: bool = False

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str | None = None
    email_to: str | None = None

    @classmethod
    def from_env(cls, prefix: str = "SDA_") -> "Settings":
        _load_dotenv()
        values: dict[str, str] = {}
        for name in cls.model_fields:
            raw = os.environ.get(prefix + name.upper())
            if raw is not None and raw != "":
                values[name] = raw
        return cls.model_validate(values)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.email_from and self.email_to)


class WatchSpec(BaseModel):
    url: str
    target_price: Decimal | None = None
    target_currency: str = "SGD"
    sizes: list[str] = []
    colors: list[str] = []
    min_discount_pct: Decimal = Decimal("30")
    min_drop_pct: Decimal = Decimal("5")
    min_drop_abs: Decimal = Decimal("2.00")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) product URL")
        return value.strip()

    @field_validator("target_currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def site(self) -> str:
        return urlparse(self.url).netloc.lower()


def load_watchlist(path: Path) -> list[WatchSpec]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = payload.get("watches", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("watchlist must be a list or an object with a 'watches' list")
    return [WatchSpec.model_validate(entry) for entry in entries]
