import json
from datetime import UTC, datetime, timedelta

from ghost_ledger.live import TRAIL, FileSink, LiveBoard, publish

T0 = datetime(2026, 9, 26, 22, 0, tzinfo=UTC)


def pos(vid, pdist, t, pid=1599, rt="79", **kw):
    return {"vid": vid, "rt": rt, "pid": pid, "pdist": pdist, "lat": 41.75, "lon": -87.65,
            "des": "Lakefront", "dly": False, "observed_at": t, **kw}  # fmt: skip


def run(board, sweeps):
    """sweeps: list of (minute offset, [positions]) fed in order; returns the last snapshot."""
    for minute, positions in sweeps:
        board.update(positions, T0 + timedelta(minutes=minute))
    return board.snapshot(T0 + timedelta(minutes=sweeps[-1][0]))


def by_vid(snapshot):
    return {b["vid"]: b for b in snapshot["vehicles"]}


def test_gap_is_the_time_since_the_bus_ahead_passed_this_spot():
    # A drives 1,000 ft/min from 0; B starts 8 minutes later behind it
    sweeps = []
    for m in range(0, 11):
        buses = [pos("A", 2000 + 1000 * m, T0 + timedelta(minutes=m))]
        if m >= 8:
            buses.append(pos("B", 2000 + 1000 * (m - 8), T0 + timedelta(minutes=m)))
        sweeps.append((m, buses))
    snap = by_vid(run(LiveBoard(), sweeps))
    assert snap["B"]["gap_s"] == 8 * 60 and snap["B"]["ahead"] == "A"
    assert snap["A"]["gap_s"] is None  # nothing ahead of the leader


def test_gap_interpolates_between_the_leaders_reports():
    # A reported at 0 ft (t=0) and 2,000 ft (t=2); B is at 1,500 ft at t=5
    board = LiveBoard()
    board.update([pos("A", 1000, T0)], T0)
    board.update([pos("A", 3000, T0 + timedelta(minutes=2))], T0 + timedelta(minutes=2))
    now = T0 + timedelta(minutes=5)
    board.update([pos("A", 6000, now), pos("B", 2500, now)], now)
    assert by_vid(board.snapshot(now))["B"]["gap_s"] == round(5 * 60 - 1.5 * 60)


def test_gap_is_unknown_when_the_leaders_trail_does_not_reach_back():
    # A appeared already ahead of B (joined mid-route, or the poller restarted)
    now = T0
    board = LiveBoard()
    board.update([pos("A", 9000, now), pos("B", 1000, now)], now)
    assert by_vid(board.snapshot(now))["B"]["gap_s"] is None


def test_buses_on_other_patterns_are_not_leaders():
    board = LiveBoard()
    for m in range(0, 5):
        t = T0 + timedelta(minutes=m)
        board.update([pos("A", 2000 + 1000 * m, t, pid=1), pos("B", 1500, t, pid=2)], t)
    snap = by_vid(board.snapshot(T0 + timedelta(minutes=4)))
    assert snap["B"]["gap_s"] is None and snap["B"]["ahead"] is None


def test_buses_that_left_the_feed_drop_out_and_old_trail_is_forgotten():
    board = LiveBoard()
    board.update([pos("A", 0, T0), pos("B", 0, T0)], T0)
    later = T0 + TRAIL + timedelta(minutes=5)
    board.update([pos("A", 100, later)], later)
    assert set(by_vid(board.snapshot(later))) == {"A"}
    assert "B" not in board.trails and len(board.trails["A"]) == 1


def test_publish_writes_the_snapshot_and_never_raises(tmp_path):
    path = tmp_path / "live" / "vehicles.json"
    board = LiveBoard()
    publish(board, FileSink(str(path)), [pos("A", 0, T0)], T0)
    snap = json.loads(path.read_text())
    v = snap["vehicles"][0]
    assert v["vid"] == "A" and v["t"] == int(T0.timestamp()) and set(v) >= {"hdg", "dir", "des"}

    class Broken:
        def write(self, snapshot):
            raise OSError("S3 down")

    publish(board, Broken(), [pos("A", 10, T0)], T0)  # logged, not raised


def test_seeded_trails_give_gaps_right_after_a_restart():
    board = LiveBoard()
    board.seed([("A", T0 + timedelta(minutes=m), 1599, 1000.0 * m) for m in range(10)])
    now = T0 + timedelta(minutes=10)
    board.update([pos("A", 10000, now), pos("B", 3000, now)], now)
    assert by_vid(board.snapshot(now))["B"]["gap_s"] == 7 * 60  # A passed 3,000 ft at minute 3


def test_buses_laying_over_at_a_terminal_get_no_gap_and_lead_nobody():
    board = LiveBoard()
    board.lengths = {1599: 50_000}
    now = T0
    board.update([pos("A", 200, now), pos("B", 50, now), pos("C", 49_500, now)], now)
    snap = by_vid(board.snapshot(now))
    assert all(snap[v]["at_terminal"] and snap[v]["gap_s"] is None for v in "ABC")
    assert snap["B"]["ahead"] is None


