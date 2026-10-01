import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ghost_ledger.live import (
    CHICAGO,
    STALE,
    TRAIL,
    FileSink,
    LiveBoard,
    load_schedule,
    pattern_shape,
    pattern_stop_geo,
    pattern_stop_ids,
    pattern_stops,
    publish,
)

T0 = datetime(2026, 9, 26, 22, 0, tzinfo=UTC)


def pos(vid, pdist, t, pid=1599, rt="79", **kw):
    return {"vid": vid, "rt": rt, "pid": pid, "pdist": pdist, "lat": 41.75, "lon": -87.65,
            "des": "Lakefront", "dly": False, "observed_at": t, **kw}  # fmt: skip


def by_vid(snapshot):
    return {b["vid"]: b for b in snapshot["vehicles"]}


def test_buses_that_left_the_feed_drop_out_and_old_trail_is_forgotten():
    board = LiveBoard()
    board.update([pos("A", 0, T0), pos("B", 0, T0)], T0)
    later = T0 + TRAIL + timedelta(minutes=5)
    board.update([pos("A", 100, later)], later)
    assert set(by_vid(board.snapshot(later))) == {"A"}
    assert "B" not in board.trails and len(board.trails["A"]) == 1


def test_publish_writes_the_snapshot_and_never_raises(tmp_path, monkeypatch):
    # build: CI waits for it to equal the commit it built (the deploy check seen from outside)
    monkeypatch.setenv("GIT_SHA", "abc123")
    path = tmp_path / "live" / "vehicles.json"
    board = LiveBoard()
    publish(board, FileSink(str(path)), [pos("A", 0, T0)], T0)
    snap = json.loads(path.read_text())
    v = snap["vehicles"][0]
    assert v["vid"] == "A" and v["t"] == int(T0.timestamp()) and set(v) >= {"hdg", "dir", "des"}
    assert snap["build"] == "abc123"

    class Broken:
        def write(self, snapshot):
            raise OSError("S3 down")

    publish(board, Broken(), [pos("A", 10, T0)], T0)  # logged, not raised


def test_buses_repeating_a_stale_report_are_left_off_the_map():
    board = LiveBoard()
    now = T0 + timedelta(minutes=10)
    board.update([pos("A", 5000, now), pos("B", 3000, T0)], now)  # B's report is 10 min old
    assert set(by_vid(board.snapshot(now))) == {"A"}


def test_each_bus_names_its_nearest_stop():
    from ghost_ledger.live import pattern_stops

    ptr = {"pt": [
        {"typ": "S", "pdist": 0, "stpnm": "Clark & Harrison"},
        {"typ": "W", "pdist": 500},  # a waypoint, not a stop
        {"typ": "S", "pdist": 2600, "stpnm": "Clark & Belmont"},
        {"typ": "S", "pdist": 5200, "stpnm": "Clark & Diversey"},
    ]}  # fmt: skip
    board = LiveBoard()
    board.stops = {1599: pattern_stops(ptr["pt"])}
    board.update([pos("A", 3000, T0), pos("B", 4800, T0), pos("C", 100, T0, pid=77)], T0)
    snap = by_vid(board.snapshot(T0))
    assert snap["A"]["near"] == "Clark & Belmont"  # 400 ft past it, 2,200 ft short of Diversey
    assert snap["B"]["near"] == "Clark & Diversey"
    assert snap["C"]["near"] is None  # pattern without stops loaded


# ---- stop-based waits (stop_gap_s): any bus of the route and direction at the next stop ----
# Route 79 eastbound, made-up patterns: MAIN has stops s0..s20 every 1,000 ft; SHORT (a short
# turn) starts at MAIN's s4 and ends at s12, sharing those stop ids; WEST runs back and
# shares only the terminal stop s20.
MAIN, SHORT, WEST = 990001, 990002, 990003
DAY = datetime(2026, 9, 26).date()


def stop_board():
    board = LiveBoard()
    main = [(1000.0 * k, f"s{k}", f"Stop {k}") for k in range(21)]
    short = [(1000.0 * (k - 4), f"s{k}", f"Stop {k}") for k in range(4, 13)]
    west = [(0.0, "s20", "Stop 20"), (5000.0, "w1", "West 1")]
    for pid, stops, rtdir in (
        (MAIN, main, "Eastbound"),
        (SHORT, short, "Eastbound"),
        (WEST, west, "Westbound"),
    ):
        board.stop_ids[pid] = stops
        board.routes[pid] = "79"
        board.dirs[pid] = rtdir
        board.lengths[pid] = stops[-1][0]
    return board


def trip(vid, pdist, minute, pid=MAIN, stst=36000, **kw):
    return pos(vid, pdist, T0 + timedelta(minutes=minute), pid=pid, stsd=DAY, stst=stst, **kw)


def sweep(board, minute, *positions):
    board.update(list(positions), T0 + timedelta(minutes=minute))


def test_a_short_turn_bus_that_passed_the_next_stop_counts():
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 2000 + 1000 * m, m)]  # main pattern, ahead: at s7 (7,000 ft) at m=5
        if m >= 3:
            buses.append(trip("S", 1000 * (m - 3), m, pid=SHORT))  # short turn: at s7 at m=6
        if m >= 7:
            buses.append(trip("B", 6600 - 600 * (10 - m), m))  # has driven 2,400 ft; short of s7
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["B"]
    assert (b["next_stpid"], b["next_stop"]) == ("s7", "Stop 7")
    assert b["stop_gap_s"] == 4 * 60 and b["last_by"] == "S"  # S passed s7 at m=6


