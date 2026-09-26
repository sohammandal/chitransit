from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg
import pytest

from ghost_ledger.bustracker import BusTrackerClient
from ghost_ledger.config import get_settings
from ghost_ledger.ingest.poller import (
    Poller,
    direction_of,
    next_tick,
    parse_vehicle,
    pattern_hash,
)

T0 = datetime(2026, 9, 26, 0, 21, 52, tzinfo=UTC)


def test_direction_matches_gtfs_words():
    assert direction_of("Westbound") == "West"
    assert direction_of("Northbound") == "North"


def test_pattern_hash_ignores_key_order():
    a = {"pid": 1, "rtdir": "Eastbound", "pt": [{"seq": 1, "pdist": 0.0}]}
    b = {"pt": [{"pdist": 0.0, "seq": 1}], "rtdir": "Eastbound", "pid": 1}
    assert pattern_hash(a) == pattern_hash(b)
    assert pattern_hash(a) != pattern_hash({**a, "rtdir": "Westbound"})


def test_parse_vehicle_resolves_utc_and_types():
    v = {
        "vid": "8423", "tmstmp": "20260925 19:21:44", "lat": "41.76", "lon": "-87.66",
        "hdg": "358", "pid": 582, "rt": "9", "des": "Ravenswood", "pdist": 23295,
        "dly": False, "tatripid": "202", "origtatripno": "280486293", "tablockid": "9 -658",
        "psgld": "N/A", "stst": 68550, "stsd": "2026-09-25",
    }  # fmt: skip
    row = parse_vehicle(v, T0)
    assert row["observed_at"] == datetime(2026, 9, 26, 0, 21, 44, tzinfo=UTC)
    assert row["tmstmp_local"] == datetime(2026, 9, 25, 19, 21, 44)
    assert (row["lat"], row["hdg"], row["stst"]) == (41.76, 358, 68550)
    assert row["stsd"] == date(2026, 9, 25)


def test_parse_vehicle_without_a_trip():
    row = parse_vehicle({"vid": "1", "tmstmp": "20260925 19:21:44", "rt": "9"}, T0)
    assert row["stsd"] is None and row["stst"] is None


def test_next_tick_keeps_cadence_and_skips_overrun_ticks():
    assert next_tick(100.0, 30.0, 110.0) == 130.0
    assert next_tick(100.0, 30.0, 175.0) == 190.0  # sweep overran two ticks
    assert next_tick(100.0, 30.0, 130.0) == 160.0


# ---------- integration: the real poller, a fake CTA API, the dev database ----------

ROUTES = ("GLT1", "GLT2")  # made-up routes and ids so nothing collides with real data
PID = 990_001
VID = "GLT-100"


class FakeCta:
    def __init__(self):
        self.vehicles: list[dict] = []
        self.fail_vehicles = False
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(endpoint)
        assert "key" in request.url.params  # the key is sent, never stored
        if endpoint == "getvehicles":
            if self.fail_vehicles:
                return httpx.Response(503)
            body = {"vehicle": self.vehicles} if self.vehicles else {"error": [{"msg": "No data"}]}
            return httpx.Response(200, json={"bustime-response": body})
        if endpoint == "getpatterns":
            ptr = {"pid": PID, "ln": 1000.0, "rtdir": "Eastbound", "pt": [{"seq": 1, "pdist": 0}]}
            return httpx.Response(200, json={"bustime-response": {"ptr": [ptr]}})
        return httpx.Response(404)


def bus(stst: int, tmstmp: str) -> dict:
    return {
        "vid": VID, "tmstmp": tmstmp, "lat": "41.7", "lon": "-87.6", "hdg": "90",
        "pid": PID, "rt": ROUTES[0], "des": "Test", "pdist": 100, "dly": False,
        "tatripid": "1", "origtatripno": "2", "tablockid": "B1", "psgld": "N/A",
        "stst": stst, "stsd": "2026-09-25",
    }  # fmt: skip


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def cleanup(conn):
    conn.execute("DELETE FROM ops.bus_positions WHERE vid LIKE 'GLT-%%'")
    conn.execute("DELETE FROM ops.bus_live_board WHERE vid LIKE 'GLT-%%'")
    conn.execute("DELETE FROM ops.bus_assignments WHERE vid LIKE 'GLT-%%'")
    conn.execute("DELETE FROM ops.patterns WHERE pid = %s", (PID,))
    conn.execute(
        "DELETE FROM ops.raw_polls WHERE request_params->>'rt' LIKE 'GLT%%' "
        "OR request_params->>'pid' = %s",
        (str(PID),),
    )
    conn.execute(
        "DELETE FROM ops.sweeps s WHERE NOT EXISTS "
        "(SELECT 1 FROM ops.raw_polls r WHERE r.sweep_id = s.sweep_id)"
    )


