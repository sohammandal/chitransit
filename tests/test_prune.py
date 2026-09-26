from datetime import UTC, date, datetime

import psycopg
import pytest

from ghost_ledger.config import get_settings
from ghost_ledger.prune import decide, gate

TODAY = date(2026, 9, 30)
DAY = date(2026, 9, 27)
MANIFEST = {
    "computed_at": "2026-09-29T11:05:00+00:00",
    "tables": {"bus_positions": {"2026-09-27": {"rows": 1002, "keys": 1000, "max_lsn": 500}}},
}


def verdict(pg_rows=1000, manifest=MANIFEST, slot_lsn=600, day=DAY):
    return decide(
        "bus_positions", day, "bus_positions_20260927", pg_rows, manifest, slot_lsn, TODAY
    )


def test_drops_only_when_every_check_passes():
    v = verdict()
    assert v.drop, v.reason


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"day": date(2026, 9, 29)}, "younger"),
        ({"manifest": None}, "no Ingress manifest"),
        ({"manifest": {**MANIFEST, "computed_at": "2026-09-27T23:00:00+00:00"}}, "before the day"),
        ({"manifest": {**MANIFEST, "tables": {}}}, "not in the Ingress manifest"),
        ({"pg_rows": 1001}, "keys"),  # a row missing from Ingress
        ({"slot_lsn": None}, "slot"),
        ({"slot_lsn": 499}, "last LSN"),  # Debezium hasn't acknowledged the whole day
    ],
)
def test_fails_closed(kwargs, reason):
    v = verdict(**kwargs)
    assert not v.drop and reason in v.reason


@pytest.mark.db
def test_gate_reads_dev_partitions_and_keeps_everything_without_a_manifest():
    settings = get_settings()
    try:
        conn = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running (make up)")
    with conn:
        verdicts = gate(conn, None, datetime.now(UTC).date())
    assert verdicts and not any(v.drop for v in verdicts)
    assert {v.table for v in verdicts} == {"bus_positions", "raw_responses"}