def test_the_opposite_direction_at_a_shared_terminal_stop_does_not_count():
    board = stop_board()
    for m in range(0, 4):
        sweep(board, m, trip("W", 1000 * m - 500, m, pid=WEST))  # westbound: leaves s20 (its 0 ft)
    passes = board.passes()
    assert [v for _, v in passes[("79", "Westbound", "s20")]] == ["W"]
    assert ("79", "Eastbound", "s20") not in passes  # eastbound waits at s20 never see it


def test_a_bus_laying_over_at_its_first_stop_passes_it_when_it_leaves():
    board = stop_board()
    # L arrives at its first stop and creeps back and forth for 10 minutes, then leaves
    creep = [0, 30, 0, 30, 10, 40, 0, 30, 20, 30, 0]
    for m, d in enumerate(creep):
        sweep(board, m, trip("L", d, m))
    for m, d in ((11, 600), (12, 1600), (13, 2600)):
        sweep(board, m, trip("L", d, m))
    passes = board.passes()[("79", "Eastbound", "s0")]
    assert passes == [(T0 + timedelta(minutes=10), "L")]  # its last crossing before leaving


def test_no_start_pass_while_the_bus_is_still_laying_over():
    board = stop_board()
    for m, d in enumerate([0, 30, 0, 30]):
        sweep(board, m, trip("L", d, m))
    assert ("79", "Eastbound", "s0") not in board.passes()
    assert by_vid(board.snapshot(T0 + timedelta(minutes=3)))["L"]["trip"] == "starting"


def test_across_a_blackout_stops_are_passed_at_the_latest_possible_time():
    board = stop_board()
    sweep(board, 0, trip("A", 3000, 0))
    sweep(board, 1, trip("A", 5000, 1))
    sweep(
        board, 13, trip("A", 9000, 13)
    )  # dark for 12 minutes: passed s6..s8 by m=13 at the latest
    passes = board.passes()
    assert passes[("79", "Eastbound", "s4")] == [(T0 + timedelta(minutes=0.5), "A")]  # interpolated
    assert passes[("79", "Eastbound", "s7")] == [(T0 + timedelta(minutes=13), "A")]


def test_a_dark_short_turn_bus_short_of_the_stop_makes_the_wait_unknown():
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 3000 + 1000 * m, m)]  # passed s7 at m=4
        if 3 <= m <= 5:
            buses.append(trip("S", 1000 * (m - 3), m, pid=SHORT))  # dark from m=6 at MAIN 6,000 ft
        if m >= 5:
            buses.append(trip("B", 5000 + 300 * (m - 5), m))  # behind S when S went dark
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["B"]
    assert b["next_stpid"] == "s7" and b["stop_gap_s"] is None  # S may have passed s7 since


def test_a_dark_bus_behind_this_one_changes_nothing():
    # D went dark after A's pass, within reach of s7 and still in doubt, but behind B: anything
    # it passed since, B passes first. B's wait stands.
    board = stop_board()
    for m in range(0, 10):
        buses = [trip("A", 3000 + 1000 * m, m)]  # passed s7 (7,000 ft) at m=4
        if 1 <= m <= 5:
            buses.append(trip("D", 3000 + 500 * (m - 1), m, stst=36600))  # dark at 5,000 ft
        if m >= 4:
            buses.append(trip("B", 6000 + 100 * (m - 4), m, stst=37200))  # ahead of D
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=9)))["B"]
    assert b["next_stpid"] == "s7" and b["stop_gap_s"] == 5 * 60 and b["last_by"] == "A"


def test_after_a_jump_back_the_stops_beyond_the_furthest_point_still_pass():
    board = stop_board()
    for m, d in enumerate([5000, 6500, 0, 7400]):  # a glitch to 0, then on past s7
        sweep(board, m, trip("A", d, m))
    passes = board.passes()
    assert ("79", "Eastbound", "s6") in passes and len(passes[("79", "Eastbound", "s6")]) == 1
    assert passes[("79", "Eastbound", "s7")] == [(T0 + timedelta(minutes=2 + 7000 / 7400), "A")]


def test_going_backwards_passes_nothing_and_a_new_trip_starts_afresh():
    board = stop_board()
    for m, d in enumerate([6000, 7500, 6800, 7200]):  # jumps back at m=2: no step until past 7,500
        sweep(board, m, trip("A", d, m))
    assert [t for t, _ in board.passes()[("79", "Eastbound", "s7")]] == [
        T0 + timedelta(minutes=2 / 3)
    ]
    for m, d in ((5, 0), (6, 3000), (7, 6500), (8, 7500)):  # the next trip, from the start
        sweep(board, m, trip("A", d, m, stst=40000))
    assert len(board.passes()[("79", "Eastbound", "s7")]) == 2


def test_a_stop_listed_twice_is_passed_twice_and_the_next_one_is_ahead():
    board = stop_board()
    board.stop_ids[MAIN] = [
        (0.0, "s0", "Stop 0"),
        (1000.0, "loop", "Loop"),
        (3000.0, "loop", "Loop"),
        (5000.0, "s5", "Stop 5"),
    ]
    for m in range(0, 5):
        buses = [trip("A", 1200 * m, m)]
        if m >= 3:
            buses.append(trip("B", {3: 400, 4: 1500}[m], m))
        sweep(board, m, *buses)
    assert len([v for _, v in board.passes()[("79", "Eastbound", "loop")] if v == "A"]) == 2
    sweep(board, 5, trip("A", 6000, 5), trip("B", 2000, 5))
    b = by_vid(board.snapshot(T0 + timedelta(minutes=5)))["B"]
    assert b["next_stpid"] == "loop" and b["stop_gap_s"] == round((5 - 3000 / 1200) * 60)


