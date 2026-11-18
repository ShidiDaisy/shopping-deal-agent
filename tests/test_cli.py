"""CLI wiring tests via click.testing.CliRunner. No real network, no real
logging side effects, no shared state between tests (each gets its own
temp-dir database and lock file).
"""
from __future__ import annotations

from click.testing import CliRunner

from helpers import FIXTURE_DIR, URL
from shopping_deal_agent import cli
from shopping_deal_agent.config import Settings
from shopping_deal_agent.singleton_lock import SingletonLock


def _runner(monkeypatch, tmp_path, **env) -> CliRunner:
    # setup_logging mutates the global logging module and writes real files;
    # none of that is what these tests are checking.
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    monkeypatch.setenv("SDA_DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return CliRunner()


def test_watch_add_list_remove(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)

    added = runner.invoke(cli.main, ["watch", "add", "--url", URL, "--target", "45.00"])
    assert added.exit_code == 0, added.output
    assert "#1" in added.output

    listed = runner.invoke(cli.main, ["watch", "list"])
    assert listed.exit_code == 0, listed.output
    assert URL in listed.output

    removed = runner.invoke(cli.main, ["watch", "remove", "1", "--yes"])
    assert removed.exit_code == 0, removed.output
    assert "removed" in removed.output.lower()

    listed_after = runner.invoke(cli.main, ["watch", "list"])
    assert "No watches yet" in listed_after.output


def test_check_runs_against_a_fixture_and_reports_verified(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    runner.invoke(cli.main, ["watch", "add", "--url", URL, "--target", "45.00"])

    fixture_path = str(FIXTURE_DIR / "adidas_verified_deal.html")
    result = runner.invoke(cli.main, ["check", "--fixture", fixture_path, "--url", URL])

    assert result.exit_code == 0, result.output
    assert "verified" in result.output
    assert "Done: 1 watch(es) checked." in result.output


def test_check_with_no_watches_does_not_error(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    result = runner.invoke(cli.main, ["check"])
    assert result.exit_code == 0, result.output
    assert "No enabled watches" in result.output


def test_verify_config_does_not_print_secrets(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path, SDA_TELEGRAM_BOT_TOKEN="super-secret-token")
    result = runner.invoke(cli.main, ["verify-config"])
    assert result.exit_code == 0, result.output
    assert "super-secret-token" not in result.output


def test_demo_runs_fully_offline_and_reports_dry_run_alerts(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    result = runner.invoke(cli.main, ["demo"])
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower() or "Done:" in result.output


def test_status_before_any_check_is_stale(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    result = runner.invoke(cli.main, ["status"])
    assert result.exit_code != 0
    assert "No checks have run yet" in result.output


def test_status_after_a_check_is_fresh(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    runner.invoke(cli.main, ["watch", "add", "--url", URL, "--target", "45.00"])
    fixture_path = str(FIXTURE_DIR / "adidas_verified_deal.html")
    runner.invoke(cli.main, ["check", "--fixture", fixture_path, "--url", URL])

    result = runner.invoke(cli.main, ["status", "--max-age-minutes", "90"])
    assert result.exit_code == 0, result.output
    assert "Last check:" in result.output


def test_status_flags_stale_with_a_tiny_max_age(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    runner.invoke(cli.main, ["watch", "add", "--url", URL, "--target", "45.00"])
    fixture_path = str(FIXTURE_DIR / "adidas_verified_deal.html")
    runner.invoke(cli.main, ["check", "--fixture", fixture_path, "--url", URL])

    result = runner.invoke(cli.main, ["status", "--max-age-minutes", "0"])
    assert result.exit_code != 0
    assert "STALE" in result.output


def test_second_check_is_rejected_while_another_instance_holds_the_lock(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    runner.invoke(cli.main, ["watch", "add", "--url", URL, "--target", "45.00"])

    db_path = tmp_path / "test.sqlite3"
    held = SingletonLock(cli._lock_path(Settings(database_path=db_path)))
    held.acquire()
    try:
        result = runner.invoke(cli.main, ["check"])
        assert result.exit_code != 0
        assert "already" in result.output.lower()
    finally:
        held.release()

    # once released, a normal check succeeds again
    fixture_path = str(FIXTURE_DIR / "adidas_verified_deal.html")
    result = runner.invoke(cli.main, ["check", "--fixture", fixture_path, "--url", URL])
    assert result.exit_code == 0, result.output
