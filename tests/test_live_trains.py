from datetime import UTC, datetime, timedelta

from ghost_ledger.bustracker import ApiResult
from ghost_ledger.ingest.poller import Call
from ghost_ledger.live_trains import (
    LiveTrains,
    etas_from,
    local_epoch,
    status_from,
    trains_from,
)

FETCHED = datetime(2026, 10, 12, 1, 48, 10, tzinfo=UTC)  # 20:48:10 in Chicago


def epoch(local: str) -> int:
    """A Chicago-local time on Oct 11 (CDT, UTC-5) as epoch seconds."""
    return int(
        (datetime.fromisoformat(f"2026-10-11T{local}") + timedelta(hours=5))
        .replace(tzinfo=UTC)
        .timestamp()
    )


def train(rn: str, **kw) -> dict:
    return {
        "rn": rn, "destNm": "Howard", "trDr": "1", "nextStaId": "40380", "nextStaNm": "Clark/Lake",
        "prdt": "2026-10-11T20:48:03", "arrT": "2026-10-11T20:50:03", "isApp": "0", "isDly": "0",
        "flags": None, "lat": "41.88", "lon": "-87.63", "heading": "358", **kw,
    }  # fmt: skip


def test_trains_come_as_a_list_an_object_or_not_at_all():
    body = {"route": [
        {"@name": "red", "train": [train("812"), train("813", isDly="1")]},
        {"@name": "y", "train": train("593")},  # one train: an object
        {"@name": "p"},  # none
    ]}  # fmt: skip
    trains = trains_from(body, FETCHED)
    assert [(t["rt"], t["rn"]) for t in trains] == [("red", "812"), ("red", "813"), ("y", "593")]
    t = trains[0]
    assert (t["dir"], t["dest"], t["next"], t["next_name"]) == (
        "1",
        "Howard",
        "40380",
        "Clark/Lake",
    )
    assert (t["arr"], t["t"]) == (epoch("20:50:03"), epoch("20:48:03"))
    assert (t["app"], t["dly"], trains[1]["dly"]) == (False, False, True)
    assert (t["lat"], t["lon"], t["hdg"]) == (41.88, -87.63, 358)


def test_a_train_cta_cant_place_has_no_position_and_north_is_a_heading():
    lost, north = trains_from({"route": {"@name": "blue", "train": [
        train("125", lat="0", lon="0", heading="90"), train("131", heading="0"),
    ]}}, FETCHED)  # fmt: skip
    assert (lost["lat"], lost["lon"], lost["hdg"]) == (None, None, None)
    assert north["hdg"] == 0


def test_local_times_take_the_nearer_hour_when_dst_ends():
    # Nov 1, 01:30 Chicago happens twice: 06:30 UTC (CDT) and 07:30 UTC (CST)
    first = datetime(2026, 11, 1, 6, 31, tzinfo=UTC)
    second = datetime(2026, 11, 1, 7, 31, tzinfo=UTC)
    assert local_epoch("2026-11-01T01:30:00", first) == int(
        datetime(2026, 11, 1, 6, 30, tzinfo=UTC).timestamp()
    )
    assert local_epoch("2026-11-01T01:30:00", second) == int(
        datetime(2026, 11, 1, 7, 30, tzinfo=UTC).timestamp()
    )
    assert local_epoch("20261011 20:48:03", FETCHED) == epoch("20:48:03")  # XML's form
    assert local_epoch(None, FETCHED) is None and local_epoch("soon", FETCHED) is None


