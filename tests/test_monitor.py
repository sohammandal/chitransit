from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from ghost_ledger.config import get_settings
from ghost_ledger.monitor import Snapshot, compute_metrics, read_snapshot

# 18:00 UTC = 13:00 Chicago (daytime); 08:00 UTC = 03:00 Chicago (night)
NOON = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
NIGHT = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def snap(**overrides) -> Snapshot:
    base = dict(
        now=NOON, last_sweep_finished_at=NOON - timedelta(seconds=20), last_sweep_vehicles=900,
        sweeps_last_hour=120, calls_ok_15m=390, calls_total_15m=390,
        sweeps_last_10m=20, sweeps_with_buses_last_10m=20, slots=(),
    )  # fmt: skip
    return Snapshot(**{**base, **overrides})


def values(s: Snapshot) -> dict[str, float]:
    metrics = compute_metrics(s, poll_interval_seconds=30, disk_used_pct=41.2)
    return {m.name: m.value for m in metrics}


def test_healthy_poller():
    v = values(snap())
    assert v["MinutesSinceLastSweep"] == pytest.approx(0.33, abs=0.01)
    assert v["SweepCoverage1h"] == 1.0
    assert v["CallSuccessRate15m"] == 1.0
    assert v["BusesInService"] == 900
    assert v["ZeroBusesDaytime"] == 0
    assert v["DiskUsedPct"] == 41.2
    assert (v["ReplicationSlotLagMB"], v["ReplicationSlotsLost"]) == (0, 0)


def test_stalled_poller_shows_as_minutes_since_last_sweep():
    v = values(snap(last_sweep_finished_at=NOON - timedelta(minutes=7)))
    assert v["MinutesSinceLastSweep"] == 7.0


def test_no_sweeps_ever_publishes_no_staleness_metric():
    # absent metric -> the alarm's missing-data rule fires, which is the point
    assert "MinutesSinceLastSweep" not in values(snap(last_sweep_finished_at=None))


def test_coverage_counts_missing_sweeps_and_is_capped_at_one():
    assert values(snap(sweeps_last_hour=118))["SweepCoverage1h"] == pytest.approx(0.9833, abs=1e-4)
    assert values(snap(sweeps_last_hour=121))["SweepCoverage1h"] == 1.0


def test_coverage_on_a_fresh_database_counts_only_the_time_since_the_first_sweep():
    # 10 minutes old with every sweep present: full coverage, not 20/120
    fresh = dict(first_sweep_started_at=NOON - timedelta(minutes=10), sweeps_last_hour=20)
    assert values(snap(**fresh))["SweepCoverage1h"] == 1.0
    # ... but a real gap in a young database still shows
    assert values(snap(**{**fresh, "sweeps_last_hour": 10}))["SweepCoverage1h"] == 0.5
    # older than an hour: the normal one-hour window
    old = NOON - timedelta(days=2)
    assert values(snap(first_sweep_started_at=old, sweeps_last_hour=60))["SweepCoverage1h"] == 0.5


def test_call_success_rate_and_its_absence_with_no_calls():
    assert values(snap(calls_ok_15m=351, calls_total_15m=390))["CallSuccessRate15m"] == 0.9
    assert "CallSuccessRate15m" not in values(snap(calls_ok_15m=0, calls_total_15m=0))


def test_zero_buses_alarms_in_daytime_only():
    empty = dict(sweeps_with_buses_last_10m=0, last_sweep_vehicles=0)
    assert values(snap(**empty))["ZeroBusesDaytime"] == 1
    assert values(snap(now=NIGHT, last_sweep_finished_at=NIGHT, **empty))["ZeroBusesDaytime"] == 0


def test_replication_slot_lag_lost_and_inactive():
    slots = ((True, "reserved", 600 * 1_048_576), (False, "lost", None))
    v = values(snap(slots=slots))
    assert v["ReplicationSlotLagMB"] == 600
    assert v["ReplicationSlotsLost"] == 1
    assert v["ReplicationSlotsInactive"] == 1


@pytest.mark.db
def test_snapshot_query_runs_against_the_real_schema():
    try:
        conn = psycopg.connect(get_settings().database_url, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running (make up)")
    with conn:
        s = read_snapshot(conn, datetime.now(UTC))
    assert s.sweeps_last_hour >= 0 and isinstance(s.slots, tuple)
