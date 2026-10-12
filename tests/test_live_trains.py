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
