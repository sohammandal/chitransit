import json
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest

from ghost_ledger.config import get_settings
from ghost_ledger.ingest.trains import (
    ALL_CAP_24H,
    ARRIVALS_CAP_24H,
    CallBudget,
    TrainPoller,
    is_station,
)
from ghost_ledger.l_stations import STATIONS
from ghost_ledger.traintracker import TrainTrackerClient

DAY = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)  # 15:00 in Chicago: service hours
NIGHT = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)  # 03:00 in Chicago


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def minute(t: datetime) -> int:
    return int(t.timestamp() // 60)


# ---------- the call budget ----------


def test_the_budget_paces_a_full_round_every_30_s_by_day_and_every_minute_at_night():
    clock = Clock(DAY)
    b = CallBudget(90_000, clock)
    assert b.day_rate == pytest.approx(1.25)  # 90,000 over 16 h + 8 h at half rate
    spent = 0
    while b.allow_arrivals():
        b.spend(1)
        spent += 1
    assert spent == 75  # a minute's calls saved up, no more
    clock.t += timedelta(seconds=20)
    b._refill()
    assert b.tokens == pytest.approx(25)  # a 20 s sweep: positions + 24 of a round's 36 calls
    night = CallBudget(90_000, Clock(NIGHT))
    night.tokens = 0
    night.now.t += timedelta(seconds=20)
    night._refill()
    assert night.tokens == pytest.approx(12.5)  # half: a full round about every minute


def test_positions_go_first_and_the_24_hour_backstop_stops_arrivals_before_positions():
    clock = Clock(DAY)
    b = CallBudget(90_000, clock)
    b.tokens = 0
    assert b.allow_positions() and not b.allow_arrivals()  # positions don't wait for a token
    busy = CallBudget(90_000, clock, {minute(DAY) - 60: ARRIVALS_CAP_24H})  # an hour ago
    assert busy.allow_positions() and not busy.allow_arrivals()
    full = CallBudget(90_000, clock, {minute(DAY) - 60: ALL_CAP_24H})
    assert not full.allow_positions()
    old = CallBudget(90_000, clock, {minute(DAY) - 24 * 60 - 1: ALL_CAP_24H})  # over a day ago
    assert old.allow_positions() and old.allow_arrivals() and old.used_24h() == 0


def test_a_simulated_day_of_sweeps_stays_inside_the_budget():
    clock = Clock(datetime(2026, 10, 12, 5, 0, tzinfo=UTC))  # midnight in Chicago
    b = CallBudget(90_000, clock)
    rounds = {"day": [], "night": []}
    total = 0
    for _ in range(4_320):  # 24 h of 20 s sweeps
        b.spend(1)  # positions
        n = 0
        while n < 36 and b.allow_arrivals():
            b.spend(1)
            n += 1
        total += 1 + n
        rounds["day" if b.rate(clock.t) == b.day_rate else "night"].append(n)
        clock.t += timedelta(seconds=20)
    assert total <= 90_000 + 75
    day = sum(rounds["day"]) / len(rounds["day"])
    night = sum(rounds["night"]) / len(rounds["night"])
    assert 23 < day < 25  # every station's arrivals about every 30 s in service hours
    assert 10 < night < 13  # about every minute at night
    assert b.used_24h() <= ARRIVALS_CAP_24H


def test_station_ids_are_the_4xxxx_parent_stations():
    assert len(STATIONS) >= 140 and all(is_station(s) for s in STATIONS)
    assert not is_station("30074") and not is_station("") and not is_station(None)


# ---------- a sweep against the database ----------


class FakeTrainTracker:
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.replies: dict[str, list[str]] = {}  # endpoint -> bodies as sent
        self.fail_positions = False

    def reply(self, endpoint: str, doc: dict) -> httpx.Response:
        text = json.dumps(doc)
        self.replies.setdefault(endpoint, []).append(text)
        return httpx.Response(200, text=text, headers={"content-type": "application/json"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1].removesuffix(".aspx")
        self.requests.append((endpoint, dict(request.url.params)))
        if endpoint == "routes":
            assert "key" not in request.url.params
            return self.reply(endpoint, {"CTARoutes": {"ErrorCode": "0", "RouteInfo": [
                {"Route": "Red Line", "RouteStatus": "Normal Service"}]}})  # fmt: skip
        assert request.url.params.get("key") == "test-train-key"
        if endpoint == "ttpositions":
            if self.fail_positions:
                return httpx.Response(503)
            trains = [
                {"rn": "812", "nextStaId": "40380", "nextStaNm": "Clark/Lake"},
                {"rn": "813", "nextStaId": "41999", "nextStaNm": "A New Station"},
            ]
            return self.reply(endpoint, {"ctatt": {"errCd": "0", "route": [
                {"@name": "red", "train": trains}, {"@name": "y", "train": {"rn": "501"}},
            ]}})  # fmt: skip
        if endpoint == "ttarrivals":
            etas = [{"staId": m, "isSch": "0"} for m in request.url.params["mapid"].split(",")]
            return self.reply(endpoint, {"ctatt": {"errCd": "0", "eta": etas}})
        return httpx.Response(404)


def remove(conn, sweep_ids: list[int]) -> None:
    if not sweep_ids:
        return
    polls = "SELECT poll_id FROM ops.raw_polls WHERE sweep_id = ANY(%s)"
    conn.execute(f"DELETE FROM ops.raw_responses WHERE poll_id IN ({polls})", (sweep_ids,))
    conn.execute("DELETE FROM ops.raw_polls WHERE sweep_id = ANY(%s)", (sweep_ids,))
    conn.execute("DELETE FROM ops.sweeps WHERE sweep_id = ANY(%s)", (sweep_ids,))


@pytest.fixture
def db():
    settings = get_settings({"TRAIN_POLL": "on", "TRAIN_CALLS_PER_DAY": "90000"})
    try:
        conn = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running (make up)")
    created: list[int] = []
    yield settings, conn, created
    remove(conn, created)
    conn.close()


@pytest.mark.db
def test_a_train_sweep_keeps_every_reply_raw_in_one_transaction(db):
    settings, conn, created = db
    fake = FakeTrainTracker()
    client = TrainTrackerClient(
        "test-train-key", transport=httpx.MockTransport(fake.handler), sleep=lambda s: None
    )
    clock = Clock(DAY)
    poller = TrainPoller(settings, client, now=clock, sleep=lambda s: None)

    s1 = poller.sweep()
    created.append(s1.sweep_id)
    # positions, the line status, and every station's arrivals 4 at a time (a new station too)
    assert "41999" in poller.stations
    n_stations = len(poller.stations)
    assert (s1.trains, s1.stations_sampled) == (3, n_stations)
    assert s1.calls_total == s1.calls_ok == 2 + -(-n_stations // 4)
    row = conn.execute(
        "SELECT feed, calls_total, calls_ok, vehicles FROM ops.sweeps WHERE sweep_id = %s",
        (s1.sweep_id,),
    ).fetchone()
    assert row == ("train", s1.calls_total, s1.calls_ok, 3)
    polls = conn.execute(
        "SELECT p.feed, p.endpoint, p.request_params, p.vehicle_count, r.body "
        "FROM ops.raw_polls p JOIN ops.raw_responses r USING (poll_id, fetched_at) "
        "WHERE p.sweep_id = %s ORDER BY p.poll_id",
        (s1.sweep_id,),
    ).fetchall()
    assert {p[0] for p in polls} == {"train"}
    endpoints = [p[1] for p in polls]
    assert endpoints[0] == "ttpositions" and endpoints[-1] == "routes"  # arrivals in between
    assert set(endpoints[1:-1]) == {"ttarrivals"}  # right after positions: the same snapshot
    assert all("key" not in p[2] for p in polls)  # the key is sent, never stored
    # every reply exactly as CTA sent it, in the order sent
    for endpoint in ("ttpositions", "routes", "ttarrivals"):
        assert [p[4] for p in polls if p[1] == endpoint] == fake.replies[endpoint]
    batches = [p[2]["mapid"].split(",") for p in polls if p[1] == "ttarrivals"]
    assert all(1 <= len(b) <= 4 for b in batches)
    assert sorted(m for b in batches for m in b) == sorted(poller.stations)  # each once
    assert [p[3] for p in polls if p[1] == "ttpositions"] == [3]

    # CTA fails: the sweep is still written, with the failure on the record
    fake.fail_positions = True
    clock.t += timedelta(seconds=30)
    s2 = poller.sweep()
    created.append(s2.sweep_id)
    assert s2.trains == 0 and s2.calls_ok == s2.calls_total - 1
    failed = conn.execute(
        "SELECT attempts, http_status, error FROM ops.raw_polls "
        "WHERE sweep_id = %s AND endpoint = 'ttpositions'",
        (s2.sweep_id,),
    ).fetchone()
    assert failed == (3, 503, "HTTP 503")
    assert "routes" not in [e for e, _ in fake.requests[-(s2.calls_total + 2) :]]  # every 2 min

    # a restart reads the day's calls back from the log: the budget can't be reset
    spent = conn.execute(
        "SELECT SUM(attempts) FROM ops.raw_polls WHERE feed = 'train' AND endpoint LIKE 'tt%%' "
        "AND fetched_at > %s",
        (clock.t - timedelta(hours=24),),
    ).fetchone()[0]
    again = TrainPoller(settings, client, now=clock, sleep=lambda s: None)
    assert again._ensure_budget().used_24h() == spent >= s1.calls_total - 1 + 3
    client.close()
    poller.conn.close()
    again.conn.close()


def test_a_sweeps_calls_reach_the_live_files_and_a_failing_sink_never_raises(tmp_path):
    settings = get_settings({"TRAIN_POLL": "on", "LIVE_TARGET": str(tmp_path / "vehicles.json")})
    # no calls, no database; the test's own clock (DAY, 3 PM): on the wall clock its 3:02 PM arrival
    # went stale at 3:02 PM on Oct 12
    poller = TrainPoller(settings, client=None, connect=lambda: None, now=lambda: DAY)

    def call(endpoint: str, params: dict, body: dict):
        from ghost_ledger.bustracker import ApiResult
        from ghost_ledger.ingest.poller import Call

        return Call(endpoint, params, ApiResult(body=body, raw="{}", attempts=1), DAY)

    positions = {"route": [{"@name": "red", "train": [{"rn": "812", "nextStaId": "40380"}]}]}
    arrivals = {
        "eta": [{"staId": "40380", "rn": "812", "rt": "Red", "arrT": "2026-10-12T15:02:00"}]
    }
    poller._publish([
        call("ttpositions", {"rt": "red"}, positions),
        call("ttarrivals", {"mapid": "40380"}, arrivals),
    ])  # fmt: skip
    trains = json.loads((tmp_path / "trains.json").read_text())
    stations = json.loads((tmp_path / "stations.json").read_text())
    assert [t["rn"] for t in trains["trains"]] == ["812"]
    assert [e["rn"] for e in stations["stations"]["40380"]["eta"]] == ["812"]

    class Broken:
        def put_doc(self, name, doc):
            raise OSError("S3 is down")

    poller.live_sink = Broken()
    poller._publish([call("ttpositions", {"rt": "red"}, positions)])  # logged, not raised
