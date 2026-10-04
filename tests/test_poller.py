import dataclasses
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg
import pytest

from ghost_ledger.bustracker import BusTrackerClient
from ghost_ledger.config import get_settings
from ghost_ledger.ingest import sql
from ghost_ledger.ingest.poller import (
    Poller,
    direction_of,
    next_tick,
    parse_vehicle,
    pattern_hash,
    sample_stops,
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


def test_sample_stops_are_the_first_and_middle_stop():
    pt = [
        {"seq": 1, "typ": "W"},
        {"seq": 2, "typ": "S", "stpid": "10"},
        {"seq": 3, "typ": "S", "stpid": "11"},
        {"seq": 4, "typ": "S", "stpid": "12"},
        {"seq": 5, "typ": "S", "stpid": "13"},
    ]
    assert sample_stops({"pt": pt}) == {"10", "12"}
    assert sample_stops({"pt": [{"typ": "W"}]}) == set()


def test_next_tick_keeps_cadence_and_skips_overrun_ticks():
    assert next_tick(100.0, 30.0, 110.0) == 130.0
    assert next_tick(100.0, 30.0, 175.0) == 190.0  # sweep overran two ticks
    assert next_tick(100.0, 30.0, 130.0) == 160.0


# ---------- integration: the real poller, a fake CTA API, the dev database ----------

ROUTES = ("GLT1", "GLT2")  # made-up routes and ids so nothing collides with real data
PID = 990_001
VID = "GLT-100"
STOPS = ("GLT-S1", "GLT-S2", "GLT-S3")
PATTERN_POINTS = [
    {"seq": 1, "typ": "S", "stpid": STOPS[0], "pdist": 0},
    {"seq": 2, "typ": "W", "pdist": 400},
    {"seq": 3, "typ": "S", "stpid": STOPS[1], "pdist": 500},
    {"seq": 4, "typ": "S", "stpid": STOPS[2], "pdist": 1000},
]


class FakeCta:
    def __init__(self):
        self.vehicles: list[dict] = []
        self.fail_vehicles = False
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(endpoint)
        if endpoint == "alerts.aspx":  # Customer Alerts: keyless
            assert "key" not in request.url.params
            alert = {
                "AlertId": "1",
                "Impact": "Planned Reroute",
                "EventStart": "2026-09-25T09:00:00",
            }
            return httpx.Response(200, json={"CTAAlerts": {"ErrorCode": "0", "Alert": [alert]}})
        assert "key" in request.url.params  # the key is sent, never stored
        if endpoint == "getvehicles":
            if self.fail_vehicles:
                return httpx.Response(503)
            quiet = {"error": [{"rt": ROUTES[0], "msg": "No data found for parameter"}]}
            body = {"vehicle": self.vehicles} if self.vehicles else quiet
            return httpx.Response(200, json={"bustime-response": body})
        if endpoint == "getpatterns":
            ptr = {"pid": PID, "ln": 1000.0, "rtdir": "Eastbound", "pt": PATTERN_POINTS}
            return httpx.Response(200, json={"bustime-response": {"ptr": [ptr]}})
        if endpoint == "getpredictions":
            prd = {"stpid": STOPS[0], "rt": ROUTES[0], "dyn": 1, "stsd": "2026-09-25", "stst": 1}
            return httpx.Response(200, json={"bustime-response": {"prd": [prd]}})
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
    test_polls = (
        "SELECT poll_id FROM ops.raw_polls WHERE request_params->>'rt' LIKE 'GLT%%' "
        "OR request_params->>'pid' = %s OR request_params->>'stpid' LIKE 'GLT%%' "
        "OR request_params->>'routeid' LIKE 'GLT%%'"
    )
    conn.execute(f"DELETE FROM ops.raw_responses WHERE poll_id IN ({test_polls})", (str(PID),))
    conn.execute("DELETE FROM ops.bus_positions WHERE vid LIKE 'GLT-%%'")
    conn.execute("DELETE FROM ops.bus_live_board WHERE vid LIKE 'GLT-%%'")
    conn.execute("DELETE FROM ops.bus_assignments WHERE vid LIKE 'GLT-%%'")
    conn.execute("DELETE FROM ops.patterns WHERE pid = %s", (PID,))
    conn.execute(f"DELETE FROM ops.raw_polls WHERE poll_id IN ({test_polls})", (str(PID),))
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
    assert_replayable(conn, s1.sweep_id)
    # the new pattern's first and middle stops were sampled in the same sweep
    sampled = one(
        conn,
        "SELECT p.request_params->>'stpid', r.body FROM ops.raw_polls p "
        "JOIN ops.raw_responses r USING (poll_id) "
        "WHERE p.sweep_id = %s AND p.endpoint = 'getpredictions'",
        s1.sweep_id,
    )
    assert sorted(sampled[0].split(",")) == [STOPS[0], STOPS[1]]
    assert json.loads(sampled[1])["bustime-response"]["prd"][0]["dyn"] == 1

    # Customer Alerts sampled for the polled routes, stored raw, no key in the request
    alerts = conn.execute(
        "SELECT p.sweep_id, p.error, r.body FROM ops.raw_polls p JOIN ops.raw_responses r "
        "USING (poll_id) WHERE p.endpoint = 'alerts' AND p.request_params->>'routeid' = %s",
        (",".join(ROUTES),),
    ).fetchall()
    assert [(a[0], a[1]) for a in alerts] == [(s1.sweep_id, None)]
    assert json.loads(alerts[0][2])["CTAAlerts"]["Alert"][0]["Impact"] == "Planned Reroute"

    # 2. same report again: no new position, no assignment write, no alerts call yet
    clock.t = T0 + timedelta(seconds=30)
    s2 = poller.sweep()
    assert (s2.new_positions, s2.assigned, s2.patterns_written) == (0, 0, 0)
    n_alerts = (
        "SELECT COUNT(*) FROM ops.raw_polls "
        "WHERE endpoint = 'alerts' AND request_params->>'routeid' = %s"
    )
    assert one(conn, n_alerts, ",".join(ROUTES)) == (1,)

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


POSITION_COLUMNS = (
    "vid, observed_at, tmstmp_local, fetched_at, rt, pid, pdist, lat, lon, hdg, dly, des, "
    "tatripid, tablockid, origtatripno, stst, stsd, psgld"
)


def assert_replayable(conn, sweep_id: int) -> None:
    """Every call of the sweep has its exact response stored, and re-parsing the stored
    getvehicles text with parse_vehicle reproduces bus_positions (derived
    data can be rebuilt from the immutable input)."""
    calls = conn.execute(
        "SELECT p.poll_id, p.endpoint, r.body FROM ops.raw_polls p "
        "LEFT JOIN ops.raw_responses r USING (poll_id) WHERE p.sweep_id = %s",
        (sweep_id,),
    ).fetchall()
    assert calls and all(body is not None for _, _, body in calls)
    for poll_id, endpoint, body in calls:
        if endpoint != "getvehicles":
            continue
        fetched_at = one(conn, "SELECT fetched_at FROM ops.raw_polls WHERE poll_id = %s", poll_id)[
            0
        ]
        vehicles = json.loads(body)["bustime-response"].get("vehicle", [])
        replayed = sorted(tuple(parse_vehicle(v, fetched_at).values()) for v in vehicles)
        stored = conn.execute(
            f"SELECT {POSITION_COLUMNS} FROM ops.bus_positions WHERE poll_id = %s ORDER BY vid",
            (poll_id,),
        ).fetchall()
        assert replayed == sorted(stored)


@pytest.mark.db
def test_restart_keeps_a_bus_that_repeats_a_stale_report(db):
    """A bus resending the same report is present (no new bus_positions row, though). After
    a restart it must not look gone just because its last new position is old."""
    settings, conn = db
    fake = FakeCta()
    clock = Clock(T0)
    transport = httpx.MockTransport(fake.handler)
    first = Poller(settings, BusTrackerClient("k", transport=transport), now=clock)
    first.start()
    fake.vehicles = [bus(64890, "20260925 19:21:44")]
    first.sweep()
    clock.t = T0 + timedelta(minutes=11)  # same stale report 11 min later: still present
    assert first.sweep().new_positions == 0
    first.conn.close()

    # restart a minute later; the bus drops out of this one response
    clock.t = T0 + timedelta(minutes=12)
    fake.vehicles = []
    second = Poller(settings, BusTrackerClient("k", transport=transport), now=clock)
    second.start()
    assert second.sweep().gone == 0  # seen 1 min ago, inside the 10 min grace
    assert one(conn, "SELECT COUNT(*) FROM ops.bus_assignments WHERE vid = %s", VID) == (1,)
    second.conn.close()


@pytest.mark.db
def test_sample_stops_sql_agrees_with_python(db):
    _, conn = db
    pattern = {"pid": PID, "rtdir": "Eastbound", "pt": PATTERN_POINTS}
    conn.execute(
        "INSERT INTO ops.patterns (pid, rt, rtdir, length_ft, content_hash, pattern, "
        "first_seen_at, changed_at) VALUES (%s, %s, 'Eastbound', 1000, 'h', %s, NOW(), NOW())",
        (PID, ROUTES[0], json.dumps(pattern)),
    )
    from_sql = {s for rt, s in conn.execute(sql.LOAD_SAMPLE_STOPS) if rt == ROUTES[0]}
    assert from_sql == sample_stops(pattern)


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


@pytest.mark.db
def test_sweeps_commit_with_the_live_feed_on(db, tmp_path):
    """Regression (Sep 26 00:40 UTC): seeding the live feed's trails outside the startup
    transaction left one open, so every sweep logged success but none committed."""
    settings, conn = db
    feed = tmp_path / "live" / "vehicles.json"
    settings = dataclasses.replace(settings, live_target=str(feed))
    fake = FakeCta()
    fake.vehicles = [bus(64890, "20260925 19:21:44")]
    client = BusTrackerClient("k", transport=httpx.MockTransport(fake.handler))
    poller = Poller(settings, client, now=Clock(T0))
    poller.start()
    s = poller.sweep()
    # seen from another connection: committed, not just logged
    assert one(conn, "SELECT COUNT(*) FROM ops.sweeps WHERE sweep_id = %s", s.sweep_id) == (1,)
    assert json.loads(feed.read_text())["vehicles"][0]["vid"] == VID
    poller.conn.close()


@pytest.mark.db
def test_a_wild_report_time_is_skipped_not_fatal_to_the_sweep(db):
    """One report dated far from the fetch has no day partition: inserting it would roll back
    the whole sweep. It is skipped; the rest of the sweep commits."""
    settings, conn = db
    fake = FakeCta()
    fake.vehicles = [bus(64890, "20270925 19:21:44")]  # a year ahead of the fetch
    client = BusTrackerClient("k", transport=httpx.MockTransport(fake.handler))
    poller = Poller(settings, client, now=Clock(T0))
    poller.start()
    s = poller.sweep()
    assert s.new_positions == 0
    assert one(conn, "SELECT COUNT(*) FROM ops.sweeps WHERE sweep_id = %s", s.sweep_id) == (1,)
    poller.conn.close()
