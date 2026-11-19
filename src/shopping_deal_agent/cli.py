"""Command line interface. No frontend, no background service by default."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import click
from click.core import ParameterSource

from .agent import DealAgent
from .clock import utcnow
from .config import Settings, WatchSpec, load_watchlist
from .fetch.fixture_fetcher import FixtureFetcher
from .fetch.scrapling_fetcher import ScraplingFetcher
from .logging_setup import setup_logging
from .money import format_money
from .notify import AlertDispatcher
from .scheduler import build_scheduler
from .singleton_lock import AlreadyRunning, SingletonLock
from .store import Store

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = _ROOT / "tests" / "fixtures" / "adidas_verified_deal.html"
_DEFAULT_WATCHLIST = _ROOT / "config" / "watchlist.example.json"


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--db", type=click.Path(dir_okay=False), default=None, help="SQLite file override")
@click.pass_context
def main(ctx: click.Context, db: str | None) -> None:
    """Shopping Deal Agent - verified price, discount and restock monitoring."""
    ctx.ensure_object(dict)
    settings = Settings.from_env()
    if db:
        settings = settings.model_copy(update={"database_path": Path(db)})
    setup_logging(settings.log_path, settings.log_level)
    ctx.obj["settings"] = settings


def _store(ctx: click.Context) -> Store:
    obj = ctx.ensure_object(dict)
    store = obj.get("store")
    if store is None:
        store = Store(obj["settings"].database_path)
        store.initialize()
        obj["store"] = store
    return store


def _build_agent(ctx: click.Context, fixture: str | None, url: str | None):
    settings: Settings = ctx.obj["settings"]
    store = _store(ctx)
    if fixture:
        settings = settings.model_copy(
            update={"allow_fixture_alerts": True, "dry_run": True}
        )
        fetcher = FixtureFetcher.from_file(fixture, url or "*")
        click.echo("Fixture mode: offline, dry-run forced, no external requests.")
    else:
        fetcher = ScraplingFetcher(settings)
    agent = DealAgent(settings, store, fetcher, AlertDispatcher(settings, store))
    return settings, store, agent


def _close_fetcher(fetcher) -> None:
    """Release a ScraplingFetcher's cached browser, if it has one. No-op for
    FixtureFetcher and other fetchers that don't hold OS resources."""
    closer = getattr(fetcher, "close", None)
    if callable(closer):
        closer()


def _lock_path(settings: Settings) -> Path:
    db = settings.database_path
    return db.with_name(db.name + ".lock")


def _run_and_print(agent: DealAgent) -> None:
    outcomes = agent.check_all()
    for outcome in outcomes:
        observation = outcome.observation
        price = (
            format_money(observation.price, observation.currency)
            if observation.price is not None
            else "n/a"
        )
        click.echo(
            f"#{outcome.watch_id} {observation.status.value:>11} price={price} "
            f"discount={observation.discount_pct if observation.discount_pct is not None else '-'}% "
            f"source={observation.source_kind.value} strategy={observation.strategy} "
            f"alerts={len(outcome.alerts)}"
        )
        for note in observation.notes[:3]:
            click.echo(f"    - {note}")
        for alert in outcome.alerts:
            click.echo(f"    ! [{alert.condition.value}] {alert.message.splitlines()[0]}")
    click.echo(f"Done: {len(outcomes)} watch(es) checked.")


@main.command("init-db")
@click.pass_context
def init_db(ctx: click.Context) -> None:
    """Create the SQLite schema."""
    _store(ctx)
    click.echo(f"Database ready: {ctx.obj['settings'].database_path}")


@main.group("watch")
def watch_group() -> None:
    """Manage monitored products."""


@watch_group.command("add")
@click.option("--url", required=True, help="Absolute product page URL")
@click.option("--target", default=None, help="Target price, e.g. 45.00")
@click.option("--currency", default="SGD", show_default=True)
@click.option("--size", "sizes", multiple=True, help="Preferred size (repeatable)")
@click.option("--color", "colors", multiple=True, help="Preferred colour (repeatable)")
@click.option("--min-discount", default="30", show_default=True)
@click.option("--min-drop-pct", default="5", show_default=True)
@click.option("--min-drop-abs", default="2.00", show_default=True)
@click.pass_context
def watch_add(
    ctx: click.Context,
    url: str,
    target: str | None,
    currency: str,
    sizes: tuple[str, ...],
    colors: tuple[str, ...],
    min_discount: str,
    min_drop_pct: str,
    min_drop_abs: str,
) -> None:
    """Add or update one watched product."""
    spec = WatchSpec(
        url=url,
        target_price=Decimal(target) if target else None,
        target_currency=currency,
        sizes=list(sizes),
        colors=list(colors),
        min_discount_pct=Decimal(min_discount),
        min_drop_pct=Decimal(min_drop_pct),
        min_drop_abs=Decimal(min_drop_abs),
    )
    watch_id = _store(ctx).upsert_watch(spec)
    click.echo(f"Watch #{watch_id}: {spec.url}")