def test_a_seeded_board_gives_the_same_stop_waits_as_one_that_ran_all_along():
    running = stop_board()
    rows = []
    for m in range(0, 11):
        buses = [trip("A", 2000 + 1000 * m, m)]
        if m >= 3:
            buses.append(trip("S", 1000 * (m - 3), m, pid=SHORT))
        if m >= 7:
            buses.append(trip("B", 6600 - 600 * (10 - m), m))
        sweep(running, m, *buses)
        rows += [
            (b["vid"], b["observed_at"], b["pid"], b["pdist"], b["stsd"], b["stst"]) for b in buses
        ]
    restarted = stop_board()
    restarted.seed(sorted(rows, key=lambda r: (r[0], r[1])))
    now = T0 + timedelta(minutes=10)
    restarted.update(
        [trip("A", 12000, 10), trip("S", 7000, 10, pid=SHORT), trip("B", 6600, 10)], now
    )
    keys = ("stop_gap_s", "last_by", "next_stpid")
    assert [{k: v[k] for k in keys} for v in running.snapshot(now)["vehicles"]] == [
        {k: v[k] for k in keys} for v in restarted.snapshot(now)["vehicles"]
    ]


def test_buses_at_a_terminal_get_no_stop_wait():
    # T has left its start (under way) but is still within TERMINAL_FT of it
    board = stop_board()
    for m in range(0, 4):
        buses = [trip("A", 500 + 1000 * m, m)]  # passed s1 at m=0.5
        if m >= 1:
            buses.append(trip("T", 300 * (m - 1), m, stst=37000))
        sweep(board, m, *buses)
    t = by_vid(board.snapshot(T0 + timedelta(minutes=3)))["T"]
    assert t["at_terminal"] and t["stop_gap_s"] is None and t["next_stpid"] is None


def test_a_trip_that_starts_mid_pattern_passes_its_first_stops_when_it_leaves():
    # Route 146, Sep 28: buses lay over ~2,500 ft into the pattern, and a new trip's first
    # report jumps ahead (4,000 ft) before settling back. The nightly's furthest-point rule
    # then never counted the departure past s3 (3,000 ft), and the bus behind saw 50+ minutes.
    board = stop_board()
    for m, d in enumerate([4000, 2500, 2500, 2480, 2500, 2700, 3400, 4600, 5600]):
        sweep(board, m, trip("L", d, m))
    passes = board.passes()
    assert [t for t, _ in passes[("79", "Eastbound", "s3")]] == [T0 + timedelta(minutes=5 + 3 / 7)]
    assert [t for t, _ in passes[("79", "Eastbound", "s5")]] == [T0 + timedelta(minutes=7.4)]


def test_a_bus_that_has_not_left_its_start_gets_no_stop_wait():
    board = stop_board()
    for m in range(0, 6):
        sweep(
            board, m, trip("A", 3000 + 1000 * m, m), trip("L", 2500 - 20 * (m % 2), m, stst=40000)
        )
    l_bus = by_vid(board.snapshot(T0 + timedelta(minutes=5)))["L"]
    assert (
        l_bus["stop_gap_s"] is None and l_bus["at_terminal"] is False
    )  # 2,500 ft in, still laying over


def test_an_overdue_bus_just_short_of_the_stop_makes_the_wait_unknown():
    # D's report is a minute late (not yet dark on the map) and it was 200 ft short of s7 at
    # 500 ft/min: it has very likely passed s7 unseen, so B's wait there isn't claimed.
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 3000 + 2000 * m, m)]  # passed s7 at m=2
        if m <= 9:
            buses.append(trip("D", 2300 + 500 * m, m))  # 6,800 ft at m=9, then no report
        if m >= 7:
            buses.append(trip("B", 5000 + 600 * (m - 7), m))  # 6,200 ft at m=9, 6,800 at m=10
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["B"]
    assert b["next_stpid"] == "s7" and b["stop_gap_s"] is None


def test_an_overdue_bus_that_could_not_have_reached_the_stop_changes_nothing():
    # D, ahead of B, crawled 100 ft/min and was 700 ft short of s7 a minute ago: even at a
    # generous pace (600 ft/min) it can't be there yet, so B's long wait at s7 stands.
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 3000 + 2000 * m, m)]  # passed s7 at m=2
        if m <= 9:
            buses.append(trip("D", {8: 6200, 9: 6300}.get(m, 4000 + 250 * m), m))
        if m >= 7:
            buses.append(trip("B", 5000 + 600 * (m - 7), m))  # 6,200 ft at m=9, 6,800 at m=10
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["B"]
    assert b["next_stpid"] == "s7" and b["stop_gap_s"] == 8 * 60 and b["last_by"] == "A"


# ---- the live schedule (sched_s, sched_due) ----
def schedule_file(tmp_path, departures):
    """A live schedule file as the nightly writes it (transform/live_schedule.py)."""
    import gzip
    import importlib.util
    import json as json_

    spec = importlib.util.spec_from_file_location("live_schedule", "transform/live_schedule.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [
        ("79", stop, T0.astimezone(CHICAGO).date(), int(t.timestamp())) for stop, t in departures
    ]
    path = tmp_path / "stop_departures.json"
    path.write_bytes(gzip.compress(json_.dumps(mod.build(rows)).encode()))
    return path


