"""Scheduler wiring: UTC-aware immediate first run, single coalesced job.

Regression coverage for a previously-fixed bug: ``next_run_time`` must be a
timezone-aware UTC datetime, not a naive ``datetime.now()`` silently stamped
with the scheduler's UTC timezone (which would be wrong by the host's local
UTC offset).
"""
from __future__ import annotations

from datetime import timezone

import pytest

from shopping_deal_agent.clock import utcnow
from shopping_deal_agent.scheduler import build_scheduler


class _StubAgent:
    def check_all(self):
        return []


def test_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        build_scheduler(_StubAgent(), 0)
    with pytest.raises(ValueError):
        build_scheduler(_StubAgent(), -5)


def test_first_run_is_scheduled_immediately_in_utc():
    before = utcnow()
    scheduler = build_scheduler(_StubAgent(), interval_minutes=60)
    job = scheduler.get_job("check_all")
    after = utcnow()

    assert job.next_run_time.tzinfo is not None
    assert before <= job.next_run_time.astimezone(timezone.utc) <= after


def test_job_is_single_instance_coalesced_with_grace_period():
    scheduler = build_scheduler(_StubAgent(), interval_minutes=5)
    job = scheduler.get_job("check_all")

    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 300