def test_buses_repeating_a_stale_report_are_left_off_the_map():
    board = LiveBoard()
    now = T0 + timedelta(minutes=10)
    board.update([pos("A", 5000, now), pos("B", 3000, T0)], now)  # B's report is 10 min old
    assert set(by_vid(board.snapshot(now))) == {"A"}


def drive(board, minutes, buses):
    """buses: {vid: (start_minute, start_ft, last_minute or None)} at 1,000 ft/min; a bus is
    reported from start to last minute, then its tracker goes dark (it drops out of the feed)."""
    for m in range(minutes + 1):
        t = T0 + timedelta(minutes=m)
        reports = [
            pos(vid, ft + 1000 * (m - start), t)
            for vid, (start, ft, last) in buses.items()
            if start <= m and (last is None or m <= last)
        ]
        board.update(reports, t)
    return by_vid(board.snapshot(T0 + timedelta(minutes=minutes)))


def test_a_bus_whose_tracker_died_after_passing_here_still_counts():
    # A leads; Z follows 5 min later and goes dark at minute 12 (at 9,000 ft), still driving;
    # B follows Z by 5 min and is at 8,000 ft at minute 16, a spot Z passed before going dark.
    snap = drive(LiveBoard(), 16, {"A": (0, 2000, None), "Z": (5, 2000, 12), "B": (10, 2000, None)})
    # B is at 8,000 ft; A passed it at minute 6, Z (dark now) at minute 11: the wait is 5 min.
    assert snap["B"]["gap_s"] == 5 * 60
    assert "Z" not in snap  # dark buses stay off the map


def test_gap_is_unknown_when_a_dark_bus_may_have_passed_since():
    # Z went dark at minute 8 at 5,000 ft, before reaching B's spot (8,000 ft), after A passed
    # it (minute 6): Z may well have passed since, so no wait is claimed.
    snap = drive(LiveBoard(), 16, {"A": (0, 2000, None), "Z": (5, 2000, 8), "B": (10, 2000, None)})
    assert snap["B"]["gap_s"] is None


def test_a_dark_bus_behind_this_one_changes_nothing():
    # Z went dark behind B (B was already past Z's last spot), so B's gap is to A as usual.
    snap = drive(LiveBoard(), 16, {"A": (0, 2000, None), "B": (4, 2000, None), "Z": (8, 2000, 10)})
    assert snap["B"]["gap_s"] == 4 * 60 and snap["B"]["ahead"] == "A"


def test_snapshot_names_the_running_commit(monkeypatch):
    # CI waits for this to equal the commit it built: the deploy check seen from outside.
    monkeypatch.setenv("GIT_SHA", "abc123")
    board = LiveBoard()
    board.update([pos("A", 0, T0)], T0)
    assert board.snapshot(T0)["build"] == "abc123"


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


def trip(vid, pdist, minute, pid=MAIN, stst=36000):
    return pos(vid, pdist, T0 + timedelta(minutes=minute), pid=pid, stsd=DAY, stst=stst)


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
    assert b["ahead"] == "A"  # the old same-pattern fields are unchanged


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


def test_a_dark_bus_behind_this_one_changes_nothing_across_patterns():
    board = stop_board()
    for m in range(0, 11):
        buses = [trip("A", 2000 + 1000 * m, m)]
        if m <= 4:
            buses.append(trip("S", 500 + 100 * m, m, pid=SHORT))  # dark at MAIN 4,900 ft, behind B
        if m >= 4:
            buses.append(trip("B", 5000 + 200 * (m - 4), m))
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=10)))["B"]
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
    for m, d in ((5, 6500), (6, 7500)):  # the next trip (a new stst) passes s7 again
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
    board = stop_board()
    sweep(board, 0, trip("A", 3000, 0), trip("T", 200, 0))
    t = by_vid(board.snapshot(T0))["T"]
    assert t["stop_gap_s"] is None and t["next_stpid"] is None


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


def test_a_bus_that_creeps_past_its_first_stop_and_waits_passes_it_when_it_leaves():
    board = stop_board()
    for m, d in enumerate([0, 20, 20, 20, 20, 20, 20, 20, 900, 1900]):  # creeps 20 ft, waits 6 min
        sweep(board, m, trip("L", d, m))
    passes = board.passes()
    assert passes[("79", "Eastbound", "s0")] == [(T0 + timedelta(minutes=7), "L")]  # not m=0


def test_the_old_gap_keeps_its_75_minute_view_while_the_stop_wait_looks_back_further():
    board = stop_board()
    for m in range(0, 101, 1):
        buses = [trip("A", 2000 + 1000 * m, m)] if m <= 10 else []  # passes s7 at m=5, then gone
        if m >= 96:
            buses.append(trip("B", 4600 + 500 * (m - 96), m))  # 6,600 ft at m=100
        sweep(board, m, *buses)
    b = by_vid(board.snapshot(T0 + timedelta(minutes=100)))["B"]
    assert b["gap_s"] is None  # as before: A's pass is 95 minutes old
    assert b["stop_gap_s"] == 95 * 60 and b["last_by"] == "A"