def test_the_schedule_file_round_trips_from_the_nightly_to_the_feed(tmp_path):
    every_10 = [("s7", T0 - timedelta(minutes=m)) for m in range(0, 120, 10)]
    schedule = load_schedule(str(schedule_file(tmp_path, every_10)))
    now = T0 + timedelta(minutes=5)
    # six departures in the past hour: a 10-minute gap; since 25 min ago, two were due
    assert schedule.judge("79", "s7", now, now - timedelta(minutes=25)) == (600, 2)
    assert schedule.judge("79", "s8", now, None) == (None, None)  # no departures there
    # after a service break (54A-like: nothing midday): one bus due in 4 hours is not "buses
    # didn't come"
    gap = [("s9", T0 - timedelta(hours=h)) for h in (5, 4.5, 4)] + [("s9", T0)]
    later = load_schedule(str(schedule_file(tmp_path, gap)))
    assert later.judge("79", "s9", now, now - timedelta(hours=4)) == (3600, 1)


def test_the_feed_carries_the_schedule_at_the_next_stop(tmp_path):
    board = stop_board()
    board.schedule = load_schedule(
        str(schedule_file(tmp_path, [("s7", T0 + timedelta(minutes=m)) for m in range(-60, 11, 5)]))
    )
    for m in range(0, 11):
        buses = [trip("A", 3000 + 1000 * m, m)]  # passed s7 at m=4
        if m >= 7:
            buses.append(trip("B", 6600 - 600 * (10 - m), m))
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["B"]
    assert b["stop_gap_s"] == 6 * 60
    assert b["sched_s"] == 300 and b["sched_due"] == 2  # due at m=5 and m=10; A came at m=4


def test_a_trip_first_seen_just_past_a_stop_passed_it_by_then():
    # Route 3, Sep 28: a trip's first report came 118 ft past King & 83rd. It drove past (or
    # pulled out there) just before: passed at the latest by that first report.
    board = stop_board()
    for m, d in enumerate([7118, 7600, 8300, 9100]):
        sweep(board, m, trip("A", d, m))
    assert board.passes()[("79", "Eastbound", "s7")] == [(T0, "A")]
    far = stop_board()
    for m, d in enumerate([7700, 8200, 9000]):  # first seen 700 ft on: can't say it served s7
        sweep(far, m, trip("A", d, m))
    assert ("79", "Eastbound", "s7") not in far.passes()


def test_a_bus_standing_at_a_stop_passes_it_when_it_leaves():
    # #79 at Spaulding, Sep 28: stood ~10 ft past the stop for 11 minutes
    board = stop_board()
    for m, d in enumerate([5500, 6400, 7010, 7010, 7020, 7015, 7800, 8600]):
        sweep(board, m, trip("A", d, m))
    assert board.passes()[("79", "Eastbound", "s7")] == [
        (T0 + timedelta(minutes=5), "A")
    ]  # left at m=5


def test_a_bus_reassigned_to_another_trip_while_driving_keeps_its_passes():
    # #66, Sep 28: 8125 laid over at the terminal on one trip, then was switched to another
    # trip as it drove off; the stops it passed in between belong to one continuous run.
    board = stop_board()
    for m in range(0, 4):
        sweep(board, m, trip("A", 0, m, stst=76830))
    sweep(board, 4, trip("A", 5113, 4, stst=75030))
    assert [v for _, v in board.passes()[("79", "Eastbound", "s4")]] == ["A"]


# --- The claims decided in the poller (module docstring): serving passes, behind, together ---


def test_a_short_turn_arriving_at_its_last_stop_does_not_serve_it():
    # #66 Sep 29: short turns end at Pulaski. S ends at s12 at m=8; the last bus that served
    # s12 (for riders going on) was A at m=2
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 10000 + 1000 * m, m)]
        buses.append(trip("S", min(8000, 1000 * m), m, pid=SHORT))
        if m >= 9:
            buses.append(trip("B", 11500 - 500 * (10 - m), m))
        sweep(board, m, *buses)
    assert ("79", "Eastbound", "s12") in board.passes()
    assert [v for _, v in board.passes()[("79", "Eastbound", "s12")]] == ["A"]
    b = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["B"]
    assert (b["next_stpid"], b["last_by"], b["stop_gap_s"]) == ("s12", "A", 8 * 60)


def test_a_bus_with_only_its_last_stop_left_is_finishing_and_waits_on_no_stop():
    board = stop_board()
    for m in range(0, 4):
        sweep(board, m, trip("W", 1000 * m, m, pid=WEST))  # its last stop, w1, is at 5,000 ft
    w = by_vid(board.snapshot(T0 + timedelta(minutes=3)))["W"]
    assert w["next_stop"] is None and w["stop_gap_s"] is None and not w["at_terminal"]
    assert w["trip"] == "finishing"


def test_the_bus_behind_can_be_a_short_turn_that_stops_here():
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 1000 * m - 500, m)]  # main: passed s7 at m=7.5, heading for s10
        if m >= 5:
            buses.append(trip("S", 500 * (m - 5), m, pid=SHORT))  # short turn: heading for s7
        sweep(board, m, *buses)
    a = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["A"]
    assert a["next_stpid"] == "s10"
    assert (a["behind_vid"], a["behind_stops"]) == ("S", 3)  # S heads for s7, A for s10


def test_a_short_turn_that_ends_before_this_bus_s_stop_is_not_the_bus_behind():
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 1000 * m + 4500, m)]  # main, heading for s15: passed s6 at m=1.5
        if m >= 5:
            buses.append(trip("S", 500 * (m - 5), m, pid=SHORT))  # nearer, but ends at s12
        if m >= 4:
            buses.append(trip("B", 1000 * (m - 4) - 200, m))  # main, heading for s6
        sweep(board, m, *buses)
    a = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["A"]
    assert a["next_stpid"] == "s15"
    assert (a["behind_vid"], a["behind_stops"]) == ("B", 9)  # B heads for s6, A for s15


