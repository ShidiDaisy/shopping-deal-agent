"""Periodic checks via APScheduler. One job, coalesced, never overlapping."""
from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from .agent import DealAgent
from .clock import utcnow


def build_scheduler(agent: DealAgent, interval_minutes: float) -> BlockingScheduler:
    if interval_minutes <= 0:
        raise ValueError("interval must be greater than zero")
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        agent.check_all,
        trigger="interval",
        minutes=interval_minutes,
        id="check_all",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        next_run_time=utcnow(),
    )
    return scheduler