@watch_group.command("import")
@click.option(
    "--file", "file_path", required=True,
    type=click.Path(exists=True, dir_okay=False), help="JSON watchlist",
)
@click.pass_context
def watch_import(ctx: click.Context, file_path: str) -> None:
    """Import watches from a JSON file."""
    store = _store(ctx)
    specs = load_watchlist(Path(file_path))
    identifiers = [store.upsert_watch(spec) for spec in specs]
    click.echo(f"Imported {len(identifiers)} watch(es): {identifiers}")


@watch_group.command("list")
@click.pass_context
def watch_list(ctx: click.Context) -> None:
    """List all watches."""
    watches = _store(ctx).list_watches(enabled_only=False)
    if not watches:
        click.echo("No watches yet. Try: sda watch import config/watchlist.example.json")
        return
    for item in watches:
        state = "on" if item.enabled else "off"
        click.echo(
            f"#{item.id} [{state}] {item.site} target="
            f"{format_money(item.target_price, item.target_currency)} "
            f"discount>={item.min_discount_pct}%"
        )
        click.echo(f"    {item.url}")
        click.echo(
            f"    sizes={','.join(item.sizes) or '-'} "
            f"colors={','.join(item.colors) or '-'}"
        )


@watch_group.command("enable")
@click.argument("watch_id", type=int)
@click.pass_context
def watch_enable(ctx: click.Context, watch_id: int) -> None:
    _set_enabled(ctx, watch_id, True)


@watch_group.command("disable")
@click.argument("watch_id", type=int)
@click.pass_context
def watch_disable(ctx: click.Context, watch_id: int) -> None:
    _set_enabled(ctx, watch_id, False)


def _set_enabled(ctx: click.Context, watch_id: int, enabled: bool) -> None:
    changed = _store(ctx).set_watch_enabled(watch_id, enabled)
    click.echo(f"Watch #{watch_id} {'enabled' if changed and enabled else 'updated'}")


@watch_group.command("remove")
@click.argument("watch_id", type=int)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
@click.pass_context
def watch_remove(ctx: click.Context, watch_id: int, yes: bool) -> None:
    """Permanently delete a watch and its price/alert history."""
    store = _store(ctx)
    item = store.get_watch(watch_id)
    if item is None:
        click.echo(f"Watch #{watch_id} not found.")
        return
    if not yes:
        click.confirm(f"Delete {item.url} and all its history?", abort=True)
    store.delete_watch(watch_id)
    click.echo(f"Watch #{watch_id} removed: {item.url}")


@main.command("check")
@click.option(
    "--fixture", type=click.Path(exists=True, dir_okay=False), default=None,
    help="Use a local HTML fixture instead of the network (offline demo)",
)
@click.option("--url", default=None, help="URL key for the fixture; default matches any")
@click.pass_context
def check(ctx: click.Context, fixture: str | None, url: str | None) -> None:
    """Run one check across all enabled watches."""
    settings, store, agent = _build_agent(ctx, fixture, url)
    try:
        if not store.list_watches():
            click.echo("No enabled watches. Try: sda demo")
            return
        try:
            with SingletonLock(_lock_path(settings)):
                _run_and_print(agent)
        except AlreadyRunning as exc:
            raise click.ClickException(str(exc))
    finally:
        _close_fetcher(agent.fetcher)


@main.command("demo")
@click.option(
    "--fixture", type=click.Path(exists=True, dir_okay=False),
    default=str(_DEFAULT_FIXTURE), show_default=False,
)
@click.pass_context
def demo(ctx: click.Context, fixture: str) -> None:
    """Offline end-to-end demo using the bundled fixture and sample watchlist."""
    # 演示数据写独立库并每次重置，避免污染正式数据、也避免告警去重吞掉输出。
    root = ctx.find_root()
    if root.get_parameter_source("db") is not ParameterSource.COMMANDLINE:
        settings: Settings = ctx.obj["settings"]
        demo_db = settings.database_path.with_name("demo.sqlite3")
        demo_db.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{demo_db}{suffix}").unlink(missing_ok=True)
        ctx.obj["settings"] = settings.model_copy(update={"database_path": demo_db})
        click.echo(f"Demo database: {demo_db} (reset on every run).")
    store = _store(ctx)
    if _DEFAULT_WATCHLIST.exists():
        for spec in load_watchlist(_DEFAULT_WATCHLIST):
            store.upsert_watch(spec)
    click.echo("Demo: sample watchlist loaded, local fixture, dry-run alerts.")
    _, _, agent = _build_agent(ctx, fixture, None)
    try:
        _run_and_print(agent)
    finally:
        _close_fetcher(agent.fetcher)
    db_flag = f"--db {ctx.obj['settings'].database_path} " if "demo.sqlite3" in str(
        ctx.obj["settings"].database_path
    ) else ""
    click.echo(f"View results: python -m shopping_deal_agent {db_flag}alerts / history 1")


