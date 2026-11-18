"""SQLite persistence. Prices are stored as TEXT to keep Decimal exact."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .clock import from_iso, to_iso, utcnow
from .config import WatchSpec
from .models import (
    AlertEvent,
    Availability,
    Condition,
    Observation,
    Promotion,
    SourceKind,
    VerificationStatus,
    WatchItem,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    site TEXT NOT NULL,
    target_price TEXT,
    target_currency TEXT NOT NULL,
    sizes TEXT NOT NULL DEFAULT '[]',
    colors TEXT NOT NULL DEFAULT '[]',
    min_discount_pct TEXT NOT NULL,
    min_drop_pct TEXT NOT NULL,
    min_drop_abs TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    transport TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    is_fresh INTEGER NOT NULL,
    from_cache INTEGER NOT NULL,
    title TEXT,
    price TEXT,
    original_price TEXT,
    currency TEXT,
    discount_pct TEXT,
    availability TEXT NOT NULL,
    colors TEXT NOT NULL DEFAULT '[]',
    strategy TEXT,
    confidence REAL,
    notes TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_obs_watch_time
    ON observations(watch_id, fetched_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS observation_sizes (
    observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    size TEXT NOT NULL,
    in_stock INTEGER NOT NULL,
    PRIMARY KEY (observation_id, size)
);
CREATE TABLE IF NOT EXISTS observation_promotions (
    observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    kind TEXT NOT NULL,
    code TEXT,
    value TEXT,
    applicable INTEGER NOT NULL,
    reason TEXT NOT NULL,
    valid_until TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    condition TEXT NOT NULL,
    message TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    observation_id INTEGER REFERENCES observations(id),
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    channel TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_watch_time
    ON alerts(watch_id, condition, created_at DESC);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER,
    url TEXT NOT NULL,
    status_code INTEGER,
    transport TEXT,
    outcome TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def _dec(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _json_list(value: str | None) -> tuple[str, ...]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ()
    return tuple(str(item) for item in parsed)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- watches -------------------------------------------------------
    def upsert_watch(self, spec: WatchSpec) -> int:
        payload = (
            spec.url,
            spec.site,
            str(spec.target_price) if spec.target_price is not None else None,
            spec.target_currency,
            json.dumps(spec.sizes),
            json.dumps(spec.colors),
            str(spec.min_discount_pct),
            str(spec.min_drop_pct),
            str(spec.min_drop_abs),
            to_iso(utcnow()),
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO watches
                    (url, site, target_price, target_currency, sizes, colors,
                     min_discount_pct, min_drop_pct, min_drop_abs, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    target_price=excluded.target_price,
                    target_currency=excluded.target_currency,
                    sizes=excluded.sizes,
                    colors=excluded.colors,
                    min_discount_pct=excluded.min_discount_pct,
                    min_drop_pct=excluded.min_drop_pct,
                    min_drop_abs=excluded.min_drop_abs
                """,
                payload,
            )
            row = self._conn.execute(
                "SELECT id FROM watches WHERE url = ?", (spec.url,)
            ).fetchone()
        return int(row["id"])

    @staticmethod
    def _to_watch(row: sqlite3.Row) -> WatchItem:
        return WatchItem(
            id=int(row["id"]),
            url=row["url"],
            site=row["site"],
            target_price=_dec(row["target_price"]),
            target_currency=row["target_currency"],
            sizes=_json_list(row["sizes"]),
            colors=_json_list(row["colors"]),
            min_discount_pct=_dec(row["min_discount_pct"]) or Decimal("30"),
            min_drop_pct=_dec(row["min_drop_pct"]) or Decimal("5"),
            min_drop_abs=_dec(row["min_drop_abs"]) or Decimal("2.00"),
            enabled=bool(row["enabled"]),
            created_at=from_iso(row["created_at"]),
        )

    def list_watches(self, enabled_only: bool = True) -> list[WatchItem]:
        sql = "SELECT * FROM watches"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._to_watch(row) for row in rows]

    def get_watch(self, watch_id: int) -> WatchItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM watches WHERE id = ?", (watch_id,)
            ).fetchone()
        return self._to_watch(row) if row else None

    def set_watch_enabled(self, watch_id: int, enabled: bool) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE watches SET enabled = ? WHERE id = ?",
                (int(enabled), watch_id),
            )
        return cursor.rowcount > 0

    def delete_watch(self, watch_id: int) -> bool:
        """Remove a watch and all dependent rows (foreign keys are enforced)."""
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM watches WHERE id = ?", (watch_id,)
            ).fetchone()
            if not exists:
                return False
            observation_ids = [
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM observations WHERE watch_id = ?", (watch_id,)
                ).fetchall()
            ]
            if observation_ids:
                placeholders = ",".join("?" for _ in observation_ids)
                for table in ("observation_sizes", "observation_promotions"):
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE observation_id IN ({placeholders})",
                        observation_ids,
                    )
            # alerts.observation_id references observations without ON DELETE CASCADE.
            self._conn.execute("DELETE FROM alerts WHERE watch_id = ?", (watch_id,))
            self._conn.execute("DELETE FROM fetch_log WHERE watch_id = ?", (watch_id,))
            self._conn.execute("DELETE FROM observations WHERE watch_id = ?", (watch_id,))
            self._conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        return True

    # ---- observations --------------------------------------------------
    def save_observation(self, observation: Observation) -> int:
        payload = (
            observation.watch_id,
            observation.url,
            observation.status.value,
            observation.source_kind.value,
            observation.transport,
            to_iso(observation.fetched_at),
            int(observation.is_fresh),
            int(observation.from_cache),
            observation.title,
            str(observation.price) if observation.price is not None else None,
            str(observation.original_price) if observation.original_price is not None else None,
            observation.currency,
            str(observation.discount_pct) if observation.discount_pct is not None else None,
            observation.availability.value,
            json.dumps(list(observation.colors)),
            observation.strategy,
            observation.confidence,
            json.dumps(list(observation.notes), ensure_ascii=False),
            json.dumps(dict(observation.evidence), ensure_ascii=False, default=str),
        )
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO observations
                    (watch_id, url, status, source_kind, transport, fetched_at,
                     is_fresh, from_cache, title, price, original_price, currency,
                     discount_pct, availability, colors, strategy, confidence, notes, evidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            observation_id = int(cursor.lastrowid)
            self._conn.executemany(
                "INSERT INTO observation_sizes (observation_id, size, in_stock) VALUES (?, ?, ?)",
                [
                    (observation_id, size, int(in_stock))
                    for size, in_stock in observation.sizes.items()
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO observation_promotions
                    (observation_id, description, kind, code, value, applicable, reason, valid_until)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        observation_id,
                        promo.description,
                        promo.kind,
                        promo.code,
                        str(promo.value) if promo.value is not None else None,
                        int(promo.applicable),
                        promo.reason,
                        promo.valid_until,
                    )
                    for promo in observation.promotions
                ],
            )
        return observation_id

    def _to_observation(self, row: sqlite3.Row) -> Observation:
        observation_id = int(row["id"])
        sizes = {
            size_row["size"]: bool(size_row["in_stock"])
            for size_row in self._conn.execute(
                "SELECT size, in_stock FROM observation_sizes WHERE observation_id = ?",
                (observation_id,),
            )
        }
        promotions = tuple(
            Promotion(
                description=promo["description"],
                kind=promo["kind"],
                code=promo["code"],
                value=_dec(promo["value"]),
                applicable=bool(promo["applicable"]),
                reason=promo["reason"],
                valid_until=promo["valid_until"],
            )
            for promo in self._conn.execute(
                "SELECT * FROM observation_promotions WHERE observation_id = ?",
                (observation_id,),
            )
        )
        return Observation(
            id=observation_id,
            watch_id=int(row["watch_id"]),
            url=row["url"],
            status=VerificationStatus(row["status"]),
            source_kind=SourceKind(row["source_kind"]),
            transport=row["transport"],
            fetched_at=from_iso(row["fetched_at"]) or utcnow(),
            is_fresh=bool(row["is_fresh"]),
            from_cache=bool(row["from_cache"]),
            title=row["title"],
            price=_dec(row["price"]),
            original_price=_dec(row["original_price"]),
            currency=row["currency"],
            discount_pct=_dec(row["discount_pct"]),
            availability=Availability(row["availability"]),
            sizes=sizes,
            colors=_json_list(row["colors"]),
            promotions=promotions,
            strategy=row["strategy"],
            confidence=float(row["confidence"] or 0),
            notes=_json_list(row["notes"]),
            evidence=json.loads(row["evidence"] or "{}"),
        )

    def latest_observation(
        self,
        watch_id: int,
        *,
        verified_only: bool = True,
        exclude_id: int | None = None,
    ) -> Observation | None:
        clauses = ["watch_id = ?"]
        params: list[object] = [watch_id]
        if verified_only:
            clauses.append("status = 'verified'")
        if exclude_id is not None:
            clauses.append("id <> ?")
            params.append(exclude_id)
        sql = (
            "SELECT * FROM observations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY fetched_at DESC, id DESC LIMIT 1"
        )
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return self._to_observation(row) if row else None

    def previous_sizes(self, watch_id: int, exclude_id: int | None = None) -> dict[str, bool]:
        """Sizes from the most recent observation that recorded any sizes."""
        clauses = [
            "o.watch_id = ?",
            "EXISTS (SELECT 1 FROM observation_sizes s WHERE s.observation_id = o.id)",
        ]
        params: list[object] = [watch_id]
        if exclude_id is not None:
            clauses.append("o.id <> ?")
            params.append(exclude_id)
        sql = (
            "SELECT o.id FROM observations o WHERE "
            + " AND ".join(clauses)
            + " ORDER BY o.fetched_at DESC, o.id DESC LIMIT 1"
        )
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            if row is None:
                return {}
            return {
                size_row["size"]: bool(size_row["in_stock"])
                for size_row in self._conn.execute(
                    "SELECT size, in_stock FROM observation_sizes WHERE observation_id = ?",
                    (int(row["id"]),),
                )
            }

    def price_history(self, watch_id: int, limit: int = 50) -> list[Observation]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM observations
                   WHERE watch_id = ? AND price IS NOT NULL
                   ORDER BY fetched_at DESC, id DESC LIMIT ?""",
                (watch_id, max(1, min(limit, 500))),
            ).fetchall()
            return [self._to_observation(row) for row in rows]


    # ---- alerts and fetch log -----------------------------------------
    def alert_exists(self, dedupe_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM alerts WHERE dedupe_key = ? LIMIT 1", (dedupe_key,)
            ).fetchone()
        return row is not None

    def last_alert_at(self, watch_id: int, condition: Condition) -> datetime | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT created_at FROM alerts
                   WHERE watch_id = ? AND condition = ?
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (watch_id, condition.value),
            ).fetchone()
        return from_iso(row["created_at"]) if row else None

    def save_alert(self, event: AlertEvent) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO alerts
                       (watch_id, condition, message, dedupe_key, observation_id,
                        created_at, delivered, channel)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.watch_id,
                    event.condition.value,
                    event.message,
                    event.dedupe_key,
                    event.observation_id,
                    to_iso(event.created_at or utcnow()),
                    int(event.delivered),
                    event.channel,
                ),
            )
        return cursor.rowcount > 0

    def list_alerts(self, limit: int = 50) -> list[AlertEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [
            AlertEvent(
                id=int(row["id"]),
                watch_id=int(row["watch_id"]),
                condition=Condition(row["condition"]),
                message=row["message"],
                dedupe_key=row["dedupe_key"],
                observation_id=row["observation_id"],
                created_at=from_iso(row["created_at"]),
                delivered=bool(row["delivered"]),
                channel=row["channel"],
            )
            for row in rows
        ]

    def log_fetch(
        self,
        watch_id: int | None,
        url: str,
        status_code: int | None,
        transport: str,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO fetch_log
                       (watch_id, url, status_code, transport, outcome, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    watch_id,
                    url,
                    status_code,
                    transport,
                    outcome,
                    detail,
                    to_iso(utcnow()),
                ),
            )

    def last_fetch_at(self) -> datetime | None:
        """When the most recent check attempt (of any outcome) was logged.

        Lets an operator or an external healthcheck answer "is this agent
        still running at all", independent of whether that check succeeded.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(created_at) AS latest FROM fetch_log"
            ).fetchone()
        return from_iso(row["latest"]) if row and row["latest"] else None