def test_a_bus_laying_over_at_the_start_is_the_bus_behind():
    board = stop_board()
    for m, d in zip(range(6, 11), (0, 800, 1800, 2800, 3800), strict=True):
        sweep(board, m, trip("A", d, m), trip("L", 30 * ((m + 1) % 2), m, stst=37000))
    a = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["A"]
    assert (a["behind_vid"], a["behind_stops"]) == ("L", 4)  # L waits at s0; A heads for s4


def test_buses_together_long_ago_on_a_stretch_without_stops_are_not_claimed(tmp_path):
    # J14 on Lake Shore Drive: 7 miles without a stop. Two buses left s20 a minute apart, but
    # that was over TOGETHER_FRESH ago: says nothing about now
    board = stop_board()
    every_10 = [("s20", T0 + timedelta(minutes=k)) for k in range(-60, 30, 10)]
    board.schedule = load_schedule(str(schedule_file(tmp_path, every_10)))
    for m in range(0, 14):
        buses = [trip("F", 300 * m, m, pid=WEST)]  # leaves s20 at m=0-1, slowly
        buses.append(trip("B", max(0, 300 * (m - 1)), m, pid=WEST, stst=36060))  # a minute later
        sweep(board, m, *buses)
    early = by_vid(board.snapshot(T0 + timedelta(minutes=5)))
    late = by_vid(board.snapshot(T0 + timedelta(minutes=13)))
    assert early["B"]["together_with"] == "F"
    assert late["B"]["together_with"] is None


def test_no_long_wait_when_only_one_bus_was_due_since_the_last(tmp_path):
    # A long gap, but the schedule thinned out: one departure due since A came
    board = stop_board()
    deps = [("s7", T0 + timedelta(minutes=k)) for k in range(-60, 5, 5)] + [
        ("s7", T0 + timedelta(minutes=10))
    ]
    board.schedule = load_schedule(str(schedule_file(tmp_path, deps)))
    for m in range(0, 19):
        buses = [trip("A", 3000 + 1000 * m, m)]  # passed s7 at m=4
        if m >= 15:
            buses.append(trip("B", 6000 + 200 * (m - 15), m))
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=18)))["B"]
    assert b["stop_gap_s"] == 14 * 60 and b["sched_due"] == 1
    assert b["stop_gap_s"] > 2 * b["sched_s"] + 60  # long enough, margin included
    assert b["long_wait"] is False  # but only one bus was due


def test_a_bus_ahead_is_never_the_bus_behind():
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 1000 * m - 500, m), trip("C", 1000 * m + 2500, m)]
        if m >= 3:
            buses.append(trip("B", 1000 * (m - 3) - 500, m))  # heading for s7: A there at 7.5
        if m >= 7:
            buses.append(trip("D", 1000 * (m - 7) - 500, m))  # further back, heading for s3
        sweep(board, m, *buses)
    a = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["A"]
    # C, heading for s13, is ahead of A (heading for s10); B is the nearest bus behind, not D
    assert (a["behind_vid"], a["behind_stops"]) == ("B", 3)


def test_not_together_with_a_bus_that_has_since_turned_around_or_gone(tmp_path):
    # #66 Sep 29: 8174 passed the last stop just ahead of 8300, then reached Austin and started
    # back the other way: they are no longer on the road together
    board = stop_board()
    every_10 = [(s, T0 + timedelta(minutes=k)) for s in ("s7", "s8") for k in range(-60, 30, 10)]
    board.schedule = load_schedule(str(schedule_file(tmp_path, every_10)))
    for m in range(0, 10):
        buses = [trip("A", 1000 * m - 1100, m)]  # s8 at m=9.1
        if 4 <= m < 9:
            buses.append(trip("S", 1000 * (m - 4), m, pid=SHORT))  # s8 at m=8, then gone
        sweep(board, m, *buses)
    assert by_vid(board.snapshot(T0 + timedelta(minutes=9.5)))["A"]["together_with"] is None


def test_a_report_off_its_route_is_left_out():
    # The GPS must sit on the pattern at the reported pdist. #3 8405, Sep 29: dark 32 minutes
    # at 79th St, then back downtown with 79th's pdist; #66 8677: assigned its next trip while
    # 5 km away, its pdist creeping along the pattern
    board = stop_board()
    board.shapes[MAIN] = [(0.0, 41.75, -87.65), (20000.0, 41.80, -87.65)]  # due north
    on = lambda d: 41.75 + 0.05 * d / 20000  # noqa: E731 - the pattern's latitude at d feet
    sweep(board, 0, trip("B", 2200, 0, lat=on(2200)))
    sweep(board, 1, trip("B", 2800, 1, lat=on(2800)))  # under way, heading for s3
    for m in range(2, 32):  # A passes s3 at m=2.5: a wait B would measure, were it trusted
        sweep(board, m, trip("A", 500 + 1000 * m, m, lat=on(500 + 1000 * m)))
    sweep(board, 32, trip("B", 2800, 32, lat=on(2800) + 0.1))  # 11 km away, same pdist
    assert [d for _, _, d, _ in board.trails["B"]] == [2200, 2800]
    b = by_vid(board.snapshot(T0 + timedelta(minutes=32)))["B"]
    assert b["lat"] == round(on(2800) + 0.1, 5)  # on the map where it is: not a claim
    assert b["next_stop"] is None and b["stop_gap_s"] is None and b["trip"] is None
    # laying over at a terminal it is still at the terminal, off route or not
    sweep(board, 32.5, trip("T", 300, 32.5, lat=on(300) + 0.1))
    assert by_vid(board.snapshot(T0 + timedelta(minutes=32.5)))["T"]["at_terminal"] is True
    sweep(board, 33, trip("B", 17000, 33, lat=on(17000)))
    assert [d for _, _, d, _ in board.trails["B"]] == [2200, 2800, 17000]
    sweep(board, 34, trip("B", 17100, 34, lat=on(17100) + 0.002))  # 220 m off: GPS noise, taken
    assert len(board.trails["B"]) == 4
    # restored trails are checked too
    seeded = stop_board()
    seeded.shapes[MAIN] = board.shapes[MAIN]
    t1 = T0 + timedelta(minutes=1)
    off, ok = on(5000) + 0.1, on(6000)
    seeded.seed(
        [
            ("C", T0, MAIN, 5000.0, DAY, 36000, off, -87.65),
            ("C", t1, MAIN, 6000.0, DAY, 36000, ok, -87.65),
        ]
    )
    assert [d for _, _, d, _ in seeded.trails["C"]] == [6000]


