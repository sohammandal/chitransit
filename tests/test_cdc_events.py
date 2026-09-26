"""What CDC sees from the changing tables, checked with a temporary decoding slot.

Integration test: needs the dev Postgres (`make up`); skipped when it isn't reachable.
Uses the built-in test_decoding plugin, and always drops its slot so no WAL is retained.
"""

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from ghost_ledger.config import get_settings
from ghost_ledger.ingest.sql import DELETE_ASSIGNMENT, UPSERT_ASSIGNMENT, UPSERT_PATTERN

pytestmark = pytest.mark.db

VID = "test-vid"
PID = 999_999_001


@pytest.fixture
def conn():
    try:
        connection = psycopg.connect(
            get_settings().database_url, autocommit=True, connect_timeout=2
        )
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running (make up)")
    with connection:
        connection.execute("DELETE FROM ops.bus_assignments WHERE vid = %s", (VID,))
        connection.execute("DELETE FROM ops.patterns WHERE pid = %s", (PID,))
        yield connection
        connection.execute("DELETE FROM ops.bus_assignments WHERE vid = %s", (VID,))
        connection.execute("DELETE FROM ops.patterns WHERE pid = %s", (PID,))


@pytest.fixture
def slot(conn):
    name = f"test_{uuid.uuid4().hex[:12]}"
    conn.execute("SELECT pg_create_logical_replication_slot(%s, 'test_decoding')", (name,))
    try:
        yield name
    finally:
        conn.execute("SELECT pg_drop_replication_slot(%s)", (name,))


def changes(conn, slot: str, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT data FROM pg_logical_slot_get_changes(%s, NULL, NULL, 'skip-empty-xacts', '1') "
        "WHERE data LIKE %s",
        (slot, f"table {table}:%"),
    ).fetchall()
    return [r[0] for r in rows]


def assignment(stst: int, direction: str, sweep_id: int) -> dict:
    return {
        "vid": VID, "rt": "79", "direction": direction, "stsd": "2026-09-25", "stst": stst,
        "pid": 1612, "tablockid": "79 -760", "tatripid": None, "origtatripno": None,
        "first_seen_at": "2026-09-26 00:21:44+00", "sweep_id": sweep_id,
    }  # fmt: skip


def test_assignment_lifecycle_emits_insert_update_delete_and_nothing_for_repeats(conn, slot):
    conn.execute(UPSERT_ASSIGNMENT, assignment(64890, "East", 1))
    conn.execute(UPSERT_ASSIGNMENT, assignment(64890, "East", 2))  # same trip: no-op
    conn.execute(UPSERT_ASSIGNMENT, assignment(69390, "West", 90))  # next trip
    conn.execute(DELETE_ASSIGNMENT, {"vid": VID})  # missing past the grace period

    events = changes(conn, slot, "ops.bus_assignments")
    assert [e.split(":")[1].strip() for e in events] == ["INSERT", "UPDATE", "DELETE"]
    update = events[1]
    old, new = update.split("new-tuple:")
    assert "stst[integer]:64890" in old and "'East'" in old  # before-image: the trip it left
    assert "stst[integer]:69390" in new and "'West'" in new
    assert "stst[integer]:69390" in events[2]  # delete carries the full row


def test_unchanged_pattern_refresh_emits_nothing_and_a_detour_emits_an_update(conn, slot):
    def pattern(content_hash: str, length_ft: float) -> dict:
        return {
            "pid": PID, "rt": "79", "rtdir": "Eastbound", "length_ft": length_ft,
            "content_hash": content_hash, "pattern": Jsonb({"pid": PID}),
            "seen_at": "2026-09-26 00:00:00+00",
        }  # fmt: skip

    conn.execute(UPSERT_PATTERN, pattern("hashA", 60363))
    conn.execute(UPSERT_PATTERN, pattern("hashA", 60363))  # daily refresh, unchanged
    conn.execute(UPSERT_PATTERN, pattern("hashB", 61020))  # detour

    events = changes(conn, slot, "ops.patterns")
    assert [e.split(":")[1].strip() for e in events] == ["INSERT", "UPDATE"]
    old, new = events[1].split("new-tuple:")
    assert "'hashA'" in old and "'hashB'" in new
