import json
from datetime import UTC, datetime, timedelta

from ghost_ledger.live import FileSink, LiveBoard, publish

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
    later = T0 + timedelta(minutes=80)
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