def test_of_two_buses_heading_for_one_stop_the_one_nearer_it_is_ahead():
    # #147 Sep 29: 4014 downtown and 4204 near Foster both had Foster next (5 miles of Lake
    # Shore Drive without a stop); each was listed as "right behind" the other
    board = stop_board()
    board.stop_ids[MAIN] = [(0.0, "s0", "Stop 0"), (20000.0, "far", "Far"), (21000.0, "end", "End")]
    for m in range(0, 11):
        sweep(board, m, trip("A", 1500 + 1000 * m, m), trip("B", 3000 + 1000 * m, m))
    snap = by_vid(board.snapshot(T0 + timedelta(minutes=10)))
    assert snap["A"]["next_stpid"] == snap["B"]["next_stpid"] == "far"
    assert (snap["B"]["behind_vid"], snap["B"]["behind_stops"]) == ("A", 0)  # A is behind B
    assert snap["A"]["behind_vid"] is None  # B, nearer the stop, is ahead of A


# Real cases (tests/fixtures/live_<date>.json: the day's reports and CTA's own patterns, as
# Ingress holds them)
SEP30 = {
    case: doc
    for day in ("2026-09-28", "2026-09-29", "2026-09-30")
    for case, doc in json.loads(
        (Path(__file__).parent / "fixtures" / f"live_{day}.json").read_text()
    ).items()
    if case != "about"
}


def real_board(case):
    board = LiveBoard()
    for pid, pat in SEP30[case]["patterns"].items():
        pid = int(pid)
        board.routes[pid], board.dirs[pid], board.lengths[pid] = pat["rt"], pat["rtdir"], pat["ln"]
        board.stops[pid] = pattern_stops(pat["pt"])
        board.stop_ids[pid] = pattern_stop_ids(pat["pt"])
        board.stop_geo.update(pattern_stop_geo(pat["pt"]))
        board.shapes[pid] = pattern_shape(pat["pt"])
    return board


def real_sweeps(case, until, polled=lambda t: True, last_at_until=False):
    """Feed the case's reports to a board as the poller would, a sweep every 30 s (the API
    repeats each bus's latest report while its route's call succeeds). polled(t): whether the
    route's call succeeded at t."""
    board, rows = real_board(case), SEP30[case]["positions"]
    rows = [(datetime.fromisoformat(r[1]), r) for r in rows]
    t, latest, i = rows[0][0], {}, 0
    rt = rows[0][1][2]
    grid = []
    while t < until:
        grid.append(t)
        t += timedelta(seconds=30)
    for t in [*grid, *([until] if last_at_until else [])]:  # last_at_until: one more at `until`
        new = []
        while i < len(rows) and rows[i][0] <= t:
            obs, (vid, _, r, pid, pdist, stsd, stst, lat, lon) = rows[i]
            latest[vid] = {"vid": vid, "rt": r, "pid": pid, "pdist": pdist, "observed_at": obs,
                           "stsd": stsd, "stst": stst, "lat": lat, "lon": lon}  # fmt: skip
            new.append(latest[vid])
            i += 1
        ok = polled(t)
        repeats = [p for v, p in latest.items() if p not in new and t - p["observed_at"] <= STALE]
        board.update(repeats + new if ok else [], t, {rt} if ok else set())
    return board


def test_a_bus_starting_its_trip_is_on_route_though_cta_puts_every_waypoint_at_0():
    # #147 4155, Sep 30: laying over 258 ft into its trip at Congress Plaza & Michigan. CTA sends
    # every waypoint with pdist 0; kept, they all sat at the start and the report was checked
    # against one far north: "off route", so the trip never passed its first stop.
    pat = SEP30["rt147_start"]["patterns"]["4524"]
    assert {p["pdist"] for p in pat["pt"] if p["typ"] == "W"} == {0.0}
    board = real_sweeps("rt147_start", datetime(2026, 9, 30, 11, 2, tzinfo=UTC))
    assert board.trails["4155"][0][2] == 258  # the layover reports count
    passed = {board.stop_ids[4524][0][1]: None} | {
        sid: t for (_, _, sid), ps in board.passes().items() for t, v in ps if v == "4155"
    }
    first = board.stop_ids[4524][0]
    assert first[2] == "Congress Plaza & Michigan" and passed[first[1]] is not None