@pytest.fixture
def db():
    settings = get_settings({"POLL_ROUTES": ",".join(ROUTES)})
    try:
        conn = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running (make up)")
    cleanup(conn)
    yield settings, conn
    cleanup(conn)
    conn.close()


def one(conn, query: str, *params):
    return conn.execute(query, params).fetchone()


@pytest.mark.db
def test_poller_lifecycle_against_the_database(db):
    settings, conn = db
    fake = FakeCta()
    clock = Clock(T0)
    client = BusTrackerClient(
        "test-key", transport=httpx.MockTransport(fake.handler), sleep=lambda s: None
    )
    poller = Poller(settings, client, now=clock)
    poller.start()

    # 1. first sighting: position, pattern fetched by pid, assignment East
    fake.vehicles = [bus(64890, "20260925 19:21:44")]
    s1 = poller.sweep()
    assert (s1.calls_ok, s1.vehicles, s1.new_positions, s1.assigned) == (1, 1, 1, 1)
    assert one(conn, "SELECT direction, stst FROM ops.bus_assignments WHERE vid = %s", VID) == (
        "East",
        64890,
    )
    assert one(conn, "SELECT rtdir FROM ops.patterns WHERE pid = %s", PID) == ("Eastbound",)
    keys = one(conn, "SELECT COUNT(*) FROM ops.raw_polls WHERE request_params ? 'key'")
    assert keys == (0,)

    # 2. same report again: no new position, no assignment write
    clock.t = T0 + timedelta(seconds=30)
    s2 = poller.sweep()
    assert (s2.new_positions, s2.assigned, s2.patterns_written) == (0, 0, 0)

    # 3. the bus starts its next trip
    clock.t = T0 + timedelta(seconds=60)
    fake.vehicles = [bus(69390, "20260925 19:22:50")]
    s3 = poller.sweep()
    assert (s3.new_positions, s3.assigned) == (1, 1)
    assert one(conn, "SELECT stst FROM ops.bus_assignments WHERE vid = %s", VID) == (69390,)

    # 4. its route's call fails for 20 minutes: no evidence, nothing deleted
    fake.vehicles = []
    fake.fail_vehicles = True
    clock.t = T0 + timedelta(minutes=21)
    s4 = poller.sweep()
    assert (s4.calls_ok, s4.gone) == (0, 0)
    assert one(conn, "SELECT COUNT(*) FROM ops.bus_assignments WHERE vid = %s", VID) == (1,)

    # 5. the call works and the bus is absent past the grace period: gone
    fake.fail_vehicles = False
    clock.t = T0 + timedelta(minutes=22)
    s5 = poller.sweep()
    assert s5.gone == 1
    assert one(conn, "SELECT COUNT(*) FROM ops.bus_assignments WHERE vid = %s", VID) == (0,)
    assert one(conn, "SELECT COUNT(*) FROM ops.bus_live_board WHERE vid = %s", VID) == (0,)

    # every sweep was recorded, including the failed one
    sweeps = conn.execute(
        "SELECT calls_total, calls_ok FROM ops.sweeps WHERE sweep_id IN (%s, %s, %s, %s, %s) "
        "ORDER BY sweep_id",
        (s1.sweep_id, s2.sweep_id, s3.sweep_id, s4.sweep_id, s5.sweep_id),
    ).fetchall()
    assert sweeps == [(1, 1), (1, 1), (1, 1), (1, 0), (1, 1)]
    client.close()
    poller.conn.close()


@pytest.mark.db
def test_restart_rebuilds_state_from_the_database(db):
    settings, conn = db
    fake = FakeCta()
    clock = Clock(T0)
    transport = httpx.MockTransport(fake.handler)
    first = Poller(settings, BusTrackerClient("k", transport=transport), now=clock)
    first.start()
    fake.vehicles = [bus(64890, "20260925 19:21:44")]
    first.sweep()
    first.conn.close()

    # a new process 30 s later knows the pattern and the assignment: nothing re-fetched or rewritten
    clock.t = T0 + timedelta(seconds=30)
    fake.calls.clear()
    second = Poller(settings, BusTrackerClient("k", transport=transport), now=clock)
    second.start()
    s = second.sweep()
    assert s.assigned == 0 and s.patterns_written == 0
    assert fake.calls.count("getpatterns") == 2  # only the daily refresh of the two routes
    second.conn.close()