def test_predictions_group_by_station_with_the_line_codes_of_positions():
    body = {"eta": [
        {"staId": "40380", "stpId": "30074", "stpDe": "Service toward Loop", "rn": "812",
         "rt": "Brn", "destNm": "Loop", "prdt": "2026-10-11T20:48:03",
         "arrT": "2026-10-11T20:52:03", "isApp": "0", "isSch": "1", "isDly": "0", "isFlt": "1"},
        {"staId": "41450", "stpId": "30279", "rt": "Red", "rn": "913",
         "arrT": "2026-10-11T20:49:03"},
    ]}  # fmt: skip
    etas = etas_from(body, FETCHED)
    e = etas["40380"][0]
    assert (e["rt"], e["dest"], e["stop"], e["stop_desc"]) == (
        "brn",
        "Loop",
        "30074",
        "Service toward Loop",
    )
    assert (e["sch"], e["flt"], e["app"], e["dly"]) == (True, True, False, False)
    assert etas["41450"][0]["rt"] == "red"


def test_line_status_in_ctas_words_by_line_code():
    body = {"RouteInfo": [
        {"ServiceId": "G", "RouteStatus": "Planned Work w/Station(s) Bypassed"},
        {"ServiceId": "Red", "RouteStatus": "Normal Service"},
        {"ServiceId": "Pexp", "RouteStatus": "Normal Service"},  # not a Train Tracker line
    ]}  # fmt: skip
    assert status_from(body) == {"g": "Planned Work w/Station(s) Bypassed", "red": "Normal Service"}


def call(endpoint: str, params: dict, body: dict | None, ok: bool = True) -> Call:
    result = ApiResult(body=body or {}, raw="{}", attempts=1, error=None if ok else "HTTP 503")
    return Call(endpoint, params, result, FETCHED)


def test_the_feed_shows_each_stations_latest_answer_and_never_passes_off_old_trains_as_new():
    live = LiveTrains()
    positions = {"route": [{"@name": "red", "train": [train("812")]}]}
    arrivals = {
        "eta": [{"staId": "40380", "rn": "812", "rt": "Red", "arrT": "2026-10-11T20:52:03"}]
    }
    status = {"RouteInfo": [{"ServiceId": "Red", "RouteStatus": "Normal Service"}]}
    live.update([
        call("ttpositions", {"rt": "red"}, positions),
        call("ttarrivals", {"mapid": "40380,41450"}, arrivals),
        call("routes", {"type": "rail"}, status),
    ])  # fmt: skip
    docs = dict(live.documents(FETCHED))
    assert [t["rn"] for t in docs["trains.json"]["trains"]] == ["812"]
    assert docs["trains.json"]["status"] == {"red": "Normal Service"}
    assert docs["trains.json"]["data_at"] == epoch("20:48:03")
    stations = docs["stations.json"]["stations"]
    assert [e["rn"] for e in stations["40380"]["eta"]] == ["812"]
    assert stations["41450"] == {"at": int(FETCHED.timestamp()), "eta": []}  # asked, none now

    # positions fail: no trains.json (an old list mustn't look fresh), stations.json still due;
    # the station asked again with nothing to show loses its old predictions
    live.update([
        call("ttpositions", {"rt": "red"}, None, ok=False),
        call("ttarrivals", {"mapid": "40380"}, {}),
    ])  # fmt: skip
    docs = dict(live.documents(FETCHED))
    assert "trains.json" not in docs
    assert docs["stations.json"]["stations"]["40380"]["eta"] == []
    assert live.status == {"red": "Normal Service"}  # kept until the next status call


def test_predictions_past_their_time_drop_out():
    live = LiveTrains()
    arrivals = {"eta": [
        {"staId": "40380", "rn": "1", "rt": "Red", "arrT": "2026-10-11T20:47:00"},  # 70 s ago
        {"staId": "40380", "rn": "2", "rt": "Red", "arrT": "2026-10-11T20:48:00"},  # 10 s ago
        {"staId": "40380", "rn": "3", "rt": "Red", "arrT": "2026-10-11T20:53:00"},
    ]}  # fmt: skip
    live.update([call("ttarrivals", {"mapid": "40380"}, arrivals)])
    stations = dict(live.documents(FETCHED))["stations.json"]["stations"]
    assert [e["rn"] for e in stations["40380"]["eta"]] == ["2", "3"]


