"""Config resolution for dev and prod. Uses a temp dir and an explicit environ, so the
real .env, .env.dev and ~/.secrets are never read."""

from pathlib import Path

import pytest

from ghost_ledger.config import (
    DEV_DATABASE_URL,
    LIVE_SCHEDULE_PROD,
    REPO_ROOT,
    ConfigError,
    get_settings,
    redacted_url,
)


def write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text)


def test_defaults_to_dev_with_no_files_and_no_flags(tmp_path):
    s = get_settings({}, root=tmp_path)
    assert s.env == "dev" and not s.is_prod
    assert s.database_url == DEV_DATABASE_URL
    assert s.poll_interval_seconds == 30
    assert s.poll_routes is None
    assert s.assignment_grace_minutes == 10
    assert s.vehicle_calls_per_day() == 13 * 2_880  # all routes at 30 s fit the budget


def test_rejects_unknown_environment(tmp_path):
    with pytest.raises(ConfigError, match="GHOST_LEDGER_ENV"):
        get_settings({"GHOST_LEDGER_ENV": "staging"}, root=tmp_path)


def test_prod_refuses_to_fall_back_to_the_dev_database(tmp_path):
    # neither the committed .env.dev nor a laptop .env with the dev URL may make prod start
    # against the dev database
    write(tmp_path, ".env.dev", f"DATABASE_URL={DEV_DATABASE_URL}\n")
    write(tmp_path, ".env", f"DATABASE_URL={DEV_DATABASE_URL}\n")
    with pytest.raises(ConfigError, match="Prod needs DATABASE_URL"):
        get_settings({"GHOST_LEDGER_ENV": "prod"}, root=tmp_path)


def test_prod_ignores_the_dev_file(tmp_path):
    write(tmp_path, ".env.dev", "POLL_ROUTES=79,9\n")
    s = get_settings(
        {"GHOST_LEDGER_ENV": "prod", "DATABASE_URL": "postgresql://ghost:pw@db/ghost"},
        root=tmp_path,
    )
    assert s.is_prod and s.poll_routes is None


def test_precedence_environ_over_dotenv_over_env_file(tmp_path):
    write(
        tmp_path,
        ".env.dev",
        "POLL_INTERVAL_SECONDS=45\nPOLL_ROUTES=79\nASSIGNMENT_GRACE_MINUTES=15\n",
    )
    write(tmp_path, ".env", "POLL_INTERVAL_SECONDS=40\nPOLL_ROUTES=9\n")
    s = get_settings({"POLL_INTERVAL_SECONDS": "35"}, root=tmp_path)
    assert s.poll_interval_seconds == 35  # environ wins
    assert s.poll_routes == ("9",)  # .env beats .env.dev
    assert s.assignment_grace_minutes == 15  # falls through to .env.dev


def test_poll_routes_are_parsed_and_empty_means_all(tmp_path):
    assert get_settings({"POLL_ROUTES": " 79, 9 ,"}, root=tmp_path).poll_routes == ("79", "9")
    assert get_settings({"POLL_ROUTES": ""}, root=tmp_path).poll_routes is None


def test_quota_guard_blocks_polling_all_routes_too_fast(tmp_path):
    # 13 calls x 8,640 sweeps at 10 s = ~112k/day, over the key's 100k quota
    with pytest.raises(ConfigError, match="calls/day"):
        get_settings({"POLL_INTERVAL_SECONDS": "10"}, root=tmp_path)
    # a two-route dev subset at 10 s is fine
    assert get_settings({"POLL_INTERVAL_SECONDS": "10", "POLL_ROUTES": "79,9"}, root=tmp_path)


def test_metrics_go_to_the_log_in_dev_and_cloudwatch_in_prod(tmp_path):
    assert get_settings({}, root=tmp_path).metrics_sink == "log"
    prod = {"GHOST_LEDGER_ENV": "prod", "DATABASE_URL": "postgresql://g:p@db/g"}
    assert get_settings(prod, root=tmp_path).metrics_sink == "cloudwatch"
    assert get_settings({**prod, "METRICS_SINK": "log"}, root=tmp_path).metrics_sink == "log"
    with pytest.raises(ConfigError, match="METRICS_SINK"):
        get_settings({"METRICS_SINK": "statsd"}, root=tmp_path)


def test_redacted_url_hides_the_password():
    assert redacted_url("postgresql://ghost:s3cret@localhost:5433/db") == (
        "postgresql://ghost:***@localhost:5433/db"
    )
    assert redacted_url("postgresql://localhost/db") == "postgresql://localhost/db"


def test_committed_dev_file_points_at_local_postgres_and_holds_no_secrets():
    text = (REPO_ROOT / ".env.dev").read_text()
    assert f"DATABASE_URL={DEV_DATABASE_URL}" in text
    assert "API_KEY" not in text


def test_the_live_schedule_is_the_lake_file_in_prod_and_off_in_dev(tmp_path):
    assert get_settings({}, root=tmp_path).live_schedule is None
    prod = {"GHOST_LEDGER_ENV": "prod", "DATABASE_URL": "postgresql://g:p@db/g"}
    assert get_settings(prod, root=tmp_path).live_schedule == LIVE_SCHEDULE_PROD
    local = get_settings({"LIVE_SCHEDULE": "data/stop_departures.json"}, root=tmp_path)
    assert local.live_schedule == "data/stop_departures.json"


def test_trains_are_on_in_prod_and_off_in_dev_and_dev_can_turn_them_on(tmp_path):
    prod = get_settings(
        {"GHOST_LEDGER_ENV": "prod", "DATABASE_URL": "postgresql://x"}, root=tmp_path
    )
    assert prod.train_poll and prod.train_calls_per_day == 90_000
    dev = get_settings({}, root=tmp_path)
    assert not dev.train_poll
    on = get_settings({"TRAIN_POLL": "on"}, root=tmp_path)  # the dev default covers positions
    assert on.train_poll and on.train_calls_per_day >= on.train_positions_per_day()
    with pytest.raises(ConfigError):
        get_settings({"TRAIN_CALLS_PER_DAY": "99000"}, root=tmp_path)  # too near CTA's limit