def test_no_wait_is_claimed_across_minutes_the_route_went_unseen():
    # #22 Southbound, Sep 30: CTA's API unreachable 12:41-13:02 CDT. 1522 was 1,142 ft short of
    # Clark & Van Buren at 12:40:53 and came back at 13:01:33 on its return trip: it passed the
    # stop in the dark and never reported it. At 13:05 the feed told riders ahead of 8303 "no
    # bus since 12:35" (1780), a 30-min wait that wasn't.
    lo, hi = (datetime.fromisoformat(x) for x in SEP30["rt22_blackout"]["unpolled_utc"])
    at = datetime(2026, 9, 30, 18, 5, 7, tzinfo=UTC)
    seen = lambda t: not lo <= t < hi  # noqa: E731
    board = real_sweeps("rt22_blackout", at, seen)
    b = by_vid(board.snapshot(at))["8303"]
    assert b["next_stop"] == "Clark & Van Buren"
    assert (b["stop_gap_s"], b["long_wait"]) == (None, False)  # can't tell
    assert any(v == "1780" for _, v in board.passes()[("22", "Southbound", b["next_stpid"])])
    # while nothing comes through, the feed says so instead of "0 buses on the road"
    mid = datetime(2026, 9, 30, 17, 50, tzinfo=UTC)
    snap = real_sweeps("rt22_blackout", mid, seen).snapshot(mid)
    assert snap["no_data_since"] == "2026-09-30T17:40:35+00:00"  # the last sweep that got through
    assert snap["vehicles"] == []  # nothing fresh by 12:50
    assert board.snapshot(at)["no_data_since"] is None


def test_no_headway_is_claimed_until_both_buses_have_left_the_stop():
    # X49 8077, Sep 30: stood at one stop for 49 minutes (08:05-08:54 CDT). Its pass there kept
    # moving to its latest report, so each bus that went by (1505, 1544) was "together" with it.
    class EveryTen:
        def judge(self, rt, stpid, now, last_pass):
            return 600, 3

    board = real_board("x49_standing")
    board.schedule = EveryTen()
    rows = [(datetime.fromisoformat(r[1]), r) for r in SEP30["x49_standing"]["positions"]]
    t, latest, i, claims = rows[0][0], {}, 0, []
    while t <= datetime(2026, 9, 30, 13, 15, tzinfo=UTC):
        while i < len(rows) and rows[i][0] <= t:
            obs, (vid, _, r, pid, pdist, stsd, stst, lat, lon) = rows[i]
            latest[vid] = {"vid": vid, "rt": r, "pid": pid, "pdist": pdist, "observed_at": obs,
                           "stsd": stsd, "stst": stst, "lat": lat, "lon": lon}  # fmt: skip
            i += 1
        board.update([p for p in latest.values() if t - p["observed_at"] <= STALE], t, {"X49"})
        claims += [
            (v["vid"], v["together_with"])
            for v in board.snapshot(t)["vehicles"]
            if v["together_with"]
        ]
        t += timedelta(seconds=30)
    assert not [c for c in claims if "8077" in c]


def test_a_bus_s_own_pass_on_its_last_lap_counts():
    # #171 Sep 28: only 8935 served Hyde Park & 55th (1518) northbound, every 30 min on its
    # loop. Its own passes were left out, so riders there were told 2.5 hours since another
    # bus; the last bus there was 8935 itself at 13:07.
    at = datetime(2026, 9, 28, 18, 46, tzinfo=UTC)
    b = by_vid(real_sweeps("rt171_loop", at).snapshot(at))["8935"]
    assert b["next_stpid"] == "1518"
    assert b["last_by"] == "8935" and 2200 < b["stop_gap_s"] < 2400  # since 13:07


def test_a_bus_moving_backwards_along_its_pattern_makes_the_stop_it_drove_past_unknown():
    # J14 Sep 28 morning: CTA labelled buses with the opposite direction. 4037 drove north up
    # Lake Shore Drive past 1100 S Columbus at 07:38 reporting the southbound pattern, its pdist
    # falling; with passes on forward progress only, northbound riders there were told at 07:40
    # "2 hours since the last bus" (4329, 05:41) in a 6-minute rush.
    at = datetime(2026, 9, 28, 12, 40, tzinfo=UTC)
    snap = by_vid(real_sweeps("j14_backwards", at).snapshot(at))
    b = snap["4309"]
    assert b["next_stpid"] == "14483" and b["stop_gap_s"] is None  # can't tell, not 2 hours
    assert snap["4307"]["next_stop"] is None  # running against its pattern: no claims about it


def test_a_bus_waiting_just_past_a_stop_serves_it_when_it_leaves():
    # #201 Sep 29: 8800 began its trip 1,056 ft into its pattern, eased back to 51 ft past
    # Howard & Custer and left from there at 12:49 (its GPS at the stop). By pdist it was never
    # short of the stop, so no pass: at 13:05 riders there were told 65 min since the last bus.
    for s in range(0, 90, 10):  # 8789 heads for Howard & Custer from its 13:04:42 report
        at = datetime(2026, 9, 29, 18, 4, 40, tzinfo=UTC) + timedelta(seconds=s)
        b = by_vid(real_sweeps("rt201_layover", at, last_at_until=True).snapshot(at))["8789"]
        if b["next_stpid"] == "3515":
            break
    assert b["next_stpid"] == "3515"
    assert b["last_by"] == "8800" and b["stop_gap_s"] < 1000  # since 12:49, not 12:00


def test_a_wait_just_over_twice_the_schedule_is_not_claimed_until_clearly_over(tmp_path):
    # Pass times are known to tens of seconds; Sep 28-30, most wrong long waits were claims
    # within 30 s of the line (the GPS referee). Every 5 min: the line is 10 min, claimed past 11.
    board = stop_board()
    every_5 = [("s7", T0 + timedelta(minutes=k)) for k in range(-60, 31, 5)]
    board.schedule = load_schedule(str(schedule_file(tmp_path, every_5)))
    for m in range(0, 16):
        buses = [trip("A", 3000 + 1000 * m, m)]  # passed s7 at m=4
        if m >= 13:
            buses.append(trip("B", 6000 + 100 * (m - 13), m))
        sweep(board, m, *buses)
    at = lambda minute: by_vid(board.snapshot(T0 + timedelta(minutes=minute)))["B"]  # noqa: E731
    assert (at(14.5)["stop_gap_s"], at(14.5)["long_wait"]) == (630, False)  # 10.5 min: too close
    assert (at(15.2)["stop_gap_s"], at(15.2)["long_wait"]) == (672, True)  # 11.2 min: claimed
    assert (at(15.2)["sched_s"], at(15.2)["sched_due"]) == (300, 3)  # due at m=5, 10 and 15