# ---- passes, gaps and claims (as the buses': live.py) ----
PATHS = {"red": [["p1", "p2", "p3", "p4", "p5", "p6"]]}  # one direction's platforms, in order
T = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)


def schedule(departures: list[tuple[str, datetime]], rt: str = "red"):
    """A live schedule as the nightly writes it (transform/live_schedule.py), rail rows keyed by
    the line's code."""
    import importlib.util

    from ghost_ledger.live import CHICAGO, Schedule

    spec = importlib.util.spec_from_file_location("live_schedule", "transform/live_schedule.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [(rt, stop, t.astimezone(CHICAGO).date(), int(t.timestamp())) for stop, t in departures]
    return Schedule(mod.build(rows))


def snap(live: LiveTrains, minute: float, *trains: tuple[str, str] | tuple[str, str, str]) -> None:
    """A positions snapshot `minute` minutes after T: (rn, next platform[, destination])."""
    live.trains = [
        {"rt": "red", "rn": t[0], "stop": t[1], "dest": t[2] if len(t) > 2 else "Howard"}
        for t in trains
    ]
    live.passed(int((T + timedelta(minutes=minute)).timestamp()))


def run(live: LiveTrains, steps: list[tuple[float, list]]) -> None:
    """Snapshots every 20 s through each step's minute, the trains where that step puts them."""
    minute = 0.0
    for until, trains in steps:
        while minute <= until:
            snap(live, minute, *trains)
            minute += 1 / 3


def by_rn(live: LiveTrains, minute: float) -> dict[str, dict]:
    live.claims(T + timedelta(minutes=minute))
    return {t["rn"]: t for t in live.trains}


def secs(minute: float) -> int:
    return int((T + timedelta(minutes=minute)).timestamp())


def test_a_train_passes_a_platform_when_its_next_platform_moves_on():
    live = LiveTrains(paths=PATHS)
    snap(live, 0, ("A", "p1"))
    snap(live, 1 / 3, ("A", "p1"))
    snap(live, 2 / 3, ("A", "p2"))
    assert list(live.passes[("red", "p1")]) == [(secs(2 / 3), "red:A", False)]
    assert ("red", "p2") not in live.passes  # not yet: p2 is still ahead of it


def test_a_close_station_passed_between_snapshots_is_filled_in_and_a_jump_is_only_a_bound():
    live = LiveTrains(paths=PATHS)
    snap(live, 0, ("A", "p1"))
    snap(live, 1 / 3, ("A", "p3"))  # passed p1 and p2 within 20 s (downtown): timed
    assert live.passes[("red", "p2")][-1] == (secs(1 / 3), "red:A", False)
    snap(live, 2 / 3, ("A", "p6"))  # p3, p4 and p5 in 20 s: a jump, its passes only bounds
    assert [live.passes[("red", p)][-1][2] for p in ("p3", "p4", "p5")] == [True, True, True]


def test_a_move_onto_no_known_path_or_after_an_absence_puts_the_platforms_ahead_in_doubt():
    live = LiveTrains(paths=PATHS)
    run(live, [(1, [("A", "p1")]), (2, [("A", "p2"), ("B", "p1")])])  # A passed p1
    snap(live, 7 / 3, ("A", "x9"), ("B", "p1"))  # A onto no path we know: it may be anywhere
    t = by_rn(live, 7 / 3)
    assert t["B"]["gap_s"] is not None  # p1, behind where A was, isn't in doubt
    snap(live, 8 / 3, ("A", "x9"), ("C", "p4"))
    assert by_rn(live, 8 / 3)["C"]["gap_s"] is None  # p4: A may have passed it unseen
    live2 = LiveTrains(paths=PATHS)
    snap(live2, 0, ("A", "p1"))
    snap(live2, 5, ("A", "p3"))  # unseen for 5 minutes: when it passed p1 and p2 is unknown
    assert ("red", "p1") not in live2.passes and live2.doubt[("red", "p2")] == secs(5)


def test_the_gap_is_since_the_last_train_passed_the_next_platform_unless_snapshots_are_missing():
    live = LiveTrains(paths=PATHS)
    run(live, [(10, [("A", "p2")]), (30, [("A", "p3"), ("B", "p2")])])
    b = by_rn(live, 30)["B"]
    assert (b["gap_s"], b["last_pass"]) == (20 * 60 - 20, secs(10 + 1 / 3))
    assert b["long_wait"] is False  # no schedule: no judgment
    snap(live, 33, ("A", "p3"), ("B", "p2"))  # 3 minutes without positions: trains may have passed
    assert by_rn(live, 33)["B"]["gap_s"] is None


def test_a_long_wait_is_over_twice_the_scheduled_gap_with_two_trains_due():
    every_8 = [("p2", T - timedelta(minutes=60) + timedelta(minutes=8 * i)) for i in range(15)]
    live = LiveTrains(lambda: schedule(every_8), paths=PATHS, publish_claims=True)
    run(live, [(10, [("A", "p2")]), (30, [("A", "p3"), ("B", "p2")])])
    b = by_rn(live, 30)["B"]
    assert b["gap_s"] == 19 * 60 + 40 and b["long_wait"] is True  # 19:40 > 2 x 8:00 + 1:00
    b = by_rn(live, 27)["B"]  # 16:40: not yet past twice the schedule and the margin
    assert b["long_wait"] is False
    # nothing due since the last pass (a break in the schedule): a long time, not a long wait
    after_a_break = [("p2", T - timedelta(minutes=50 - 8 * i)) for i in range(5)]
    live = LiveTrains(lambda: schedule(after_a_break), paths=PATHS, publish_claims=True)
    run(live, [(10, [("A", "p2")]), (30, [("A", "p3"), ("B", "p2")])])
    assert by_rn(live, 30)["B"]["long_wait"] is False


def test_together_under_a_quarter_of_the_scheduled_gap_behind_the_train_ahead_still_on_the_line():
    every_10 = [("p3", T - timedelta(minutes=60) + timedelta(minutes=10 * i)) for i in range(10)]
    live = LiveTrains(lambda: schedule(every_10), paths=PATHS, publish_claims=True)
    run(live, [(5, [("A", "p3"), ("B", "p2")]), (6, [("A", "p4"), ("B", "p3")]),
               (8, [("A", "p4"), ("B", "p4")])])  # fmt: skip
    b = by_rn(live, 8)["B"]
    assert (b["together_with"], b["together_s"]) == ("red:A", 60)  # 1:00 < 2:30 - 0:30
    # the train ahead turned back toward somewhere else: not together on this line any more
    snap(live, 25 / 3, ("A", "p5", "95th/Dan Ryan"), ("B", "p4"))
    assert by_rn(live, 25 / 3)["B"]["together_with"] is None
    live = LiveTrains(lambda: schedule(every_10), paths=PATHS, publish_claims=True)
    run(live, [(5, [("A", "p3"), ("B", "p2")]), (6, [("A", "p4"), ("B", "p3")]),
               (7, [("A", "p4"), ("B", "p3")]), (9, [("A", "p4"), ("B", "p4")])])  # fmt: skip
    assert by_rn(live, 9)["B"]["together_with"] is None  # 2:00 apart at p3: not under 2:00


def test_the_time_to_the_train_ahead_is_ctas_own_else_since_it_left():
    live = LiveTrains(paths=PATHS)
    live.stations = {"s3": {"at": 0, "eta": [
        {"rn": "A", "rt": "red", "arr": 1_000, "sch": False},
        {"rn": "B", "rt": "red", "arr": 1_600, "sch": False},
    ]}}  # fmt: skip
    a = {"rt": "red", "rn": "A", "next": "s3", "arr": 990}
    b = {"rt": "red", "rn": "B", "next": "s2", "arr": 1_300}
    assert live.ahead_s(b, a, 900) == 600  # both due at s3 (A's next): CTA's one answer there
    live.stations = {}
    assert live.ahead_s(b, a, 900) == 400  # no answer: B due at its next after A left it at 900
    assert live.ahead_s(b, None, None) is None


# ---- real cases (Oct 11, replayed from the recorded positions): one per rule ----
def cdt(hms: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-11T{hms}-05:00")


def replay(live: LiveTrains, rt: str, start: str, end: str, where) -> None:
    """Positions every 20 s, start to end (Chicago); where(t) -> [(rn, platform, dest)]."""
    t = cdt(start)
    while t <= cdt(end):
        live.trains = [{"rt": rt, "rn": rn, "stop": q, "dest": d} for rn, q, d in where(t)]
        live.passed(int(t.timestamp()))
        t += timedelta(seconds=20)


def test_real_long_wait_red_line_at_monroe_toward_howard():
    # Oct 11: 941 left Monroe (northbound platform 30211) at 21:25:29; 942 was next for it from
    # 21:43:29. At 21:43:49 riders there had waited 18:20, with trains timetabled every 8:34 there
    # that hour and two due since: over twice the schedule and the minute's margin (17:08 + 1:00)
    times = ["20:34:30", "20:42:00", "20:49:30", "20:57:00", "21:04:30", "21:13:30", "21:23:30",
             "21:33:30", "21:43:30", "21:53:30", "22:03:30"]  # fmt: skip
    live = LiveTrains(lambda: schedule([("30211", cdt(h)) for h in times]), publish_claims=True)

    def where(t):
        return [("941", "30211" if t < cdt("21:25:29") else "30289", "Howard"),
                ("942", "30109" if t < cdt("21:43:29") else "30211", "Howard")]  # fmt: skip

    replay(live, "red", "21:24:49", "21:43:49", where)
    live.claims(cdt("21:43:49"))
    t942 = {t["rn"]: t for t in live.trains}["942"]
    assert (t942["gap_s"], t942["long_wait"]) == (1100, True)
    assert t942["last_pass"] == int(cdt("21:25:29").timestamp())


def test_real_together_green_line_at_garfield_toward_harlem():
    # Oct 11: Green trains from both branches leave Garfield northbound (30099) every 12 min
    # together on a Sunday night; 017 (from Ashland/63rd) left at 22:01:09 and 018 (from Cottage
    # Grove) at 22:03:29: 2:20 apart, under a quarter of the schedule less the margin (2:30)
    times = ["20:52:30", "21:04:30", "21:16:30", "21:28:30", "21:40:30", "21:52:30", "22:04:30"]
    live = LiveTrains(
        lambda: schedule([("30099", cdt(h)) for h in times], rt="g"), publish_claims=True
    )

    def where(t):
        return [("017", "30099" if t < cdt("22:01:09") else "30024", "Harlem/Lake"),
                ("018", "30218" if t < cdt("22:00:09") else "30099" if t < cdt("22:03:29")
                 else "30024", "Harlem/Lake")]  # fmt: skip

    replay(live, "g", "21:59:49", "22:03:49", where)
    live.claims(cdt("22:03:49"))
    t018 = {t["rn"]: t for t in live.trains}["018"]
    assert (t018["together_with"], t018["together_s"]) == ("g:017", 140)


def test_claims_stay_unpublished_until_graded_but_the_measurements_go_out():
    every_8 = [("p2", T - timedelta(minutes=60) + timedelta(minutes=8 * i)) for i in range(15)]
    live = LiveTrains(lambda: schedule(every_8), paths=PATHS)  # as the poller makes it
    run(live, [(10, [("A", "p2")]), (30, [("A", "p3"), ("B", "p2")])])
    b = by_rn(live, 30)["B"]
    assert b["gap_s"] == 19 * 60 + 40  # measured, published
    assert (b["long_wait"], b["together_with"]) == (False, None)  # the rule fires; not published
