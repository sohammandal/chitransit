"""The poller's live schedule (Poller._load_schedule): a file that doesn't cover today is looked
for again in 10 minutes, not an hour, so a late nightly's schedule reaches the live claims soon
(Oct 19: the nightly's fix landed at 5:31 PM, the poller waited until 6:24)."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from ghost_ledger.ingest.poller import SCHEDULE_LOAD_EVERY, SCHEDULE_RETRY_EVERY, Poller

NOW = datetime(2026, 10, 19, 22, 30, tzinfo=UTC)  # 5:30 PM in Chicago


def poller_with(tmp_path, dates: list[str]) -> SimpleNamespace:
    path = tmp_path / "stop_departures.json"
    path.write_text(json.dumps({"built_at": "x", "dates": dates, "base": 0, "stops": {}}))
    return SimpleNamespace(
        settings=SimpleNamespace(live_schedule=str(path)),
        live_sink=object(),
        now=lambda: NOW,
        _next_schedule_load=NOW,
        _schedule_version=None,
        live=SimpleNamespace(schedule=None),
    )


def test_a_schedule_without_today_is_looked_for_again_in_ten_minutes(tmp_path):
    p = poller_with(tmp_path, ["2026-10-17", "2026-10-18"])
    Poller._load_schedule(p)
    assert p.live.schedule is not None
    assert p._next_schedule_load == NOW + SCHEDULE_RETRY_EVERY


def test_a_schedule_with_today_is_looked_for_again_in_an_hour(tmp_path):
    p = poller_with(tmp_path, ["2026-10-19", "2026-10-20"])
    Poller._load_schedule(p)
    assert p._next_schedule_load == NOW + SCHEDULE_LOAD_EVERY