def test_together_only_clearly_inside_a_quarter_of_the_schedule(tmp_path):
    # every 10 min: a quarter is 150 s; pairs a few seconds either side of it flip (Sep 30)
    def headway_claimed(offset_ft, at_minute):
        board = stop_board()
        every_10 = [
            (s, T0 + timedelta(minutes=k)) for s in ("s7", "s8") for k in range(-60, 30, 10)
        ]
        board.schedule = load_schedule(str(schedule_file(tmp_path, every_10)))
        for m in range(0, at_minute + 1):
            buses = [trip("A", 1000 * m - offset_ft, m)]
            if m >= 4:
                buses.append(trip("S", 1000 * (m - 4), m, pid=SHORT))  # s8 at m=8
            sweep(board, m, *buses)
        return by_vid(board.snapshot(T0 + timedelta(minutes=at_minute)))

    snap = headway_claimed(1667, 10)  # A (main) at s8 at m=9.67: 100 s after S (a short turn)
    assert (snap["A"]["together_with"], snap["A"]["together_s"]) == ("S", 100)
    assert snap["S"]["together_with"] is None  # nothing just ahead of the leader
    assert headway_claimed(2167, 11)["A"]["together_with"] is None  # 130 s: too close to 150


def test_a_bus_running_backwards_casts_doubt_only_on_the_stops_it_drove_past():
    # Buses driving back to start their next trip run against their pattern on dozens of routes
    # a day: doubting the whole other direction hid ~370 real long waits (Sep 30)
    board = stop_board()
    board.stop_geo = {"near": (41.7501, -87.6501), "far": (41.80, -87.65)}
    sweep(board, 0, trip("X", 5000, 0, lat=41.752))
    sweep(board, 0.5, trip("X", 4000, 0.5, lat=41.748))  # 1,000 ft back, 440 m south
    assert board.latest["X"].get("off_route")  # no passes or claims while running backwards
    then, now = T0 - timedelta(minutes=5), T0 + timedelta(minutes=5)
    assert board.against("79", "Westbound", "near", then, now)  # drove right past it
    assert not board.against("79", "Westbound", "far", then, now)  # 5 km away
    assert not board.against("79", "Eastbound", "near", then, now)  # its own direction


def test_an_off_route_report_is_no_evidence_of_running_backwards():
    # #96 8777, Sep 28: its first report put it 1,219 ft past McCormick & Pratt (off route: the
    # GPS was at the stop); the next, on route, 334 ft past it. Read as running backwards, that
    # report was dropped, and the stop never passed: riders were told 62 min.
    board = real_sweeps("rt96_first_seen", datetime(2026, 9, 28, 20, 24, tzinfo=UTC))
    passed = [v for (_, _, sid), ps in board.passes().items() if sid == "14998" for _, v in ps]
    assert passed == ["8777"]


def test_an_untrusted_report_at_a_stop_counts_as_a_pass():
    # #147 4398, Sep 30: its pdist lagged its GPS by a kilometre (off route, untrusted), but its
    # GPS was at Michigan & Ontario at 14:52; at 15:05 riders there were told nobody had come
    # since 4361 at 14:41.
    at = datetime(2026, 9, 30, 20, 5, tzinfo=UTC)
    board = real_sweeps("rt147_lagging_pdist", at, last_at_until=True)
    heading = [v for v in board.snapshot(at)["vehicles"] if v["next_stpid"] == "1124"]
    assert heading and all(v["last_by"] == "4398" for v in heading)  # at 14:52:23
    assert all(12 * 60 < v["stop_gap_s"] < 13 * 60 for v in heading)
    assert any(v == "4361" for _, v in board.passes()[("147", "Northbound", "1124")])


def test_while_a_route_s_call_fails_its_buses_stay_and_its_waits_are_unknown():
    # A failed call isn't an empty route (buses stay, up to STALE), and after HOLE nothing about
    # it can be claimed; the same after a restart that missed minutes of reports.
    board = stop_board()
    for m in range(0, 4):
        board.update([trip("A", 5000 + 1000 * m, m), trip("B", 4000 + 500 * m, m)],
                     T0 + timedelta(minutes=m), {"79"})  # fmt: skip
    assert (
        by_vid(board.snapshot(T0 + timedelta(minutes=3)))["B"]["stop_gap_s"] == 120
    )  # A at s7, m=2
    for m in (4, 5):
        board.update([], T0 + timedelta(minutes=m), set())  # route 79's call failed
    b = by_vid(board.snapshot(T0 + timedelta(minutes=5)))["B"]  # still on the map
    assert b["stop_gap_s"] is None

    restarted = stop_board()
    restarted.seed([("A", T0 + timedelta(minutes=m), MAIN, 5000.0 + 1000 * m, DAY, 36000)
                    for m in range(0, 4)] + [("B", T0 + timedelta(minutes=m), MAIN,
                    4000.0 + 500 * m, DAY, 36060) for m in range(0, 4)])  # fmt: skip
    later = T0 + timedelta(minutes=6)  # down for 3 minutes
    restarted.update([trip("B", 5500, 6, stst=36060)], later, {"79"})
    assert by_vid(restarted.snapshot(later))["B"]["stop_gap_s"] is None