@main.command("run")
@click.option("--interval", type=float, default=60.0, show_default=True, help="Minutes")
@click.pass_context
def run(ctx: click.Context, interval: float) -> None:
    """Run scheduled checks until interrupted."""
    settings, store, agent = _build_agent(ctx, None, None)
    try:
        if not store.list_watches():
            click.echo("No enabled watches. Try: sda watch import config/watchlist.example.json")
            return
        lock = SingletonLock(_lock_path(settings))
        try:
            lock.acquire()
        except AlreadyRunning as exc:
            raise click.ClickException(str(exc))
        try:
            scheduler = build_scheduler(agent, interval)
            click.echo(f"Scheduled every {interval} minute(s). Ctrl-C to stop.")
            try:
                scheduler.start()
            except (KeyboardInterrupt, SystemExit):
                # wait=True: let an in-flight check_all() (and its
                # save_observation transaction) finish before exiting, rather
                # than cutting it off mid-write.
                scheduler.shutdown(wait=True)
                click.echo("Stopped.")
        finally:
            lock.release()
    finally:
        _close_fetcher(agent.fetcher)


@main.command("history")
@click.argument("watch_id", type=int)
@click.option("--limit", type=int, default=20, show_default=True)
@click.pass_context
def history(ctx: click.Context, watch_id: int, limit: int) -> None:
    """Show stored price observations for a watch."""
    rows = _store(ctx).price_history(watch_id, limit)
    if not rows:
        click.echo("No price history for this watch.")
        return
    for row in rows:
        click.echo(
            f"{row.fetched_at.isoformat()} {row.status.value:>11} "
            f"{format_money(row.price, row.currency)} "
            f"orig={format_money(row.original_price, row.currency)} "
            f"sizes={row.sizes} strategy={row.strategy}"
        )


@main.command("alerts")
@click.option("--limit", type=int, default=20, show_default=True)
@click.pass_context
def alerts(ctx: click.Context, limit: int) -> None:
    """Show generated alerts (including dry-run ones)."""
    rows = _store(ctx).list_alerts(limit)
    if not rows:
        click.echo("No alerts yet.")
        return
    for row in rows:
        click.echo(
            f"#{row.id} {row.created_at.isoformat() if row.created_at else '-'} "
            f"[{row.condition.value}] channel={row.channel} delivered={row.delivered}"
        )
        click.echo(f"    {row.message.splitlines()[0]}")


@main.command("verify-config")
@click.pass_context
def verify_config(ctx: click.Context) -> None:
    """Print the effective compliance and delivery configuration."""
    settings: Settings = ctx.obj["settings"]
    click.echo(f"database          : {settings.database_path}")
    click.echo(f"user agent        : {settings.user_agent}")
    click.echo(f"dry run           : {settings.dry_run}")
    click.echo(f"robots strict     : {settings.robots_strict}")
    click.echo(f"min request gap   : {settings.min_request_interval_seconds}s")
    click.echo(f"max retries       : {settings.max_retries}")
    click.echo(f"browser fallback  : {settings.browser_fallback}")
    click.echo(f"telegram ready    : {settings.telegram_enabled}")
    click.echo(f"email ready       : {settings.email_enabled}")
    click.echo(f"max page age      : {settings.page_max_age_seconds}s")


@main.command("status")
@click.option(
    "--max-age-minutes", type=float, default=90.0, show_default=True,
    help="Exit non-zero if the last check is older than this",
)
@click.pass_context
def status(ctx: click.Context, max_age_minutes: float) -> None:
    """Report how long ago the agent last ran a check.

    Nothing about a long-running ``run`` daemon tells an operator it's still
    alive beyond watching the console; this is a cheap hook for an external
    healthcheck/monitor (cron, systemd, Nagios-style check) to poll instead.
    """
    last = _store(ctx).last_fetch_at()
    if last is None:
        click.echo("No checks have run yet.")
        raise SystemExit(1)
    age_seconds = (utcnow() - last).total_seconds()
    click.echo(f"Last check: {last.isoformat()} ({age_seconds / 60:.1f} minute(s) ago)")
    if age_seconds > max_age_minutes * 60:
        click.echo(f"STALE: no check recorded in the last {max_age_minutes:.0f} minute(s).")
        raise SystemExit(1)
