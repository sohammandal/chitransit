"""Ingress upkeep choices (transform/lake_maintenance.py): what gets locked, tagged, trimmed."""

import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SRC = Path(__file__).parents[1] / "transform/lake_maintenance.py"
spec = importlib.util.spec_from_file_location("lake_maintenance", SRC)
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)

NOW = datetime(2026, 9, 28, 11, 0, tzinfo=UTC)  # 06:00 CDT, the nightly
MS = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731


def v(key, hours_ago):
    return {"Key": key, "VersionId": "x", "LastModified": NOW - timedelta(hours=hours_ago)}


def test_only_data_files_are_locked_and_only_recent_ones_unless_all():
    versions = [
        v("warehouse/ghost_ledger_ingress.db/ghost_ops_sweeps/data/a.parquet", 5),
        v("warehouse/ghost_ledger_ingress.db/ghost_ops_sweeps/data/old.parquet", 24 * 10),
        v("warehouse/ghost_ledger_ingress.db/ghost_ops_sweeps/metadata/1.metadata.json", 5),
        v("analytics/trip_status/service_date=2026-09-26/x.parquet", 5),
    ]
    assert [x["Key"].rsplit("/", 1)[1] for x in lm.versions_to_lock(versions, NOW, 3)] == [
        "a.parquet"
    ]
    assert len(lm.versions_to_lock(versions, NOW, None)) == 2  # --all: every data file, no metadata


def test_retention_is_ten_years_from_writing_and_deterministic():
    written = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
    assert lm.retain_until(written) == datetime(2036, 9, 27, 3, 0, tzinfo=UTC)
    assert lm.retain_until(written) == lm.retain_until(written)  # re-locking never moves it
    assert lm.retain_until(datetime(2028, 2, 29, tzinfo=UTC)) == datetime(2038, 2, 28, tzinfo=UTC)


def test_each_finished_chicago_day_gets_its_last_snapshot_tagged():
    # Sep 26 CDT = 05:00 UTC Sep 26 .. 05:00 UTC Sep 27
    s1 = (1, MS(datetime(2026, 9, 26, 15, 0, tzinfo=UTC)))
    s2 = (2, MS(datetime(2026, 9, 27, 4, 55, tzinfo=UTC)))  # 23:55 CDT Sep 26: the day's last
    s3 = (3, MS(datetime(2026, 9, 27, 5, 5, tzinfo=UTC)))  # 00:05 CDT Sep 27
    s4 = (4, MS(datetime(2026, 9, 28, 10, 0, tzinfo=UTC)))  # today (Sep 28 CDT): not finished
    tags = lm.tags_to_create([s1, s2, s3, s4], set(), date(2026, 9, 28))
    assert tags == {"day-2026-09-26": 2, "day-2026-09-27": 3}
    assert lm.tags_to_create([s1, s2, s3, s4], {"day-2026-09-26"}, date(2026, 9, 28)) == {
        "day-2026-09-27": 3
    }


def test_trim_keeps_tags_the_current_snapshot_and_anything_recent():
    old = MS(NOW - timedelta(days=5))
    recent = MS(NOW - timedelta(hours=10))
    snaps = [(1, old), (2, old), (3, old), (4, recent)]
    assert lm.snapshots_to_expire(snaps, protected={2}, current=3, now=NOW, keep_days=2) == [1]
