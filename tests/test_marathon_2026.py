"""The Marathon retro's stop passes (transform/reports/marathon_2026.py): a stop counts as served
only when GPS-checked reports of one bus, trip and pattern span it, so a bus labelled with its
regular route while it drives a reroute serves none of the stops it skips."""

import importlib.util
import math
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

SRC = Path(__file__).parents[1] / "transform/reports/marathon_2026.py"
spec = importlib.util.spec_from_file_location("marathon_2026", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# A northbound pattern up a street (lon -87.65): stops every 1,000 ft (~305 m) of latitude
FT = 0.3048 / 110540  # degrees of latitude per foot
STOPS = [("A", 0), ("B", 1000), ("C", 2000), ("D", 3000)]


def setup(reports: list[tuple]) -> duckdb.DuckDBPyConnection:
    con = m.connect()
    m.table(
        con,
        "pmeta",
        [(1, "9", "Northbound", 3000.0, False)],
        "pid INT, rt VARCHAR, rtdir VARCHAR, length_ft DOUBLE, is_detour BOOLEAN",
    )
    m.table(
        con,
        "ppoints",
        [
            (1, "pt", i, "S", s, f"Stop {s}", float(d), 41.9 + d * FT, -87.65)
            for i, (s, d) in enumerate(STOPS)
        ],
        "pid INT, kind VARCHAR, seq INT, typ VARCHAR, stpid VARCHAR, stpnm VARCHAR, "
        "pdist DOUBLE, lat DOUBLE, lon DOUBLE",
    )
    # reports: (vid, t, pdist, lon offset in metres east of the street)
    con.execute("""CREATE TABLE pos (day VARCHAR, t BIGINT, vid VARCHAR, rt VARCHAR, pid INT,
                   pdist INT, lat DOUBLE, lon DOUBLE, stst INT, stsd DATE)""")
    con.executemany(
        "INSERT INTO pos VALUES ('2026-10-11', ?, ?, '9', 1, ?, ?, ?, 36000, DATE '2026-10-11')",
        [(t, vid, d, 41.9 + d * FT, -87.65 + east / 82990) for vid, t, d, east in reports],
    )
    m.stop_passes(con)
    return con


def passed(con) -> list[tuple]:
    return con.execute("SELECT vid, stpid, round(tp) FROM passes ORDER BY vid, tp").fetchall()


def test_a_bus_on_its_route_passes_each_stop_it_spans_at_the_interpolated_time():
    con = setup([("1", 0, 500, 0), ("1", 60, 1500, 0), ("1", 120, 2600, 0)])
    assert passed(con) == [("1", "B", 30), ("1", "C", 87)]


def test_a_report_off_its_pattern_proves_nothing_either_side():
    # the middle report is 2 km east of the street (driving CTA's reroute, labelled regular)
    con = setup([("1", 0, 500, 0), ("1", 60, 1500, 2000), ("1", 120, 2600, 0)])
    assert passed(con) == []


def test_a_bus_that_stops_short_of_a_stop_has_not_passed_it():
    # turns back 120 ft before D (Oct 12: #20 at Washington & Clark/Dearborn)
    con = setup([("1", 0, 2500, 0), ("1", 60, 2880, 0), ("1", 120, 2880, 0)])
    assert passed(con) == []


def test_reports_too_far_apart_in_time_prove_nothing():
    con = setup([("1", 0, 500, 0), ("1", m.MAX_PAIR_S + 1, 1500, 0)])
    assert passed(con) == []


def test_a_bus_a_block_over_passes_none_of_the_stops_of_the_street_it_left():
    # 200 m east is "on route" (within 300 m) but a block over: #12, #126 and #151's buses on a
    # parallel street or Outer Lake Shore Drive were credited with the stops they left (Oct 12)
    con = setup([("1", 0, 500, 200), ("1", 60, 1500, 200), ("1", 120, 2600, 200)])
    assert passed(con) == []


def test_a_bus_within_gps_error_of_its_street_passes():
    con = setup([("1", 0, 500, 30), ("1", 60, 1500, 30), ("1", 120, 2600, 30)])
    assert passed(con) == [("1", "B", 30), ("1", "C", 87)]


def test_no_bus_passes_a_stop_on_the_course_while_its_street_is_closed():
    # stops on the course line (LaSalle St, about miles 3 and 3.4; planned to reopen 11:30 AM): a
    # bus moving along it at 9 AM is on the road beside it (Outer Lake Shore Drive, Oct 12), one
    # crossing it is crossing, and one after the reopening served it, unless its route was still
    # detoured (to 6 PM at the latest) and it was more than 15 m off (#146 and #151 on Outer LSD,
    # 26 m from the Inner Drive, until ~5 PM), or GPS had put a bus there while it was closed
    # (the road beside it within GPS's reach: then no pass counts while its route was detoured)
    course = m.course_json()
    point = {}
    for name, mile in (("S", 3.0), ("T", 3.4)):
        i = next(k for k, a in enumerate(course["at"]) if a >= mile)
        (lon, lat), (lon2, lat2) = course["line"][i], course["line"][i + 1]
        point[name] = (lat, lon, math.degrees(math.atan2((lon2 - lon) * m.KX, (lat2 - lat) * m.KY)))
    at = lambda hhmm, day="2026-10-11": f"epoch(TIMESTAMPTZ '{day} {hhmm}:00-05:00')"  # noqa: E731

    def row(vid, rt, stop, hhmm, chord, turn=0, day="2026-10-11"):
        lat, lon, along = point[stop]
        tp = at(hhmm, day)
        return f"('{vid}', '{day}', '{rt}', '{stop}', {lat}, {lon}, {tp}, {along + turn}, {chord})"

    con = m.connect()
    con.execute("""CREATE TABLE detours AS SELECT * FROM (VALUES
        (TIMESTAMPTZ '2026-10-11 16:45:00-05:00', 1, 'BOA Marathon', '151'),
        (TIMESTAMPTZ '2026-10-12 06:00:00-05:00', 1, 'BOA Marathon', '146')
      ) v(fetched_at, st, "desc", rt)""")
    rows = [
        row("S along at 9", "22", "S", "09:00", 5),
        row("S back the other way at 9", "22", "S", "09:05", 5, 180),
        row("S crossing at 9", "22", "S", "09:10", 5, 90),
        row("S along at 2", "22", "S", "14:00", 30),
        row("S detoured, at it at 2", "151", "S", "14:05", 5),
        row("S along on a normal Sunday", "22", "S", "09:00", 5, day="2026-10-04"),
        row("T detoured, beside it at 2", "151", "T", "14:00", 26),
        row("T detoured, at it at 2", "151", "T", "14:05", 5),
        row("T back beside it at 5:30", "151", "T", "17:30", 26),
        row("T flagged all night, beside it at 5:30", "146", "T", "17:30", 26),
        row("T flagged all night, beside it at 6:30", "146", "T", "18:30", 26),
    ]
    con.execute(f"""CREATE TABLE passes AS SELECT * FROM (VALUES {", ".join(rows)})
                    v(vid, day, rt, stpid, lat, lon, tp, brg, chord_m)""")
    m.closed_course(con)
    kept = [v for (v,) in con.execute("SELECT vid FROM passes ORDER BY vid").fetchall()]
    assert kept == [
        "S along at 2",
        "S along on a normal Sunday",
        "S crossing at 9",
        "T back beside it at 5:30",
        "T detoured, at it at 2",
        "T flagged all night, beside it at 6:30",
    ]


def test_a_bus_hidden_while_it_drives_a_reroute_passes_none_of_the_stops_it_skips():
    # Bus Tracker stops reporting a bus off its pattern: #12's buses labelled with Roosevelt Rd
    # vanished on the reroute and reappeared on Roosevelt 9-14 min later, credited with Roosevelt &
    # Racine all day (its real buses: 5:33 AM, then 6:13 PM; Oct 12). Reroute 2 runs A-X-D in place
    # of A-B-C-D, so it skips B and C. A gap-bridging pass counts next to one seen all the way
    con = m.connect()
    m.table(
        con,
        "pmeta",
        [(2, "12", "Westbound", 0.0, True)],
        "pid INT, rt VARCHAR, rtdir VARCHAR, length_ft DOUBLE, is_detour BOOLEAN",
    )
    m.table(
        con,
        "ppoints",
        [(2, "dtrpt", i, "S", s) for i, s in enumerate("ABCD")]
        + [(2, "pt", i, "S", s) for i, s in enumerate("AXD")],
        "pid INT, kind VARCHAR, seq INT, typ VARCHAR, stpid VARCHAR",
    )
    con.execute("CREATE TABLE pos AS SELECT '2026-10-11' AS day, 2 AS pid")
    at = lambda hhmm, day="2026-10-11": f"epoch(TIMESTAMPTZ '{day} {hhmm}:00-05:00')"  # noqa: E731
    con.execute(f"""CREATE TABLE passes AS SELECT * FROM (VALUES
        ('after a gap, skipped stop', '2026-10-11', '12', 'Westbound', 'B', {at("09:00")}, 600),
        ('after a gap, at 5 AM', '2026-10-11', '12', 'Westbound', 'B', {at("05:00")}, 600),
        ('after a gap, late, alone', '2026-10-11', '12', 'Westbound', 'B', {at("17:52")}, 755),
        ('after a gap, next to a seen one', '2026-10-11', '12', 'Westbound', 'C', {at("09:10")},
         600),
        ('after a gap, stop it runs', '2026-10-11', '12', 'Westbound', 'A', {at("09:00")}, 600),
        ('seen all the way', '2026-10-11', '12', 'Westbound', 'C', {at("09:00")}, 30),
        ('after a gap, normal Sunday', '2026-10-04', '12', 'Westbound', 'B',
         {at("09:00", "2026-10-04")}, 600)
      ) v(vid, day, rt, rtdir, stpid, tp, dt)""")
    m.hidden_on_reroute(con)
    kept = [v for (v,) in con.execute("SELECT vid FROM passes ORDER BY vid").fetchall()]
    assert kept == [
        "after a gap, at 5 AM",
        "after a gap, next to a seen one",
        "after a gap, normal Sunday",
        "after a gap, stop it runs",
        "seen all the way",
    ]
