"""Marathon retro: what the Chicago Marathon (Sun Oct 11, 2026) did to the buses, as the derived
files behind chitransit.fyi/reports/marathon-2026/.

Usage (laptop; reads Ingress, never writes it):
    eval "$(aws configure export-credentials --profile ghost-ledger --format env)"
    AWS_DEFAULT_REGION=us-east-2 uv run --group transform \
        python transform/reports/marathon_2026.py \
        --work ~/marathon-work --out site/public/data/reports/marathon-2026 [--publish]

Stages:
  extract  Ingress -> Parquet in --work: every bus report of each day below (00:00 to 04:00 the next
           morning, Chicago), every pattern version, CTA's detours (getdetours, every 2 min) and
           Customer Alerts (every 10 min). Skipped when the files exist (--fresh to redo).
  base     one DuckDB file in --work: reports checked against their pattern by GPS and the stop
           passes they prove (the live site's on-route rule, below).
  build    the derived files in --out, checked against the page's contract (check) first: a
           broken build writes nothing.
  publish  (--publish) the files to the site bucket's data/reports/marathon-2026/, which neither
           the site deploy nor the nightly touches.

Only derived results leave this script (CTA's license): counts, per-stop states, waits, paths of
CTA's own patterns; never raw vehicle positions.

Stop passes (as live.py's on-route rule and docs/research/marathon-2026-10-11/passes.py): a report
counts only if its GPS lies within max(300 m, half the stop gap) of where its pattern puts it at
its pdist; a stop is passed when its pdist lies between two consecutive such reports of the same
bus, trip and pattern no more than 15 min apart, at the interpolated time. A bus labelled with its
regular pattern while it drives CTA's reroute therefore passes nothing it didn't reach.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent))
import marathon_2026_crossings as crossings  # noqa: E402
import marathon_2026_dark as dark  # noqa: E402

MARATHON = "2026-10-11"
NORMAL = ["2026-09-27", "2026-10-04"]  # Sundays on the same GTFS service, no special events
DAYS = sorted([MARATHON, *NORMAL])
INGRESS = "glue.ghost_ledger_ingress"
GTFS = "s3://ghost-ledger-lake-354894954112/gtfs"  # transform/load_gtfs.py: one folder per feed
# On route: GPS within this many metres of where the pattern puts the bus (or half the gap
# between the stops around it, when wider). A pass spans at most MAX_PAIR_S between two reports,
# and the straight line between them comes within CHORD_M of the stop: on route alone credited
# buses a block over, or on Outer Lake Shore Drive, with the stops of the street they left (Oct 12
# check: #12, #126, #151; 100 m kept some on Michigan Ave, where Wabash and Indiana are ~100 m
# away; 60 m kept corner cuts 43-52 m off, e.g. Sheridan & Irving Park, Archer & Canal, credited
# 5 h early; 40 m costs normal Sundays' noise 145 -> 150 stops). 15 min errs toward service: a
# bus that drops out of Bus Tracker while it drives a reroute its label doesn't show is credited
# with the stops it skipped (hidden_on_reroute() takes most of those back), so stops look less
# dark and back sooner than they were; 2 or 5 min instead drop real passes across GPS gaps and
# double the false
# "dark" on normal Sundays (Sep 27: 924 -> 1,390-1,465 dark stop-hours). A trip run breaks at
# RUN_GAP_S. closed_course() then drops the Marathon's passes at stops on the closed course.
ON_ROUTE_M = 300
MAX_PAIR_S = 900
CHORD_M = 40
RUN_GAP_S = 900
RIDERSHIP = Path(__file__).with_name("marathon_2026_ridership.csv")  # City of Chicago data portal


def say(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def connect(path: str = ":memory:", read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path, read_only=read_only)
    con.execute("SET TimeZone = 'America/Chicago'")
    return con


def extract(work: Path, fresh: bool) -> dict:
    """Ingress -> Parquet; returns each table's current snapshot id (pinned in the manifest)."""
    pinned = work / "snapshots.json"  # the Ingress snapshot each cached extract was read at
    if pinned.exists() and not fresh:
        return json.loads(pinned.read_text())
    con = connect()
    con.execute(
        "INSTALL httpfs; INSTALL aws; INSTALL iceberg; LOAD httpfs; LOAD aws; LOAD iceberg;"
    )
    con.execute("SET threads = 24; SET preserve_insertion_order = false")
    con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    con.execute("ATTACH '354894954112' AS glue (TYPE iceberg, ENDPOINT_TYPE glue)")
    snapshots = {}
    for t in [
        "ghost_ops_bus_positions",
        "ghost_ops_patterns",
        "ghost_ops_raw_polls",
        "ghost_ops_raw_responses",
    ]:
        snapshots[t] = con.execute(
            f"SELECT snapshot_id FROM iceberg_snapshots({INGRESS}.{t}) "
            "ORDER BY timestamp_ms DESC LIMIT 1"
        ).fetchone()[0]

    def copy(name: str, sql: str) -> None:
        target = work / f"{name}.parquet"
        if target.exists() and not fresh:
            return
        t0 = time.time()
        part = target.with_suffix(".part")  # renamed once whole: an interrupted run leaves no file
        con.execute(f"COPY ({sql}) TO '{part}' (FORMAT parquet, COMPRESSION zstd)")
        part.rename(target)
        say(f"extract {name}: {time.time() - t0:.0f} s")

    for t in ("calendar", "calendar_dates", "trips", "routes"):  # the timetable CTA published
        copy(
            f"gtfs_{t}",
            f"SELECT * FROM read_parquet('{GTFS}/{t}/*/*.parquet', hive_partitioning = true)",
        )
    for day in DAYS:
        # CDC is at-least-once: identical copies of a report collapse under DISTINCT
        copy(
            f"pos_{day}",
            f"""
            SELECT DISTINCT vid, observed_at, rt, pid, pdist, lat, lon, stst, stsd, tablockid, psgld
            FROM {INGRESS}.ghost_ops_bus_positions
            WHERE observed_at >= TIMESTAMPTZ '{day} 00:00:00 America/Chicago'
              AND observed_at < TIMESTAMPTZ '{day} 00:00:00 America/Chicago' + INTERVAL 28 HOUR""",
        )
    copy(
        "patterns",
        f"""
        SELECT pid, rt, rtdir, length_ft, content_hash, pattern, first_seen_at, changed_at,
               __lsn AS lsn
        FROM {INGRESS}.ghost_ops_patterns WHERE __op != 'd'
        QUALIFY row_number() OVER (PARTITION BY pid, content_hash ORDER BY __lsn) = 1""",
    )
    # CTA's detour and alert feeds from the morning before to the morning after
    copy(
        "feeds",
        f"""
        WITH polls AS (
            SELECT DISTINCT poll_id, endpoint, fetched_at FROM {INGRESS}.ghost_ops_raw_polls
            WHERE endpoint IN ('getdetours', 'alerts')
              AND fetched_at >= TIMESTAMPTZ '{MARATHON} 00:00:00 America/Chicago' - INTERVAL 1 DAY
              AND fetched_at < TIMESTAMPTZ '{MARATHON} 00:00:00 America/Chicago' + INTERVAL 36 HOUR)
        SELECT p.endpoint, p.fetched_at, b.body FROM polls p JOIN (
            SELECT DISTINCT poll_id, body FROM {INGRESS}.ghost_ops_raw_responses
            WHERE fetched_at >= TIMESTAMPTZ '{MARATHON} 00:00:00 America/Chicago' - INTERVAL 1 DAY
              AND fetched_at < TIMESTAMPTZ '{MARATHON} 00:00:00 America/Chicago' + INTERVAL 36 HOUR
        ) b USING (poll_id)""",
    )
    pinned.write_text(json.dumps(snapshots))
    return snapshots


def pattern_tables(con: duckdb.DuckDBPyConnection, work: Path) -> None:
    """pmeta (a row per pid) and ppoints (its driven path 'pt' and, for CTA's reroutes, the
    regular path 'dtrpt' they replace), from each pid's latest version: no pid's stop list
    changed between versions (checked Oct 12), only whether CTA sent dtrid alongside."""
    latest = con.execute(
        f"""SELECT pid, rt, rtdir, length_ft, pattern FROM '{work}/patterns.parquet'
            QUALIFY row_number() OVER (PARTITION BY pid ORDER BY lsn DESC) = 1"""
    ).fetchall()
    detour = dict(
        con.execute(
            f"""SELECT pid, pattern FROM '{work}/patterns.parquet'
                WHERE pattern LIKE '%"dtrpt"%'
                QUALIFY row_number() OVER (PARTITION BY pid ORDER BY lsn DESC) = 1"""
        ).fetchall()
    )
    meta, points = [], []
    for pid, rt, rtdir, length_ft, pattern in latest:
        p = json.loads(pattern)
        d = json.loads(detour[pid]) if pid in detour else {}
        meta.append((pid, rt, rtdir, length_ft, pid in detour))
        for kind, pts in (("pt", p.get("pt") or []), ("dtrpt", d.get("dtrpt") or [])):
            points += [
                (
                    pid,
                    kind,
                    int(x["seq"]),
                    x.get("typ"),
                    x.get("stpid"),
                    x.get("stpnm"),
                    float(x.get("pdist") or 0),
                    float(x["lat"]),
                    float(x["lon"]),
                )
                for x in pts
            ]
    table(
        con,
        "pmeta",
        meta,
        "pid INT, rt VARCHAR, rtdir VARCHAR, length_ft DOUBLE, is_detour BOOLEAN",
    )
    table(
        con,
        "ppoints",
        points,
        "pid INT, kind VARCHAR, seq INT, typ VARCHAR, stpid VARCHAR, stpnm VARCHAR, "
        "pdist DOUBLE, lat DOUBLE, lon DOUBLE",
    )


def table(con: duckdb.DuckDBPyConnection, name: str, rows: list[tuple], columns: str) -> None:
    """A table from Python rows, loaded column-wise (row by row, ~340k points took minutes)."""
    names = [c.split()[0] for c in columns.split(", ")]
    con.register("rows_in", pa.table({n: [r[i] for r in rows] for i, n in enumerate(names)}))
    con.execute(f"CREATE OR REPLACE TABLE {name} ({columns})")
    con.execute(f"INSERT INTO {name} SELECT * FROM rows_in")
    con.unregister("rows_in")


def stop_passes(con: duckdb.DuckDBPyConnection) -> None:
    """ping (each report, on_route or not) and passes from table pos (day, vid, t, rt, pid, pdist,
    lat, lon, stst, stsd) and the pattern tables. Pure SQL so tests can run it on a few rows."""
    con.execute("""CREATE OR REPLACE TABLE seg AS
      SELECT s.pid, m.rt, m.rtdir, s.seq, s.stpid, s.stpnm, s.pdist, s.lat, s.lon,
             lead(s.pdist) OVER w AS pdist2, lead(s.lat) OVER w AS lat2, lead(s.lon) OVER w AS lon2
      FROM ppoints s JOIN pmeta m USING (pid)
      WHERE s.kind = 'pt' AND s.typ = 'S'
      WINDOW w AS (PARTITION BY s.pid ORDER BY s.seq)""")
    # Where the pattern puts the bus: between the stops around its pdist, in proportion
    # (CTA gives waypoints no pdist); metres by a flat-earth scale fine at Chicago's latitude.
    con.execute(f"""CREATE OR REPLACE TABLE ping AS
      WITH j AS (
        SELECT p.*, s.rtdir,
          CASE WHEN s.pdist2 IS NULL OR s.pdist2 <= s.pdist THEN s.lat
               ELSE s.lat + least(1, (p.pdist - s.pdist) / (s.pdist2 - s.pdist)) * (s.lat2 - s.lat)
          END AS elat,
          CASE WHEN s.pdist2 IS NULL OR s.pdist2 <= s.pdist THEN s.lon
               ELSE s.lon + least(1, (p.pdist - s.pdist) / (s.pdist2 - s.pdist)) * (s.lon2 - s.lon)
          END AS elon,
          coalesce((s.pdist2 - s.pdist) * 0.3048, 0) AS gap_m
        FROM pos p ASOF LEFT JOIN seg s ON p.pid = s.pid AND p.pdist >= s.pdist)
      SELECT *, sqrt(pow((lat - elat) * 110540, 2) + pow((lon - elon) * 82990, 2)) AS off_m,
             coalesce(sqrt(pow((lat - elat) * 110540, 2) + pow((lon - elon) * 82990, 2))
                      <= greatest({ON_ROUTE_M}, 0.5 * gap_m), false) AS on_route
      FROM j""")
    con.execute(f"""CREATE OR REPLACE TABLE pairs AS
      SELECT * FROM (
        SELECT day, vid, rt, pid, rtdir, t, pdist, on_route, stst, stsd, lat, lon,
          lag(lat) OVER w AS lat0, lag(lon) OVER w AS lon0,
          lag(t) OVER w AS t0, lag(pdist) OVER w AS d0, lag(pid) OVER w AS pid0,
          lag(on_route) OVER w AS on0, lag(stst) OVER w AS stst0, lag(stsd) OVER w AS stsd0
        FROM ping WINDOW w AS (PARTITION BY day, vid ORDER BY t))
      WHERE pid = pid0 AND stst IS NOT DISTINCT FROM stst0 AND stsd IS NOT DISTINCT FROM stsd0
        AND pdist > d0 AND on_route AND on0 AND t - t0 <= {MAX_PAIR_S}""")
    # the stop's distance (m) from the straight line between the two reports
    ax, ay, bx, by = "p.lon0 * 82990", "p.lat0 * 110540", "p.lon * 82990", "p.lat * 110540"
    px, py = "s.lon * 82990", "s.lat * 110540"
    u = (
        f"greatest(0, least(1, (({px} - {ax}) * ({bx} - {ax}) + ({py} - {ay}) * ({by} - {ay}))"
        f" / greatest(pow({bx} - {ax}, 2) + pow({by} - {ay}, 2), 1e-9)))"
    )
    chord = (
        f"sqrt(pow({px} - {ax} - {u} * ({bx} - {ax}), 2)"
        f" + pow({py} - {ay} - {u} * ({by} - {ay}), 2))"
    )
    con.execute(f"""CREATE OR REPLACE TABLE passes AS
      SELECT p.day, p.vid, p.rt, p.rtdir, p.pid, s.stpid, s.stpnm, s.seq, s.lat, s.lon,
             p.t0 + (s.pdist - p.d0) / (p.pdist - p.d0) * (p.t - p.t0) AS tp, p.t - p.t0 AS dt,
             p.stst, p.stsd, {chord} AS chord_m,
             degrees(atan2((p.lon - p.lon0) * 82990, (p.lat - p.lat0) * 110540)) AS brg
      FROM pairs p JOIN seg s ON s.pid = p.pid AND s.pdist > p.d0 AND s.pdist <= p.pdist
      WHERE {chord} <= {CHORD_M}""")


def feed_tables(con: duckdb.DuckDBPyConnection, work: Path) -> None:
    """detours (a row per detour, route and direction, per poll) and alerts (per alert and
    impacted service, per poll) from CTA's raw responses."""
    detours, alerts = [], []
    for endpoint, fetched_at, body in con.execute(
        f"SELECT endpoint, fetched_at, body FROM '{work}/feeds.parquet'"
    ).fetchall():
        try:
            b = json.loads(body)
        except ValueError:
            continue
        if endpoint == "getdetours":
            for d in b.get("bustime-response", {}).get("dtrs") or []:
                for r in d.get("rtdirs") or []:
                    detours.append(
                        (
                            fetched_at,
                            str(d["id"]),
                            int(d.get("st") or 0),
                            d.get("desc"),
                            r.get("rt"),
                            r.get("dir"),
                            d.get("startdt"),
                            d.get("enddt"),
                        )
                    )
        else:
            found = b.get("CTAAlerts", {}).get("Alert") or []
            for a in found if isinstance(found, list) else [found]:
                services = (a.get("ImpactedService") or {}).get("Service") or []
                full = a.get("FullDescription")
                full = full.get("#cdata-section") if isinstance(full, dict) else full
                for s in services if isinstance(services, list) else [services]:
                    alerts.append(
                        (
                            fetched_at,
                            str(a.get("AlertId")),
                            a.get("Headline"),
                            a.get("ShortDescription"),
                            full,
                            a.get("EventStart"),
                            a.get("EventEnd"),
                            s.get("ServiceType"),
                            s.get("ServiceId"),
                        )
                    )
    table(
        con,
        "detours",
        detours,
        'fetched_at TIMESTAMPTZ, id VARCHAR, st INT, "desc" VARCHAR, rt VARCHAR, dir VARCHAR, '
        "startdt VARCHAR, enddt VARCHAR",
    )
    table(
        con,
        "alerts",
        alerts,
        "fetched_at TIMESTAMPTZ, alert_id VARCHAR, headline VARCHAR, short VARCHAR, "
        "full_desc VARCHAR, event_start VARCHAR, event_end VARCHAR, service_type VARCHAR, "
        "service_id VARCHAR",
    )


def base(work: Path) -> None:
    con = connect(str(work / "base.duckdb"))
    con.execute("SET memory_limit = '12GB'; SET preserve_insertion_order = false")
    con.execute(
        "CREATE OR REPLACE TABLE pos AS "
        + " UNION ALL ".join(
            f"SELECT '{d}' AS day, epoch(observed_at)::BIGINT AS t, * FROM '{work}/pos_{d}.parquet'"
            for d in DAYS
        )
    )
    pattern_tables(con, work)
    stop_passes(con)
    feed_tables(con, work)
    for row in con.execute(
        "SELECT day, count(*), round(avg(on_route::INT), 4) FROM ping GROUP BY 1 ORDER BY 1"
    ).fetchall():
        say("reports (day, n, on route):", row)
    for row in con.execute("SELECT day, count(*) FROM passes GROUP BY 1 ORDER BY 1").fetchall():
        say("passes (day, n):", row)
    con.close()


COURSE = Path(__file__).with_name(
    "marathon_2026_course.geojson"
)  # OSM centrelines (ODbL), see its properties
MARATHON_MI = 26.2188  # the certified 42.195 km


OSM_CREDIT = "Course line: (c) OpenStreetMap contributors, ODbL 1.0 (https://opendatacommons.org/licenses/odbl/)"


def course_json() -> dict:
    """The course line and the distance along it (miles) at each point. Centrelines run ~1.7%
    longer than the certified course (which cuts corners), so distances are scaled to it: mile 13.1
    on the line is where the half-way mark is."""
    feature = next(
        f
        for f in json.loads(COURSE.read_text())["features"]
        if f["geometry"]["type"] == "LineString"
    )
    line = [(round(lon, 5), round(lat, 5)) for lon, lat in feature["geometry"]["coordinates"]]
    line = [p for i, p in enumerate(line) if i == 0 or p != line[i - 1]]
    metres = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(line, line[1:], strict=False):
        metres.append(metres[-1] + haversine(lat1, lon1, lat2, lon2))
    return {"line": line, "at": [round(m / metres[-1] * MARATHON_MI, 3) for m in metres]}


# The city's planned reopening of each course street (official 2026 street-closure table; miles are
# our course line's): (first mile, last mile, minutes after midnight, street)
REOPEN = [
    (0.00, 1.22, 630, "Columbus Dr and Grand Ave"),
    (1.22, 2.29, 660, "Dearborn St"),
    (2.29, 4.74, 690, "LaSalle St"),
    (4.74, 7.71, 720, "Stockton Dr to Inner Lake Shore Dr"),
    (7.71, 8.95, 750, "Broadway, Sheridan to Briar"),
    (8.95, 9.92, 765, "Broadway and Clark St"),
    (9.92, 10.22, 780, "Clark St, Fullerton to Webster"),
    (10.22, 10.95, 795, "Sedgwick St"),
    (10.95, 12.80, 810, "Wells St"),
    (12.80, 13.42, 825, "Wacker Dr"),
    (13.42, 15.52, 840, "Adams St"),
    (15.52, 17.58, 870, "Jackson Blvd and Halsted St"),
    (17.58, 18.32, 885, "Taylor St"),
    (18.32, 19.10, 900, "Loomis St"),
    (19.10, 20.20, 915, "18th St and Halsted St"),
    (20.20, 21.34, 930, "Cermak Rd"),
    (21.34, 22.25, 945, "Wentworth Ave and 26th St"),
    (22.25, 23.31, 960, "Michigan Ave, 26th to 35th"),
    (23.31, 23.87, 975, "Indiana Ave"),
    (23.87, 25.86, 990, "Michigan Ave, 31st to Roosevelt"),
    (25.86, 26.04, 1080, "Roosevelt Rd"),
]

# Where the race was on the course, by the organisers' published plan: the lead wheelchair racers'
# time at each mile (2026 street-closure brochure, p. 2: 7:20 at the start to 8:45 at the finish)
# and the last participants' (2026 media guide, p. 18: each 2-mile stretch cleared 15 minutes after
# the one before, mile 2 at 9:30 to the finish at 3:35 PM).
LEAD = [
    7 * 60 + 20,
    443,
    446,
    449,
    452,
    456,
    459,
    462,
    465,
    469,
    472,
    475,
    478,
    482,
    485,
    488,
    491,
    495,
    498,
    501,
    504,
    508,
    511,
    514,
    517,
    521,
    524,
]  # minutes after midnight at miles 0-26
LAST = [
    (2, 9 * 60 + 30),
    (26, 15 * 60 + 30),
    (MARATHON_MI, 15 * 60 + 35),
]  # (mile, minute), 15 min a mile between


def field(minute: float) -> list[float] | None:
    """[last, first] mile of the course with racers on it at that minute, or None (none yet,
    or all in)."""
    if minute < LEAD[0] or minute > LAST[-1][1]:
        return None
    front = (
        MARATHON_MI
        if minute >= LEAD[-1] + 1
        else next(
            m + (minute - LEAD[m]) / (LEAD[m + 1] - LEAD[m])
            for m in range(26)
            if LEAD[m] <= minute < LEAD[m + 1]
        )
    )
    (m0, t0), (m1, t1) = (LAST[0], LAST[1]) if minute <= LAST[1][1] else (LAST[1], LAST[2])
    back = max(0.0, m0 + (minute - t0) * (m1 - m0) / (t1 - t0))
    return [round(min(back, MARATHON_MI), 2), round(min(front, MARATHON_MI), 2)]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(a))


def groups(con: duckdb.DuckDBPyConnection) -> None:
    """Table rg: each route's group. M = CTA's own "BOA Marathon" detour label (34 routes); S = only
    the set-up/clean-up detours around Columbus Dr and Congress Plaza; O = every other route, but
    #19, an evening United Center special that ran only on Oct 11."""
    con.execute("""CREATE OR REPLACE TABLE rg AS
      WITH lab AS (SELECT rt, bool_or("desc" ILIKE '%BOA%Marathon%') AS m,
                          bool_or("desc" ILIKE '%Set-up%') AS s
                   FROM detours GROUP BY rt)
      SELECT r.rt,
             CASE WHEN lab.m THEN 'M' WHEN lab.s THEN 'S' WHEN r.rt = '19' THEN 'X' ELSE 'O'
             END AS grp
      FROM (SELECT DISTINCT rt FROM pos) r LEFT JOIN lab USING (rt)""")
    # a route's regular stops: those on any of its patterns that isn't one of CTA's reroutes
    con.execute("""CREATE OR REPLACE TABLE regstop AS
      SELECT DISTINCT m.rt, s.stpid FROM seg s JOIN pmeta m USING (pid) WHERE NOT m.is_detour""")


def minute_of_day(col: str, day: str = "day") -> str:
    """Minutes after the service day's midnight (all three days are CDT, UTC-5), a DOUBLE: bin it
    with floor(), since DuckDB's // on a DOUBLE doesn't floor and ::INT rounds."""
    return f"(({col}) - epoch(({day} || ' 00:00:00-05:00')::TIMESTAMPTZ)) / 60"


def series(con: duckdb.DuckDBPyConnection) -> dict:
    """Same buses, fewer stops: per 15 minutes, 04:00-24:00, buses out and stop passes at the
    routes' regular stops, as % of the two normal Sundays' mean; the band is each normal Sunday's
    own % (the Sunday-to-Sunday noise). A bus is out while it reports, and through a 2-60 min
    silence that ends on the same route (Bus Tracker drops buses that leave their pattern)."""
    step, start, end = 15, 4 * 60, 24 * 60
    con.execute(f"""CREATE OR REPLACE TEMP TABLE out_bins AS
      WITH r AS (
        SELECT day, vid, rt, t, lead(t) OVER w AS t_next, lead(rt) OVER w AS rt_next FROM pos
        WINDOW w AS (PARTITION BY day, vid ORDER BY t)),
      c AS (
        SELECT r.day, g.grp, vid, {minute_of_day("t")} AS m0,
               {minute_of_day("t")}
               + CASE WHEN t_next - t <= 120 THEN t_next - t
                      WHEN t_next - t <= 3600 AND rt_next = r.rt THEN t_next - t
                      ELSE 30 END / 60 AS m1
        FROM r JOIN rg g USING (rt))
      SELECT day, grp, bin, count(DISTINCT vid) AS buses
      FROM (SELECT day, grp, vid,
                   unnest(range(floor(m0 / {step})::INT,
                                floor((m1 - 0.001) / {step})::INT + 1)) AS bin
            FROM c)
      GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pass_bins AS
      SELECT p.day, g.grp, floor({minute_of_day("p.tp")} / {step})::INT AS bin, count(*) AS stops
      FROM passes p JOIN rg g USING (rt) JOIN regstop r ON r.rt = p.rt AND r.stpid = p.stpid
      GROUP BY ALL""")
    bins = range(start // step, end // step)
    out = {"start": start, "step": step}
    for grp in ("M", "O"):
        rows = {
            (d, b): (n, 0)
            for d, b, n in con.execute(
                "SELECT day, bin, buses FROM out_bins WHERE grp = ?", [grp]
            ).fetchall()
        }
        for d, b, n in con.execute(
            "SELECT day, bin, stops FROM pass_bins WHERE grp = ?", [grp]
        ).fetchall():
            rows[(d, b)] = (rows.get((d, b), (0, 0))[0], n)
        part = {}
        for k, name in ((0, "buses"), (1, "stops")):

            def v(d, b, rows=rows, k=k):
                return rows.get((d, b), (0, 0))[k]

            norm = [(v(NORMAL[0], b) + v(NORMAL[1], b)) / 2 for b in bins]

            def pct(d, norm=norm):
                return [
                    round(100 * v(d, b) / n, 1) if n else None
                    for b, n in zip(bins, norm, strict=True)
                ]

            a, b_ = pct(NORMAL[0]), pct(NORMAL[1])
            part[name] = pct(MARATHON)
            part[f"{name}_band"] = [
                [min(x, y), max(x, y)] if x is not None else None
                for x, y in zip(a, b_, strict=True)
            ]
        out[grp] = part
    return out


def local(col: str) -> str:
    """A CTA "YYYYMMDD HH:MM" Chicago time as a timestamp."""
    return f"strptime({col}, '%Y%m%d %H:%M')::TIMESTAMP AT TIME ZONE 'America/Chicago'"


def minutes(ts: str) -> str:
    """Minutes after midnight starting Oct 11 (Chicago), rounded."""
    return (
        f"round((epoch({ts}) - epoch(TIMESTAMPTZ '{MARATHON} 00:00:00 America/Chicago')) / 60)::INT"
    )


def cutoff(con: duckdb.DuckDBPyConnection) -> None:
    """Table cut: the route-direction-stops the Marathon cut off. Both normal Sundays had >= 2
    GPS-checked passes there 10:00-12:00 and Oct 11 had none; only the route's regular stops, not
    the first stop of any pattern it ran that day (a trip's first stop is never "passed"). With the
    last pass before 10:00 (dark), its first missed bus (missed: when it lost its buses) and the
    first pass after 12:00 (back), in minutes."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE w AS
      SELECT day, rt, rtdir, stpid, count(*) AS n FROM passes
      WHERE {minute_of_day("tp")} >= 600 AND {minute_of_day("tp")} < 720 GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TABLE cut AS
      WITH firsts AS (
        SELECT DISTINCT m.rt, arg_min(s.stpid, s.seq) AS stpid FROM seg s JOIN pmeta m USING (pid)
        WHERE pid IN (SELECT DISTINCT pid FROM pos WHERE day = '{MARATHON}') GROUP BY m.rt, pid),
      c AS (
        SELECT rt, rtdir, stpid FROM w WHERE day = '{NORMAL[0]}' AND n >= 2
        INTERSECT SELECT rt, rtdir, stpid FROM w WHERE day = '{NORMAL[1]}' AND n >= 2
        EXCEPT SELECT rt, rtdir, stpid FROM w WHERE day = '{MARATHON}')
      SELECT c.rt, c.rtdir, c.stpid, any_value(p.stpnm) AS stpnm, any_value(p.lat) AS lat,
             any_value(p.lon) AS lon,
             {
        minutes(
            "to_timestamp(max(p.tp) FILTER (WHERE p.day = '"
            + MARATHON
            + "' AND p.tp < epoch(TIMESTAMPTZ '"
            + MARATHON
            + " 10:00:00 America/Chicago')))"
        )
    } AS dark,
             {
        minutes(
            "to_timestamp(min(p.tp) FILTER (WHERE p.day = '"
            + MARATHON
            + "' AND p.tp > epoch(TIMESTAMPTZ '"
            + MARATHON
            + " 12:00:00 America/Chicago')))"
        )
    } AS back
      FROM c JOIN rg g USING (rt) JOIN regstop r USING (rt, stpid)
      LEFT JOIN passes p ON p.rt = c.rt AND p.rtdir = c.rtdir AND p.stpid = c.stpid
      WHERE g.grp = 'M' AND (c.rt, c.stpid) NOT IN (SELECT rt, stpid FROM firsts)
      GROUP BY c.rt, c.rtdir, c.stpid""")
    # when it lost its buses: its first missed bus, the first time after its last one (5 min on,
    # past that same trip) and after 4 AM (a Saturday-night bus 6 min early made #77's lakefront
    # "missed" at 12:56 AM; its next is at 7:09) that both normal Sundays had one, the later of
    # the two. The last bus
    # alone misleads: #6's came at 1:00 AM, Saturday night's, and #6 has none before ~5:30 on any
    # Sunday; with no bus since midnight it's the first one after midnight
    con.execute("ALTER TABLE cut ADD COLUMN missed INTEGER")
    con.execute(f"""UPDATE cut SET missed = x.missed FROM (
      SELECT c.rt, c.rtdir, c.stpid,
             greatest(min(p.m) FILTER (WHERE p.day = '{NORMAL[0]}'),
                      min(p.m) FILTER (WHERE p.day = '{NORMAL[1]}'))::INT AS missed
      FROM cut c
      JOIN (SELECT day, rt, rtdir, stpid, {minute_of_day("tp")} AS m FROM passes
            WHERE day IN ('{NORMAL[0]}', '{NORMAL[1]}')) p USING (rt, rtdir, stpid)
      WHERE p.m > greatest(coalesce(c.dark, -10) + 5, 240)
      GROUP BY ALL) x
      WHERE cut.rt = x.rt AND cut.rtdir = x.rtdir AND cut.stpid = x.stpid""")


def half(times: list, n: int):
    """When half of n stops had it, from the sorted times of those that did (None: not half)."""
    k = (n + 1) // 2 - 1
    return times[k] if k < len(times) else None


def prune_cut(con: duckdb.DuckDBPyConnection) -> None:
    """Leaves out of the cut-off stops those the data can't call cut off, as the stop finder does:
    stops of the shuttles Bus Tracker never showed ("can't tell"), and stops the route's own buses
    drove past (a drive-by) or served at the same corner (a twin: CTA's reroute stopping within
    60 m, e.g. #9 at Ashland & Harrison, 54 m away) between 10 AM and noon (Oct 12 check)."""
    n = con.execute("SELECT count(*) FROM cut").fetchone()[0]
    con.execute("DELETE FROM cut WHERE stpid IN (SELECT stpid FROM shuttle_stops)")
    con.execute(f"""DELETE FROM cut c USING visits_aug v
      WHERE v.day = '{MARATHON}' AND v.kind IN ('driveby', 'twin') AND v.rt = c.rt
        AND v.stpid = c.stpid AND v.sec >= 36000 AND v.sec < 43200""")
    say("cut-off route-stops:", n, "->", con.execute("SELECT count(*) FROM cut").fetchone()[0])


def timeline(con: duckdb.DuckDBPyConnection) -> dict:
    """Per Marathon route: CTA's detour flag (on, off; null = still on at our last poll next
    morning), when its cut-off stops lost their buses (the median first missed bus) and got them
    back (first, median, last), against the window CTA's Customer Alerts gave."""
    # CTA's alerts give local times without an offset ("2026-10-11T06:30:00")
    window = con.execute(
        f"""SELECT {minutes("min(event_start::TIMESTAMP AT TIME ZONE 'America/Chicago')")},
                   {minutes("max(event_end::TIMESTAMP AT TIME ZONE 'America/Chicago')")}
            FROM alerts WHERE service_type = 'B' AND full_desc ILIKE '%marathon%'
              AND event_start LIKE '{MARATHON}%'"""
    ).fetchone()
    flags = {
        rt: (on, None if still else off)
        for rt, on, off, still in con.execute(f"""
      SELECT rt, {minutes("min(fetched_at) FILTER (WHERE st = 1)")},
             {minutes("max(fetched_at) FILTER (WHERE st = 1)")},
             max(fetched_at) FILTER (WHERE st = 1) = (SELECT max(fetched_at) FROM detours)
      FROM detours WHERE "desc" ILIKE '%BOA%Marathon%' GROUP BY rt""").fetchall()
    }
    stops = {}
    for rt, lost, back in con.execute("SELECT rt, missed, back FROM cut").fetchall():
        stops.setdefault(rt, []).append((lost, back))
    routes = []
    for rt, (on, off) in flags.items():
        st = stops.get(rt, [])
        losts = sorted(d for d, _ in st if d is not None)
        backs = sorted(b for _, b in st if b is not None)
        routes.append(
            {
                "rt": rt,
                "flag": [on, off],
                "cut": len(st),
                "out": losts[len(losts) // 2] if losts else on,
                # first, half of them all (those never back count as never: null when that's
                # more than half), last; and how many never had one
                "back": [backs[0], half(backs, len(st)), backs[-1]] if backs else None,
                "never": len(st) - len(backs),
            }
        )
    # each route's own Marathon alerts, in minutes after Oct 11's midnight (some ran Oct 8 to
    # Oct 12: #6, #10 and J14's set-up and clean-up reroute, #126 and #147's until 8 PM)
    own: dict = {}
    for rt, start, end in con.execute(f"""
      SELECT DISTINCT service_id,
             {minutes("event_start::TIMESTAMP AT TIME ZONE 'America/Chicago'")},
             {minutes("event_end::TIMESTAMP AT TIME ZONE 'America/Chicago'")}
      FROM alerts WHERE service_type = 'B' AND full_desc ILIKE '%marathon%'
        AND event_start < '{MARATHON}T23:59' AND event_end > '{MARATHON}T00:00'
      ORDER BY 1, 2, 3""").fetchall():
        own.setdefault(rt, []).append([start, end])
    names = dict(con.execute("SELECT route_id, route_long_name FROM gtfs_routes").fetchall())
    sunday = (
        "SELECT route, avg_sunday_rides "
        f"FROM read_csv('{RIDERSHIP}', types = {{'route': 'VARCHAR'}})"
    )
    rides = dict(con.execute(sunday).fetchall())
    for r in routes:
        r["name"], r["rides"] = names.get(r["rt"], "").strip(), rides.get(r["rt"])
        r["alerts"] = own.get(r["rt"], [])
    routes.sort(key=lambda r: (r["flag"][1] is None, r["flag"][1] or 0, r["rt"]))
    return {"alerts": list(window), "routes": routes}


KX, KY = 82_990.0, 110_540.0  # metres per degree of longitude / latitude at Chicago


def far_runs(path: list, other: list, far: float = 60.0, min_len: float = 100.0) -> list[list]:
    """Stretches of path (lon, lat) more than `far` metres from the line `other`, at least min_len
    long, each with one point either side so it meets the shared streets."""
    pts = []
    for a, b in zip(path, path[1:], strict=False):  # every ~10 m
        n = max(1, int(length([a, b]) // 10))
        pts += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]
    pts.append(path[-1])
    cells: dict = {}  # the other line's segments by 200 m cell, to test distance quickly
    for a, b in zip(other, other[1:], strict=False):
        for cx in range(int(min(a[0], b[0]) * KX // 200), int(max(a[0], b[0]) * KX // 200) + 1):
            for cy in range(int(min(a[1], b[1]) * KY // 200), int(max(a[1], b[1]) * KY // 200) + 1):
                cells.setdefault((cx, cy), []).append((a, b))

    def dist(p) -> float:
        cx, cy = int(p[0] * KX // 200), int(p[1] * KY // 200)
        best = 1e9
        for k in ((cx + i, cy + j) for i in (-1, 0, 1) for j in (-1, 0, 1)):
            for a, b in cells.get(k, ()):
                ax, ay, bx, by, px, py = (
                    a[0] * KX,
                    a[1] * KY,
                    b[0] * KX,
                    b[1] * KY,
                    p[0] * KX,
                    p[1] * KY,
                )
                dx, dy = bx - ax, by - ay
                u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
                best = min(best, ((ax + u * dx - px) ** 2 + (ay + u * dy - py) ** 2) ** 0.5)
        return best

    flags = [dist(p) > far for p in pts]
    runs, i = [], 0
    while i < len(pts):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < len(pts) and flags[j]:
            j += 1
        seg = pts[max(0, i - 1) : j + 1]
        if length(seg) >= min_len:
            runs.append(simplify(seg))
        i = j
    return runs


def length(line: list) -> float:
    return sum(
        (((b[0] - a[0]) * KX) ** 2 + ((b[1] - a[1]) * KY) ** 2) ** 0.5
        for a, b in zip(line, line[1:], strict=False)
    )


def simplify(line: list, tol: float = 10.0) -> list:
    """Douglas-Peucker to `tol` metres, coordinates to 5 decimals (~1 m)."""

    def rec(lo: int, hi: int, keep: set) -> None:
        a, b = line[lo], line[hi]
        dx, dy = (b[0] - a[0]) * KX, (b[1] - a[1]) * KY
        worst, at = 0.0, None
        for i in range(lo + 1, hi):
            px, py = (line[i][0] - a[0]) * KX, (line[i][1] - a[1]) * KY
            u = max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy or 1)))
            d = ((u * dx - px) ** 2 + (u * dy - py) ** 2) ** 0.5
            if d > worst:
                worst, at = d, i
        if at is not None and worst > tol:
            keep.add(at)
            rec(lo, at, keep)
            rec(at, hi, keep)

    keep = {0, len(line) - 1}
    rec(0, len(line) - 1, keep)
    return [[round(line[i][0], 5), round(line[i][1], 5)] for i in sorted(keep)]


def main_reroutes(con: duckdb.DuckDBPyConnection) -> None:
    """dmain: per Marathon route-direction, the reroute pattern most of its buses drove 08:00-14:00
    on Oct 11; dskip: the stops of the regular path it replaces (its dtrpt, but its first stop) that
    it doesn't serve: the stops CTA's own reroute skipped."""
    con.execute(f"""CREATE OR REPLACE TABLE dmain AS
      SELECT p.rt, m.rtdir, arg_max(p.pid, n) AS pid FROM (
        SELECT rt, pid, count(*) AS n FROM pos WHERE day = '{MARATHON}'
          AND t BETWEEN epoch(TIMESTAMPTZ '{MARATHON} 08:00:00 America/Chicago')
                    AND epoch(TIMESTAMPTZ '{MARATHON} 14:00:00 America/Chicago')
        GROUP BY ALL) p JOIN pmeta m USING (pid) JOIN rg g ON g.rt = p.rt
      WHERE m.is_detour AND g.grp = 'M' GROUP BY ALL""")
    con.execute("""CREATE OR REPLACE TABLE dregular AS
      SELECT d.rt, d.rtdir, d.pid, s.stpid, s.stpnm, s.seq,
             s.stpid NOT IN (SELECT stpid FROM ppoints x
                             WHERE x.pid = d.pid AND x.kind = 'pt' AND x.typ = 'S') AS skipped
      FROM dmain d JOIN ppoints s ON s.pid = d.pid AND s.kind = 'dtrpt' AND s.typ = 'S'
      QUALIFY s.seq > min(s.seq) OVER (PARTITION BY d.pid)""")


def reroutes(con: duckdb.DuckDBPyConnection) -> dict:
    """Where the buses went: per Marathon route-direction, CTA's reroute that most of its buses
    drove on Oct 11 (08:00-14:00), as its whole path (route), the regular streets it left (its
    dtrpt, where more than 60 m from the path driven) and the streets driven instead; the stops
    it cut off (table cut) with when they lost and got back a bus; and where buses stood waiting
    that normal Sundays never had them."""
    main = con.execute("SELECT rt, rtdir, pid FROM dmain ORDER BY 1, 2").fetchall()
    paths: dict = {}
    for pid, kind, lon, lat in con.execute(
        """SELECT pid, kind, lon, lat FROM ppoints
           WHERE pid IN (SELECT unnest(?)) ORDER BY pid, kind, seq""",
        [[m[2] for m in main]],
    ).fetchall():
        paths.setdefault((pid, kind), []).append((lon, lat))
    cut: dict = {}
    for rt, rtdir, lon, lat, name, lost, back in con.execute(
        "SELECT rt, rtdir, lon, lat, stpnm, missed, back FROM cut"
    ).fetchall():
        cut.setdefault((rt, rtdir), []).append(
            (rt, rtdir[0], round(lon, 5), round(lat, 5), name.strip(), lost, back)
        )
    normal = dict(
        ((rt, d), n)
        for rt, d, n in con.execute(f"""
      SELECT rt, rtdir, count(DISTINCT stpid) FROM w WHERE day = '{NORMAL[0]}' AND n >= 2
      GROUP BY ALL""").fetchall()
    )
    lines, routes, stops = [], [], []
    for rt, rtdir, pid in main:
        regular, driven = paths.get((pid, "dtrpt"), []), paths.get((pid, "pt"), [])
        # the whole path driven, under the changes: without it a picked route showed only the bit
        # that changed (#66 cut short at Chicago & Orleans read as a few blocks, not a route from
        # Austin, Oct 12); simplified to 8 m, plenty at the map's zoom
        kept = simplify(driven, 8.0)
        if len(kept) > 1:
            lines.append(
                {
                    "type": "Feature",
                    "properties": {"rt": rt, "dir": rtdir[0], "kind": "route"},
                    "geometry": {"type": "LineString", "coordinates": kept},
                }
            )
        km = 0.0
        for kind, path, other in (("skipped", regular, driven), ("instead", driven, regular)):
            for run in far_runs(path, other):
                lines.append(
                    {
                        "type": "Feature",
                        "properties": {"rt": rt, "dir": rtdir[0], "kind": kind},
                        "geometry": {"type": "LineString", "coordinates": run},
                    }
                )
                km += length(run) / 1000 if kind == "skipped" else 0
        mine = cut.get((rt, rtdir), [])
        backs = sorted(b for *_, b in mine if b is not None)
        stops += mine
        routes.append(
            {
                "rt": rt,
                "dir": rtdir[0],
                "stops": [len(mine), normal.get((rt, rtdir), 0)],
                "km": {"not_driven": round(km, 1)},
                "back": [backs[0], backs[len(backs) // 2], backs[-1]] if backs else None,
            }
        )
    terminals = waiting_places(con)
    # the view: what changed, not the routes' whole paths (with them it opened on the suburbs)
    xs = [
        c[0]
        for f in lines
        if f["properties"]["kind"] != "route"
        for c in f["geometry"]["coordinates"]
    ]
    ys = [
        c[1]
        for f in lines
        if f["properties"]["kind"] != "route"
        for c in f["geometry"]["coordinates"]
    ]
    return {
        "bounds": [[min(xs), min(ys)], [max(xs), max(ys)]],
        "routes": routes,
        "lines": lines,
        "stops": stops,
        "terminals": sorted(terminals, key=lambda t: -t["hours"]),
    }


def trip_states(con: duckdb.DuckDBPyConnection) -> None:
    """Table rep: each report with what its bus was doing. A trip run = one bus's consecutive
    reports with the same pattern and scheduled trip, no silence over 15 min; within it a report is
    'pre' until the bus gets 300 ft past the lowest pdist of the run (waiting to leave), 'post' from
    when it gets within 300 ft of the highest (arrived, at a terminal temporary or not), 'mid'
    between; 'still' if the run never covers 1,000 ft. w = the seconds a report stands for (to the
    bus's next report, at most 120; 30 for its last)."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE rep0 AS
      WITH p AS (
        SELECT g.day, g.vid, g.t, g.rt, g.pid, g.pdist, g.on_route, g.lat, g.lon, g.stst, g.stsd,
               lag(g.t) OVER w AS t_prev, lead(g.t) OVER w AS t_next, lead(g.rt) OVER w AS rt_next,
               lag(g.pid) OVER w AS pid_prev, lag(g.stst) OVER w AS stst_prev,
               lag(g.stsd) OVER w AS stsd_prev
        FROM ping g WINDOW w AS (PARTITION BY g.day, g.vid ORDER BY g.t)),
      q AS (
        SELECT *, least(coalesce(t_next - t, 30), 120) AS w,
               (t_prev IS NULL OR pid IS DISTINCT FROM pid_prev OR stst IS DISTINCT FROM stst_prev
                OR stsd IS DISTINCT FROM stsd_prev OR t - t_prev > {RUN_GAP_S})::INT AS new_run
        FROM p)
      SELECT *, sum(new_run) OVER (PARTITION BY day, vid ORDER BY t) AS run_no FROM q""")
    con.execute("""CREATE OR REPLACE TEMP TABLE runs AS
      WITH a AS (SELECT day, vid, run_no, min(pdist) AS lo, max(pdist) AS hi, min(t) AS t_first
                 FROM rep0 GROUP BY ALL),
           b AS (SELECT a.day, a.vid, a.run_no,
                        min(r.t) FILTER (WHERE r.pdist > a.lo + 300) AS t_move,
                        min(r.t) FILTER (WHERE r.pdist >= a.hi - 300) AS t_arr
                 FROM rep0 r JOIN a USING (day, vid, run_no) GROUP BY ALL),
           c AS (SELECT b.day, b.vid, b.run_no, max(r.t) FILTER (WHERE r.t < b.t_move) AS t_dep
                 FROM rep0 r JOIN b USING (day, vid, run_no) GROUP BY ALL)
      SELECT a.*, b.t_arr, c.t_dep, a.hi - a.lo >= 1000 AS moving
      FROM a JOIN b USING (day, vid, run_no) JOIN c USING (day, vid, run_no)""")
    con.execute("""CREATE OR REPLACE TABLE rep AS
      SELECT r.*, CASE WHEN NOT u.moving THEN 'still'
                       WHEN r.t <= coalesce(u.t_dep, u.t_first - 1) THEN 'pre'
                       WHEN r.t >= u.t_arr THEN 'post' ELSE 'mid' END AS state
      FROM rep0 r JOIN runs u USING (day, vid, run_no)""")


def waiting_places(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Where buses waited on Marathon day that they don't on a normal Sunday: bus-time waiting to
    leave or after arriving (states pre, post, still; on route), plus silences of 2-60 min that end
    within 300 m of where they began (it sat there), 07:00-18:00, by ~150 m cell (0.0015 deg lat x
    0.002 deg lon). Cells with >= 3 hours on Oct 11 and <= 1 on each normal Sunday, merged within
    300 m and named by the nearest stop."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE wc AS
      WITH wt AS (
        SELECT r.day, r.rt, r.vid, r.lat, r.lon,
               r.w + CASE WHEN r.t_next - r.t > 120 AND r.t_next - r.t <= 3600 AND r.rt_next = r.rt
                           AND sqrt(pow((n.lat - r.lat) * {KY}, 2)
                                    + pow((n.lon - r.lon) * {KX}, 2)) < 300
                          THEN r.t_next - r.t - 120 ELSE 0 END AS sec
        FROM rep r LEFT JOIN rep n ON n.day = r.day AND n.vid = r.vid AND n.t = r.t_next
        WHERE r.on_route AND r.state IN ('pre', 'post', 'still')
          AND {minute_of_day("r.t", "r.day")} BETWEEN 7 * 60 AND 18 * 60)
      SELECT floor(lat / 0.0015)::INT AS cy, floor(lon / 0.002)::INT AS cx,
             sum(sec) FILTER (WHERE day = '{MARATHON}') / 3600 AS h,
             coalesce(sum(sec) FILTER (WHERE day = '{NORMAL[0]}'), 0) / 3600 AS h1,
             coalesce(sum(sec) FILTER (WHERE day = '{NORMAL[1]}'), 0) / 3600 AS h2,
             list(DISTINCT rt ORDER BY rt) FILTER (WHERE day = '{MARATHON}') AS rts,
             list(DISTINCT vid) FILTER (WHERE day = '{MARATHON}') AS vids,
             avg(lat) FILTER (WHERE day = '{MARATHON}') AS lat,
             avg(lon) FILTER (WHERE day = '{MARATHON}') AS lon
      FROM wt GROUP BY ALL""")
    cells = con.execute(f"""
      WITH st AS (SELECT DISTINCT stpnm, lat, lon FROM seg)
      SELECT w.lat, w.lon, w.h, w.h1, w.h2, w.rts, w.vids,
             (SELECT stpnm FROM st
              ORDER BY pow((st.lat - w.lat) * {KY}, 2) + pow((st.lon - w.lon) * {KX}, 2) LIMIT 1)
      FROM wc w WHERE h >= 3 AND h1 <= 1 AND h2 <= 1 ORDER BY h DESC""").fetchall()
    places: list[dict] = []
    for lat, lon, h, h1, h2, rts, vids, name in cells:
        near = next(
            (
                p
                for p in places
                if ((p["lat"] - lat) * KY) ** 2 + ((p["lon"] - lon) * KX) ** 2 <= 300**2
            ),
            None,
        )
        if near:
            near["lat"] = (near["lat"] * near["hours"] + lat * h) / (near["hours"] + h)
            near["lon"] = (near["lon"] * near["hours"] + lon * h) / (near["hours"] + h)
            near["hours"] += h
            near["normal"] += (h1 + h2) / 2
            near["routes"] = sorted(set(near["routes"]) | set(rts), key=lambda r: (len(r), r))
            near["vids"] |= set(vids)
        else:
            places.append(
                {
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "hours": h,
                    "normal": (h1 + h2) / 2,
                    "routes": sorted(rts, key=lambda r: (len(r), r)),
                    "vids": set(vids),
                }
            )
    # how many buses: "21 hours" alone read as one bus waiting 21 hours (Oct 12)
    return [
        {
            **{k: v for k, v in p.items() if k != "vids"},
            "buses": len(p["vids"]),
            "lat": round(p["lat"], 5),
            "lon": round(p["lon"], 5),
            "hours": round(p["hours"], 1),
            "normal": round(p["normal"], 1),
        }
        for p in places
    ]


SITE_BUCKET = "ghost-ledger-site-354894954112"
PREFIX = "data/reports/marathon-2026"


def publish(out: Path) -> None:
    """Upload --out to the site bucket (5 minutes at the edge, so a correction shows quickly)."""
    import boto3

    s3 = boto3.client("s3")
    for f in sorted(out.glob("*.json")):
        s3.upload_file(
            str(f),
            SITE_BUCKET,
            f"{PREFIX}/{f.name}",
            ExtraArgs={"ContentType": "application/json", "CacheControl": "public, max-age=300"},
        )
        say(f"published s3://{SITE_BUCKET}/{PREFIX}/{f.name} ({f.stat().st_size / 1024:.0f} KB)")


def at(minute: int | None) -> str | None:
    """Minutes after midnight as "1:39 PM"."""
    if minute is None:
        return None
    h, m = divmod(int(minute) % 1440, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def pct(a: float, b: float) -> float:
    return round(100 * a / b, 1) if b else 0.0


def expected_waits(con: duckdb.DuckDBPyConnection, lo: int = 7 * 60, hi: int = 16 * 60) -> dict:
    """Expected wait (minutes) for a rider arriving at a random moment lo-hi at the Marathon routes'
    stops that kept a bus every hour (on all three days, so the same stops on each), stop-weighted:
    the time to the next GPS-checked pass, looking past the window's end for it. A bus passing the
    same stop again within 15 min counts once; no first or last stop of any pattern run that day."""
    rows = con.execute(f"""
      WITH ends AS (
        SELECT DISTINCT p.day, s.stpid
        FROM (SELECT DISTINCT day, pid FROM pos) p JOIN seg s USING (pid)
        QUALIFY s.seq = min(s.seq) OVER (PARTITION BY p.day, s.pid)
             OR s.seq = max(s.seq) OVER (PARTITION BY p.day, s.pid)),
      x AS (
        SELECT p.day, p.rt, p.rtdir, p.stpid, p.vid, {minute_of_day("p.tp", "p.day")} AS m
        FROM passes p JOIN rg g USING (rt)
        WHERE g.grp = 'M' AND (p.day, p.stpid) NOT IN (SELECT day, stpid FROM ends)
        QUALIFY coalesce({minute_of_day("p.tp", "p.day")}
                         - lag({minute_of_day("p.tp", "p.day")})
                             OVER (PARTITION BY p.day, p.vid, p.stpid ORDER BY p.tp), 999) > 15),
      hours AS (
        SELECT rt, rtdir, stpid FROM x WHERE m >= {lo} AND m < {hi}
        GROUP BY rt, rtdir, stpid
        HAVING count(DISTINCT (day, floor(m / 60)::INT)) = 3 * {(hi - lo) // 60})
      SELECT day, rt, rtdir, stpid, list(m ORDER BY m)
      FROM x JOIN hours USING (rt, rtdir, stpid) GROUP BY ALL""").fetchall()
    out: dict = {}
    for day, _, _, _, ms in rows:
        total, t = 0.0, lo  # integrate the wait over arrivals lo..hi
        for m in ms:
            if m <= t:
                continue
            seg_end = min(m, hi)
            total += (seg_end - t) * ((m - t) + (m - seg_end)) / 2
            t = seg_end
            if t >= hi:
                break
        if t < hi:  # no bus after: not a kept stop then (can't happen with a bus every hour)
            continue
        out.setdefault(day, []).append(total / (hi - lo))
    return {d: round(sum(v) / len(v), 2) for d, v in out.items()} | {
        "stops": len(rows) // 3,
        "physical_stops": len({stpid for _, _, _, stpid, _ in rows}),
    }


def facts(con: duckdb.DuckDBPyConnection, files: dict, work: Path) -> dict:
    """Every number the page's text states, computed here (site/src/data/marathon-2026.json)."""
    f: dict = {}
    f["routes"] = con.execute("SELECT count(*) FROM rg WHERE grp = 'M'").fetchone()[0]
    # the detour window 06:30-18:00 in the series' 15-minute bins: buses out and usual-stop visits
    b0, b1 = 390 // 15, 1080 // 15
    for name, table, col in (("buses", "out_bins", "buses"), ("visits", "pass_bins", "stops")):
        v = dict(
            con.execute(
                f"""SELECT day, sum({col}) FROM {table}
                    WHERE grp = 'M' AND bin >= {b0} AND bin < {b1} GROUP BY day"""
            ).fetchall()
        )
        normal = (v[NORMAL[0]] + v[NORMAL[1]]) / 2
        f[f"{name}_pct"] = pct(v[MARATHON], normal)
        f[f"{name}_change"] = round(100 * v[MARATHON] / normal - 100)  # whole %, exact ratio
        f[f"{name}_noise"] = round(abs(v[NORMAL[0]] / v[NORMAL[1]] - 1) * 100, 1)  # the two differ
    # trips on CTA's reroute patterns: each scheduled trip (route, service date, start) by the
    # pattern most of its reports carried, a tie to the lower pid (the regular one: a reroute's
    # are 500000+, so ties never count as rerouted; arg_max alone broke ties at random: 73.3 or
    # 73.4% between runs); scheduled starts 06:30-18:00 (as trips_run)
    trips = con.execute("""
      SELECT day, count(*), count(*) FILTER (WHERE is_detour) FROM (
        SELECT day, rt, stsd, stst, arg_max(pid, n * 10000000 - pid) AS pid FROM (
          SELECT day, rt, stsd, stst, pid, count(*) AS n FROM pos
          WHERE stst BETWEEN 6.5 * 3600 AND 18 * 3600 GROUP BY ALL HAVING n >= 4)
        GROUP BY ALL) t JOIN pmeta m ON m.pid = t.pid JOIN rg g ON g.rt = t.rt
      WHERE g.grp = 'M' GROUP BY day""").fetchall()
    f["trips_on_reroutes"] = {d: pct(r, n) for d, n, r in trips}
    st = files["stops.json"]
    peak = max(st["dark"])
    f["dark_peak"], f["dark_peak_at"] = (
        peak,
        [at(st["start"] + i * st["step"]) for i, n in enumerate(st["dark"]) if n == peak],
    )
    race = [
        i for i in range(st["frames"]) if 7 * 60 + 30 <= st["start"] + i * st["step"] <= 15 * 60
    ]
    f["dark_median_race"] = sorted(st["dark"][i] for i in race)[len(race) // 2]
    f["dark_noise_median_race"] = sorted(st["noise"][i] for i in race)[len(race) // 2]
    f["dark_noise_max"] = max(st["noise"])
    # stop-hours dark (a frame is 10 minutes), Oct 11 against each normal Sunday judged by the other
    shuttles = "stpid NOT IN (SELECT stpid FROM shuttle_stops)"  # can't tell there
    f["dark_stop_hours"] = {
        k: round(n / 6)
        for k, n in con.execute(
            f"SELECT 'marathon', count(*) FILTER (WHERE dark AND {shuttles}) FROM d_m_x UNION ALL "
            f"SELECT 'sep27', count(*) FILTER (WHERE dark AND {shuttles}) FROM d_s_o_x UNION ALL "
            f"SELECT 'oct4', count(*) FILTER (WHERE dark AND {shuttles}) FROM d_o_s_x"
        ).fetchall()
    }
    f["dark_stops"] = con.execute(
        "SELECT count(DISTINCT stpid) FROM d_m_x "
        "WHERE dark AND stpid NOT IN (SELECT stpid FROM shuttle_stops)"
    ).fetchone()[0]
    f["no_bus_730_to_3"] = {}
    for key, day, table in (
        ("marathon", MARATHON, "d_m_x"),
        ("sep27", NORMAL[0], "d_s_o_x"),
        ("oct4", NORMAL[1], "d_o_s_x"),
    ):
        f["no_bus_730_to_3"][key] = con.execute(f"""
          SELECT count(*) FROM (
            SELECT stpid FROM {table}
            WHERE T BETWEEN 27000 AND 54000 AND stpid NOT IN (SELECT stpid FROM shuttle_stops)
            GROUP BY stpid HAVING bool_and(dark))
          WHERE stpid NOT IN (SELECT stpid FROM visits_aug
                              WHERE day = '{day}' AND sec BETWEEN 27000 AND 54000)""").fetchone()[0]
    f["dark_stops_by_day"] = {
        key: con.execute(f"""SELECT count(DISTINCT stpid) FROM {table}
                             WHERE dark
                               AND stpid NOT IN (SELECT stpid FROM shuttle_stops)""").fetchone()[0]
        for key, table in (("marathon", "d_m_x"), ("sep27", "d_s_o_x"), ("oct4", "d_o_s_x"))
    }
    # at the peak: how many of the dark stops had seen no bus since before 7:00, and how many lie
    # beyond the map's box; through the race, the smallest multiple of the noise
    i = st["dark"].index(peak)
    t_peak = (st["start"] + i * st["step"]) * 60
    (x0, y0), (x1, y1) = st["bounds"]
    n, early, off = con.execute(f"""
      SELECT count(*), count(*) FILTER (WHERE d.gap < 7 * 3600),
             count(*) FILTER (WHERE NOT (sd.lon BETWEEN {x0} AND {x1}
                                         AND sd.lat BETWEEN {y0} AND {y1}))
      FROM d_m_x d JOIN stop_dim sd USING (stpid)
      WHERE d.T = {t_peak} AND d.dark
        AND stpid NOT IN (SELECT stpid FROM shuttle_stops)""").fetchone()
    f["dark_peak_since_7am_pct"] = round(100 * early / n)
    f["dark_peak_offmap"] = off
    f["dark_peak_offmap_routes"] = dict(
        con.execute(f"""
      SELECT sd.routes_normal, count(*) FROM d_m_x d JOIN stop_dim sd USING (stpid)
      WHERE d.T = {t_peak} AND d.dark AND stpid NOT IN (SELECT stpid FROM shuttle_stops)
        AND NOT (sd.lon BETWEEN {x0} AND {x1} AND sd.lat BETWEEN {y0} AND {y1})
      GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    )
    ratio = min(st["dark"][i] / max(1, st["noise"][i]) for i in race)
    f["dark_ratio_min_race"] = int(ratio * 10) / 10  # "at least": floored, never rounded up
    f["cant_tell_stops"] = con.execute("SELECT count(*) FROM shuttle_stops").fetchone()[0]
    # the Loop at 12:40: stops within 1 km of State & Madison normally in service then, and dark
    f["loop_1240"] = dict(
        zip(
            ("served_normally", "dark"),
            con.execute(f"""
      SELECT count(*) FILTER (WHERE c.s60 >= 1 AND c.o60 >= 1), count(*) FILTER (WHERE d.dark)
      FROM cs_x c JOIN d_m_x d USING (stpid, f) JOIN stop_dim sd USING (stpid)
      WHERE c.T = 12 * 3600 + 40 * 60
        AND sqrt(pow((sd.lat - 41.88207) * {KY}, 2) + pow((sd.lon + 87.62781) * {KX}, 2))
            <= 1000""").fetchone(),
            strict=True,
        )
    )
    # named examples: the outage around noon (last visit before, first after) and how many buses
    # each normal Sunday had at that stop in those same hours
    f["examples"] = []
    for stpid in ("1437", "15292"):
        name, before, after = con.execute(f"""
          SELECT any_value(sd.stpnm), max(v.sec) FILTER (WHERE v.sec < 12 * 3600),
                 min(v.sec) FILTER (WHERE v.sec > 12 * 3600)
          FROM visits_aug v JOIN stop_dim sd USING (stpid)
          WHERE v.day = '{MARATHON}' AND stpid = '{stpid}'""").fetchone()
        normal = [
            n
            for (n,) in con.execute(
                f"""SELECT count(*) FROM visits
                    WHERE stpid = '{stpid}' AND sec > {before} AND sec < {after}
                      AND day IN ('{NORMAL[0]}', '{NORMAL[1]}') GROUP BY day ORDER BY day"""
            ).fetchall()
        ]
        routes = con.execute(
            f"SELECT list(DISTINCT rt ORDER BY rt) FROM visits WHERE stpid = '{stpid}' "
            f"AND day IN ('{NORMAL[0]}', '{NORMAL[1]}')"
        ).fetchone()[0]
        f["examples"].append(
            {
                "stop": name,
                "routes": routes,
                "last": at(before // 60),
                "next": at(after // 60),
                "normal_buses": normal,
            }
        )
    c = con.execute(
        """SELECT count(*), count(DISTINCT stpid), count(*) FILTER (WHERE back <= 960),
                  max(back), count(*) FILTER (WHERE back IS NULL)
           FROM cut"""
    ).fetchone()
    (
        f["cut_route_stops"],
        f["cut_stops"],
        f["cut_back_by_4pm"],
        f["cut_last_back"],
        f["cut_never"],
    ) = c[0], c[1], c[2], at(c[3]), c[4]
    f["cut_back_by_4pm_pct"] = round(100 * c[2] / c[0])
    # "half of the cut-off stops had a bus again by": half of them all, those never back included
    backs = sorted(
        b for (b,) in con.execute("SELECT back FROM cut WHERE back IS NOT NULL").fetchall()
    )
    f["cut_back_median"] = at(backs[(c[0] + 1) // 2 - 1])
    f["cut_never_routes"] = [
        r
        for (r,) in con.execute(
            "SELECT DISTINCT rt FROM cut WHERE back IS NULL ORDER BY rt"
        ).fetchall()
    ]
    t = files["reroutes.json"]["terminals"]
    f["waiting_places"], f["waiting_hours"] = len(t), round(sum(x["hours"] for x in t))
    f["waiting_normal"] = round(sum(x["normal"] for x in t), 1)
    f["waiting_top"] = {
        "name": t[0]["name"],
        "hours": round(t[0]["hours"]),
        "routes": t[0]["routes"],
    }
    f["waits"] = expected_waits(con)
    # CTA's detour flags: when they came on (2-minute polls) and whether in text order of
    # route number
    on = con.execute(
        """SELECT rt, min(fetched_at) FILTER (WHERE st = 1) FROM detours
           WHERE "desc" ILIKE '%BOA%Marathon%' GROUP BY rt"""
    ).fetchall()
    polls: dict = {}
    for rt, ts in on:
        polls.setdefault(ts, []).append(rt)
    order = [sorted(polls[ts]) for ts in sorted(polls)]
    first = {
        ts: m
        for ts, m in con.execute(
            f"SELECT ts, {minutes('ts')} FROM (SELECT unnest(?) AS ts)", [sorted(polls)]
        ).fetchall()
    }
    f["flags_on"] = [[at(first[ts]), sorted(polls[ts])] for ts in sorted(polls)]
    # in text order: every route first flagged in a poll sorts after every route of the polls before
    # (as text, "10" < "124" < "8"), leaving out the first and last poll (#10 at 6:07, #77 at 9:07)
    inner = order[1:-1]
    f["flags_text_order"] = all(max(a) < min(b) for a, b in zip(inner, inner[1:], strict=False))
    tl = files["timeline.json"]
    f["alerts_window"] = [at(m) for m in tl["alerts"]]
    f["flags_off_first"], f["flags_off_last"] = (
        at(min(r["flag"][1] for r in tl["routes"] if r["flag"][1])),
        at(max(r["flag"][1] for r in tl["routes"] if r["flag"][1])),
    )
    f["flags_still_on"] = [r["rt"] for r in tl["routes"] if r["flag"][1] is None]
    # every clock, in minutes after midnight, for the page's "what CTA said" chart: the stops' own
    # clock is when the median cut-off stop lost its buses (its first missed bus) and got them back
    # (the same "half of them all" as cut_back_median: those never back count as never)
    missed = sorted(m for (m,) in con.execute("SELECT missed FROM cut").fetchall())
    backs = sorted(
        b for (b,) in con.execute("SELECT back FROM cut WHERE back IS NOT NULL").fetchall()
    )
    half = (len(missed) + 1) // 2 - 1
    out_median, back_median = missed[half], backs[half]
    flagged = [r["flag"] for r in tl["routes"]]
    f["clocks"] = {
        "alerts": tl["alerts"],
        "flags": [min(on for on, _ in flagged), max(off for _, off in flagged if off)],
        "stops": [out_median, back_median],
        "city_reopen": [REOPEN[0][2], REOPEN[-1][2]],  # its first street to the finish area (6 PM)
    }
    # half the cut-off stops back, minutes after the route's flag came down: the median route
    lags = sorted(r["back"][1] - r["flag"][1] for r in tl["routes"] if r["back"] and r["flag"][1])
    f["back_after_flag_median_min"] = lags[len(lags) // 2]
    # the window CTA's detour records gave, and how many routes were still flagged when the
    # alerts left the feed (the first poll at or after 16:00)
    w0, w1 = con.execute(f"""
      SELECT {minutes(f"min({local('startdt')})")}, {minutes(f"max({local('enddt')})")}
      FROM detours WHERE "desc" ILIKE '%BOA%Marathon%'""").fetchone()
    f["detour_window"] = [at(w0), at(w1)]
    f["clocks"]["detour_records"] = [w0, w1]
    at_four = f"""
      SELECT count(DISTINCT rt) FROM detours WHERE "desc" ILIKE '%BOA%Marathon%' AND st = 1
        AND fetched_at = (SELECT min(fetched_at) FROM detours
                          WHERE fetched_at >= TIMESTAMPTZ '{MARATHON} 16:00:00 America/Chicago')"""
    f["flagged_at_alert_end"] = con.execute(at_four).fetchone()[0]
    # the hour before each route-direction's flag came on: strict passes at the stops CTA's
    # reroute skips
    before = dict(
        con.execute(f"""
      WITH fl AS (SELECT rt, replace(dir, 'bound', '') AS d,
                         {minutes("min(fetched_at) FILTER (WHERE st = 1)")} AS f
                  FROM detours WHERE "desc" ILIKE '%BOA%Marathon%' GROUP BY ALL)
      SELECT p.day, count(*) FROM dregular r JOIN fl ON fl.rt = r.rt AND r.rtdir LIKE fl.d || '%'
      JOIN passes p ON p.rt = r.rt AND p.stpid = r.stpid AND p.dt <= 90
      WHERE r.skipped AND {minute_of_day("p.tp", "p.day")} >= fl.f - 60
        AND {minute_of_day("p.tp", "p.day")} < fl.f
      GROUP BY p.day""").fetchall()
    )
    f["hour_before_flag"] = {d: before.get(d, 0) for d in DAYS}
    # CTA's plan vs the buses, 10:00-13:00: normally served stops (>= 2 strict passes on both
    # Sundays) of each reroute's regular path, skipped or kept by CTA, with or without a strict
    # pass on Oct 11
    m = con.execute(f"""
      WITH ps AS (SELECT r.rt, r.rtdir, r.stpid, r.skipped, p.day, count(*) AS n FROM dregular r
                  JOIN passes p ON p.rt = r.rt AND p.stpid = r.stpid AND p.dt <= 90
                  WHERE {minute_of_day("p.tp", "p.day")} BETWEEN 600 AND 780 GROUP BY ALL),
      s AS (SELECT r.rt, r.rtdir, r.stpid, r.skipped,
                   coalesce(max(n) FILTER (WHERE day = '{MARATHON}'), 0) AS mar,
                   coalesce(max(n) FILTER (WHERE day = '{NORMAL[0]}'), 0) AS a,
                   coalesce(max(n) FILTER (WHERE day = '{NORMAL[1]}'), 0) AS b
            FROM dregular r LEFT JOIN ps USING (rt, rtdir, stpid, skipped) GROUP BY ALL)
      SELECT count(*) FILTER (WHERE skipped), count(*) FILTER (WHERE skipped AND mar = 0),
             count(*) FILTER (WHERE NOT skipped), count(*) FILTER (WHERE NOT skipped AND mar > 0)
      FROM s WHERE a >= 2 AND b >= 2""").fetchone()
    f["cta_plan"] = {"skipped": m[0], "skipped_no_bus": m[1], "kept": m[2], "kept_bus": m[3]}
    # Navy Pier: reports within 400 m of its terminal, 05:00-18:00
    pier = con.execute(f"""
      WITH pier AS (SELECT avg(lat) AS lat, avg(lon) AS lon FROM seg
                    WHERE stpnm = 'Navy Pier Terminal'),
      r AS (SELECT p.day, p.t FROM pos p, pier
            WHERE sqrt(pow((p.lat - pier.lat) * {KY}, 2) + pow((p.lon - pier.lon) * {KX}, 2)) <= 400
              AND {minute_of_day("p.t", "p.day")} BETWEEN 300 AND 1080),
      g AS (SELECT day, t, lead(t) OVER (PARTITION BY day ORDER BY t) AS t2 FROM r)
      SELECT day, {minute_of_day("arg_max(t, t2 - t)")} , {minute_of_day("arg_max(t2, t2 - t)")},
             max(t2 - t) / 60
      FROM g GROUP BY day""").fetchall()
    f["navy_pier"] = {d: {"from": at(a), "to": at(b), "minutes": round(n)} for d, a, b, n in pier}
    # out but not in Bus Tracker: share of the Marathon routes' bus-time 07:00-18:00 inside
    # silences of 2-60 min that end on the same route
    dark = con.execute(f"""
      SELECT r.day, coalesce(m.is_detour, false) AS det,
             sum(CASE WHEN r.t_next - r.t > 120 AND r.t_next - r.t <= 3600 AND r.rt_next = r.rt
                      THEN r.t_next - r.t - 120 ELSE 0 END),
             sum(r.w + CASE WHEN r.t_next - r.t > 120 AND r.t_next - r.t <= 3600
                             AND r.rt_next = r.rt THEN r.t_next - r.t - 120 ELSE 0 END)
      FROM rep r JOIN rg g USING (rt) LEFT JOIN pmeta m USING (pid)
      WHERE g.grp = 'M' AND {minute_of_day("r.t", "r.day")} BETWEEN 420 AND 1080
      GROUP BY ALL""").fetchall()
    total = {d: sum(b for dd, _, _, b in dark if dd == d) for d in DAYS}
    f["unseen_share"] = {d: pct(sum(a for dd, _, a, _ in dark if dd == d), total[d]) for d in DAYS}
    # of all the bus time, the unseen time while on one of CTA's reroutes, and on a regular pattern
    f["unseen_share_reroute"] = {
        d: pct(sum(a for dd, det, a, _ in dark if dd == d and det), total[d]) for d in DAYS
    }
    f["unseen_share_regular"] = {
        d: pct(sum(a for dd, det, a, _ in dark if dd == d and not det), total[d]) for d in DAYS
    }
    f["load_data_reports"] = con.execute(
        "SELECT count(*) FILTER (WHERE psgld IS NOT NULL AND psgld != 'N/A'), count(*) FROM pos"
    ).fetchone()
    f["schedule"] = timetable(con, work)
    rides = con.execute(f"""
      SELECT sum(avg_sunday_rides) FILTER (WHERE route IN (SELECT rt FROM rg WHERE grp = 'M')),
             sum(avg_sunday_rides),
             min(first_date), max(last_date), max(n_days)
      FROM read_csv('{RIDERSHIP}', types = {{'route': 'VARCHAR'}})""").fetchone()
    f["ridership"] = {
        "rerouted": round(rides[0], -3),
        "share_pct": round(100 * rides[0] / rides[1]),
        "from": str(rides[2]),
        "to": str(rides[3]),
        "sundays": rides[4],
    }
    f["crossings"] = crossing_facts(con)
    f["trips_run"] = dict(
        con.execute("""
      SELECT day, count(*) FROM (SELECT day, rt, stsd, stst FROM pos JOIN rg g USING (rt)
                                 WHERE g.grp = 'M' AND stst BETWEEN 6.5 * 3600 AND 18 * 3600
                                 GROUP BY ALL HAVING count(*) >= 4)
      GROUP BY day""").fetchall()
    )
    return f


def crossing_series(con: duckdb.DuckDBPyConnection) -> dict:
    """Buses crossing the course, per 15 minutes 04:00-24:00: at street level on Oct 11, the two
    normal Sundays as a band, and on Oct 11 under the course (expressway underpasses, on CTA's
    reroutes)."""
    step, start, end = 15, 4 * 60, 24 * 60
    rows = {
        (d, k, b): n
        for d, k, b, n in con.execute(f"""
      SELECT day, kind, floor({minute_of_day("t")} / {step})::INT, count(*) FROM xing
      GROUP BY ALL""").fetchall()
    }
    bins = range(start // step, end // step)
    a = [rows.get((NORMAL[0], "cross", b), 0) for b in bins]
    b_ = [rows.get((NORMAL[1], "cross", b), 0) for b in bins]
    return {
        "start": start,
        "step": step,
        "cross": [rows.get((MARATHON, "cross", b), 0) for b in bins],
        "cross_band": [[min(x, y), max(x, y)] for x, y in zip(a, b_, strict=True)],
        "under": [rows.get((MARATHON, "under", b), 0) for b in bins],
    }


def crossing_facts(con: duckdb.DuckDBPyConnection) -> dict:
    """The wall in numbers: crossings during the race, the last before and first after it, the
    underpasses, and when buses crossed each course street again against the city's planned
    reopening."""
    f: dict = {}

    def m(col):
        return minute_of_day(col)

    f["street_7_to_1130"] = dict(
        con.execute(
            "SELECT day, count(*) FROM xing WHERE kind = 'cross' "
            f"AND {m('t')} BETWEEN 420 AND 690 GROUP BY day"
        ).fetchall()
    )
    f["under"] = dict(
        con.execute(
            f"""SELECT CASE {
                " ".join(
                    f"WHEN mile BETWEEN {u[1]} AND {u[2]} THEN '{u[0]}'"
                    for u in crossings.UNDERPASSES
                )
            } END, count(*)
                FROM xing WHERE day = '{MARATHON}' AND kind = 'under' GROUP BY 1"""
        ).fetchall()
    )
    f["under_normal"] = con.execute(
        f"SELECT count(*) FROM xing WHERE day != '{MARATHON}' AND kind = 'under'"
    ).fetchone()[0]
    # the street-level crossings of Oct 11 from 6:00 to 14:00, in order (the wall's edges and
    # anything through it)
    f["street_6_to_14"] = [
        {"at": at(t), "rt": rt, "mile": round(mile, 1)}
        for t, rt, mile in con.execute(f"""
      SELECT {m("t")}, rt, mile FROM xing
      WHERE day = '{MARATHON}' AND kind = 'cross' AND {m("t")} BETWEEN 360 AND 840
      ORDER BY t""").fetchall()
    ]
    # the last bus across the course in the Loop before the wall (Dearborn/Jackson/LaSalle, Wacker)
    t, rt = con.execute(
        f"""SELECT max({m("t")}), arg_max(rt, t) FROM xing
            WHERE day = '{MARATHON}' AND kind = 'cross'
              AND (mile BETWEEN 1.22 AND 2.29 OR mile BETWEEN 12.80 AND 13.42)
              AND {m("t")} < 420"""
    ).fetchone()
    f["loop_last"] = {"at": at(t), "rt": rt}
    # per course street where buses normally cross often (>= 3 an hour 10:00-18:00 on both Sundays):
    # its closure = the longest gap between street-level crossings on Oct 11 from 05:00 to 22:00, so
    # a bus let through mid-race doesn't end it; reopened = the crossing that ends it, against the
    # planned time
    lags = []
    for m0, m1, planned, street in REOPEN:
        if street == "Stockton Dr to Inner Lake Shore Dr":  # outer LSD (open) within GPS error
            continue
        normal = con.execute(
            f"""SELECT count(*) FILTER (WHERE day = '{NORMAL[0]}'),
                       count(*) FILTER (WHERE day = '{NORMAL[1]}')
                FROM xing WHERE kind = 'cross' AND mile >= {m0} AND mile < {m1}
                  AND {m("t")} BETWEEN 600 AND 1080"""
        ).fetchone()
        if min(normal) < 3 * 8:
            continue
        times = [
            t
            for (t,) in con.execute(
                f"""SELECT {m("t")} FROM xing WHERE day = '{MARATHON}' AND kind = 'cross'
                      AND mile >= {m0} AND mile < {m1} AND {m("t")} BETWEEN 300 AND 1320
                    ORDER BY t"""
            ).fetchall()
        ]
        if not times:
            continue
        times = [
            300
        ] + times  # 05:00 opens the window: some streets were closed before it (Belmont, Oct 11)
        closed, reopened = max(zip(times, times[1:], strict=False), key=lambda g: g[1] - g[0])
        lags.append((round(reopened - planned), street, at(planned), at(reopened)))
    lags.sort()
    f["reopen_lag_streets"] = len(lags)
    mid = [lags[(len(lags) - 1) // 2][0], lags[len(lags) // 2][0]] if lags else None
    f["reopen_lag_median_min"] = sum(mid) / 2 if mid else None  # the median of an even count
    f["reopen_lag_range_min"] = [lags[0][0], lags[-1][0]] if lags else None
    f["reopen_lags"] = [
        {"street": st, "planned": p, "first_bus": q, "lag": lag} for lag, st, p, q in lags
    ]
    # Dearborn St (miles 1.22-2.14): the gap between the last crossing before 7:00 and the first
    # after, and how many buses crossed there in those same hours on each normal Sunday
    last, first = con.execute(
        f"""SELECT max({m("t")}) FILTER (WHERE {m("t")} < 420),
                   min({m("t")}) FILTER (WHERE {m("t")} > 420)
            FROM xing WHERE day = '{MARATHON}' AND kind = 'cross'
              AND mile BETWEEN 1.22 AND 2.14"""
    ).fetchone()
    normal = [
        n
        for (n,) in con.execute(
            f"""SELECT count(*) FROM xing
                WHERE day IN ('{NORMAL[0]}', '{NORMAL[1]}') AND kind = 'cross'
                  AND mile BETWEEN 1.22 AND 2.14 AND {m("t")} > {last} AND {m("t")} < {first}
                GROUP BY day ORDER BY day"""
        ).fetchall()
    ]
    f["dearborn"] = {"last": at(last), "next": at(first), "normal_crossings": normal}
    return f


def timetable(con: duckdb.DuckDBPyConnection, work: Path) -> dict:
    """The bus trips CTA's timetable (GTFS) scheduled each day, and whether they were the same
    trips."""
    ids = {}
    for day in DAYS:
        d, dow = (
            day.replace("-", ""),
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][
                con.execute(f"SELECT isodow(DATE '{day}') - 1").fetchone()[0]
            ],
        )
        ids[day] = {
            t
            for (t,) in con.execute(f"""
          WITH svc AS (
            SELECT service_id FROM '{work}/gtfs_calendar.parquet'
              WHERE {dow} = '1' AND start_date <= '{d}' AND end_date >= '{d}'
            UNION SELECT service_id FROM '{work}/gtfs_calendar_dates.parquet'
              WHERE date = '{d}' AND exception_type = '1'
            EXCEPT SELECT service_id FROM '{work}/gtfs_calendar_dates.parquet'
              WHERE date = '{d}' AND exception_type = '2')
          SELECT t.trip_id FROM '{work}/gtfs_trips.parquet' t
          JOIN '{work}/gtfs_routes.parquet' r USING (route_id, feed_id)
          WHERE r.route_type = '3' AND t.service_id IN (SELECT service_id FROM svc)""").fetchall()
        }
    return {
        "bus_trips": {d: len(v) for d, v in ids.items()},
        "same_trips": len({frozenset(v) for v in ids.values()}) == 1,
    }


def closed_course(con: duckdb.DuckDBPyConnection) -> None:
    """Drops the Marathon's passes at stops on the course while their street was closed (6:00 AM
    to the city's planned reopening there): GPS can't tell a closed street from the road beside it
    (#151's southbound buses on Outer Lake Shore Drive, within 60 m of its stops on Inner Lake Shore
    Drive, were credited with them all morning, Oct 12 check). On the course: within 15 m of the
    course line, the bus moving along it (within 30 degrees, either way)."""
    course = course_json()
    line, at = course["line"], course["at"]
    table(
        con,
        "course_seg",
        [
            (
                a[0] * KX,
                a[1] * KY,
                b[0] * KX,
                b[1] * KY,
                m0,
                m1,
                min(a[1], b[1]),
                max(a[1], b[1]),
                min(a[0], b[0]),
                max(a[0], b[0]),
            )
            for a, b, m0, m1 in zip(line, line[1:], at, at[1:], strict=False)
        ],
        "xa DOUBLE, ya DOUBLE, xb DOUBLE, yb DOUBLE, m0 DOUBLE, m1 DOUBLE, lat0 DOUBLE, "
        "lat1 DOUBLE, lon0 DOUBLE, lon1 DOUBLE",
    )
    # the finish stretch on Columbus Dr reopens with Roosevelt Rd at 6 PM (official plan); it has
    # no busy cross street, so REOPEN (the reopening comparison) leaves it out
    stretches = [(a, b, t) for a, b, t, _ in REOPEN] + [(REOPEN[-1][1], MARATHON_MI, 18 * 60)]
    table(con, "reopen", stretches, "m0 DOUBLE, m1 DOUBLE, t INT")
    con.execute(f"""CREATE OR REPLACE TABLE course_stops AS
      WITH st AS (SELECT DISTINCT stpid, lat * {KY} AS py, lon * {KX} AS px, lat, lon FROM passes
                  WHERE day = '{MARATHON}'),
      j AS (SELECT st.stpid, st.px, st.py, c.*,
                   greatest(0, least(1, ((st.px - c.xa) * (c.xb - c.xa)
                                         + (st.py - c.ya) * (c.yb - c.ya))
                     / greatest(pow(c.xb - c.xa, 2) + pow(c.yb - c.ya, 2), 1e-9))) AS u
            FROM st JOIN course_seg c ON st.lat BETWEEN c.lat0 - 0.0005 AND c.lat1 + 0.0005
                                     AND st.lon BETWEEN c.lon0 - 0.0007 AND c.lon1 + 0.0007),
      d AS (SELECT stpid, m0 + u * (m1 - m0) AS mile, degrees(atan2(xb - xa, yb - ya)) AS brg,
                   sqrt(pow(px - xa - u * (xb - xa), 2) + pow(py - ya - u * (yb - ya), 2)) AS dist
            FROM j)
      SELECT stpid, arg_min(brg, dist) AS brg, arg_min(mile, dist) AS mile FROM d
      GROUP BY stpid HAVING min(dist) <= 15""")
    # after the planned reopening, while the route's detour flag stayed on (+15 min), a pass
    # more than 15 m off the stop is still the road beside it: #146 and #151 kept to Outer Lake
    # Shore Drive, 26 m from the southbound Inner Drive stops, until about 5 PM (Oct 12 check)
    con.execute(f"""CREATE OR REPLACE TABLE course_closed AS
      WITH flag AS (SELECT rt, epoch(max(fetched_at) FILTER (WHERE st = 1)) + 900 AS off
                    FROM detours WHERE "desc" ILIKE '%BOA%Marathon%' GROUP BY rt),
      rts AS (SELECT DISTINCT stpid, rt FROM passes WHERE stpid IN (SELECT stpid FROM course_stops))
      SELECT '{MARATHON}' AS day, c.stpid, c.brg, x.rt,
             epoch(TIMESTAMPTZ '{MARATHON} 00:00:00-05:00') + 360 * 60 AS t0,
             epoch(TIMESTAMPTZ '{MARATHON} 00:00:00-05:00') + r.t * 60 AS reopen,
             -- (by 6 PM every street had reopened by the plan; #146's flag stayed on all night)
             greatest(epoch(TIMESTAMPTZ '{MARATHON} 00:00:00-05:00') + r.t * 60,
                      least(coalesce(f.off, 0),
                            epoch(TIMESTAMPTZ '{MARATHON} 18:00:00-05:00'))) AS t1
      FROM course_stops c JOIN reopen r ON c.mile BETWEEN r.m0 AND r.m1 + 0.01
      JOIN rts x USING (stpid) LEFT JOIN flag f USING (rt)""")
    # where GPS put a bus at a stop on its street while closed, the road beside it is within GPS's
    # reach (Outer Lake Shore Drive within 12 m of Inner Drive's Hawthorne and Roscoe): while its
    # route stayed detoured no pass there counts, however near
    con.execute("""CREATE OR REPLACE TABLE course_ambiguous AS
      SELECT DISTINCT p.stpid FROM passes p JOIN course_closed c
        ON p.day = c.day AND p.stpid = c.stpid AND p.rt = c.rt
      WHERE p.tp >= c.t0 AND p.tp < c.reopen
        AND least(abs(((p.brg - c.brg + 540) % 360) - 180),
                  180 - abs(((p.brg - c.brg + 540) % 360) - 180)) <= 30""")
    dropped = con.execute("""DELETE FROM passes p USING course_closed c
      WHERE p.day = c.day AND p.stpid = c.stpid AND p.rt = c.rt AND p.tp >= c.t0
        AND (p.tp < c.reopen
             OR (p.tp < c.t1
                 AND (p.chord_m > 15 OR p.stpid IN (SELECT stpid FROM course_ambiguous))))
        AND least(abs(((p.brg - c.brg + 540) % 360) - 180),
                  180 - abs(((p.brg - c.brg + 540) % 360) - 180)) <= 30""").fetchone()[0]
    say(
        "course stops:",
        con.execute("SELECT count(*) FROM course_stops").fetchone()[0],
        "passes there while closed, dropped:",
        dropped,
    )


def hidden_on_reroute(con: duckdb.DuckDBPyConnection) -> None:
    """Drops the Marathon's passes that span more than 2 minutes between reports at stops CTA's
    reroute of that route skips, from 6 AM, unless a pass of the same route and direction seen
    all the way (reports at most 2 minutes apart) lies within 15 minutes of it: Bus Tracker stops
    reporting a bus off its pattern, so a bus labelled with its regular route vanished while it
    drove the reroute and its next report, back on the street, spanned the stops it skipped (#12
    at Roosevelt & Racine: 8 such passes 6 AM-6 PM, 9-14 min each; real passes 5:33 AM, 6:13 PM;
    #21 on Cermak and #36 on Broadway after their detour flags went off, Oct 12 checks).
    Skipped: in a reroute's regular path (dtrpt) and not in the path it runs (pt)."""
    dropped = con.execute(f"""DELETE FROM passes p USING (
        WITH dp AS (SELECT DISTINCT p.pid, m.rt, m.rtdir FROM pos p JOIN pmeta m USING (pid)
                    WHERE p.day = '{MARATHON}' AND m.is_detour),
        reg AS (SELECT dp.rt, dp.rtdir, dp.pid, s.stpid
                FROM dp JOIN ppoints s ON s.pid = dp.pid AND s.kind = 'dtrpt' AND s.typ = 'S'),
        run AS (SELECT dp.pid, s.stpid
                FROM dp JOIN ppoints s ON s.pid = dp.pid AND s.kind = 'pt' AND s.typ = 'S')
        SELECT DISTINCT rt, rtdir, stpid FROM reg
        WHERE (pid, stpid) NOT IN (SELECT pid, stpid FROM run)) k
      WHERE p.day = '{MARATHON}' AND p.rt = k.rt AND p.rtdir = k.rtdir AND p.stpid = k.stpid
        AND p.dt > 120 AND {minute_of_day("p.tp", "p.day")} >= 360
        AND NOT EXISTS (SELECT 1 FROM passes q
                        WHERE q.day = p.day AND q.rt = p.rt AND q.rtdir = p.rtdir
                          AND q.stpid = p.stpid AND q.dt <= 120
                          AND abs(q.tp - p.tp) <= 900)""").fetchone()[0]
    say("passes across a reroute's skipped stops after a gap, dropped:", dropped)


def build(work: Path) -> dict:
    """Every file the page reads, by name."""
    con = connect(str(work / "base.duckdb"))
    con.execute("SET memory_limit = '12GB'; SET preserve_insertion_order = false")
    groups(con)
    closed_course(con)
    hidden_on_reroute(con)
    con.execute(
        f"CREATE OR REPLACE TABLE gtfs_routes AS SELECT * FROM '{work}/gtfs_routes.parquet'"
    )
    cutoff(con)
    trip_states(con)
    main_reroutes(con)
    course = course_json()
    dark.run(con, MARATHON, NORMAL)
    prune_cut(con)
    xs, ys = [p[0] for p in course["line"]], [p[1] for p in course["line"]]
    box = [
        [round(min(xs) - 0.03, 4), round(min(ys) - 0.02, 4)],
        [round(max(xs) + 0.03, 4), round(max(ys) + 0.02, 4)],
    ]
    crossings.build(con, course["line"], course["at"], DAYS)
    files = {
        "course.json": course | {"attribution": OSM_CREDIT},  # derived from OpenStreetMap (ODbL)
        "crossings.json": crossing_series(con),
        "stops.json": dark.frames(con, MARATHON, NORMAL, box)
        | {"field": [field(dark.F0 // 60 + i * dark.STEP // 60) for i in range(dark.NF)]},
        "series.json": series(con),
        "timeline.json": timeline(con),
        "reroutes.json": reroutes(con),
    }
    # the day's moments, marked on the charts: the city closing the course streets (6:00, its
    # published plan), CTA lifting its first detour, and its alerts ending
    tl = files["timeline.json"]
    lifted = min(r["flag"][1] for r in tl["routes"] if r["flag"][1])
    events = [
        {"at": 360, "label": "6 AM: course closes"},
        {"at": lifted, "label": f"{at(lifted)}: first detour lifted"},
        {
            "at": tl["alerts"][1],
            "label": f"{at(tl['alerts'][1])}: CTA's race-day alerts end".replace(":00 ", " "),
        },
    ]
    for name in ("series.json", "crossings.json"):
        files[name]["events"] = events
    files["facts.json"] = facts(con, files, work)
    return files


def check(files: dict) -> list[str]:
    """The page's contract (site/src/lib/marathon.ts): what's wrong, empty when nothing is."""
    bad = []
    c = files["course.json"]
    if len(c["line"]) != len(c["at"]) or abs(c["at"][-1] - MARATHON_MI) > 0.01:
        bad.append("course: a distance per point, ending at the marathon's length")
    st = files["stops.json"]
    if not st["stops"] or any(
        len(x[3]) != st["frames"] or set(x[3]) - set(".sdpu") for x in st["stops"]
    ):
        bad.append("stops: one state per frame, each '.', 's', 'd', 'p' or 'u'")
    race = [
        i for i in range(st["frames"]) if 7 * 60 + 30 <= st["start"] + i * st["step"] <= 15 * 60
    ]

    def median(v):
        return sorted(v[i] for i in race)[len(race) // 2]

    if (
        len(st["dark"]) != st["frames"]
        or len(st["noise"]) != st["frames"]
        or median(st["dark"]) <= 5 * median(st["noise"])
    ):
        bad.append(
            "stops: a dark count and noise per frame, the Marathon's far above the noise "
            "during the race"
        )
    se = files["series.json"]
    for grp in ("M", "O"):
        for k in ("buses", "stops"):
            vals = [v for v in se[grp][k] if v is not None]
            if (
                len(se[grp][k]) != len(se[grp][f"{k}_band"])
                or not vals
                or not all(0 <= v <= 250 for v in vals)
            ):
                bad.append(f"series {grp}.{k}: percentages of a normal Sunday, a band for each")
    tl = files["timeline.json"]
    if len(tl["routes"]) != 34 or None in tl["alerts"]:
        bad.append("timeline: the 34 Marathon routes and CTA's alert window")
    rr = files["reroutes.json"]
    if len(rr["routes"]) < 60 or not rr["lines"] or not rr["stops"] or not rr["terminals"]:
        bad.append(
            "reroutes: each route-direction's reroute, its lines, cut-off stops and waiting places"
        )
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--work", type=Path, required=True, help="cache for the extract and the base DuckDB file"
    )
    ap.add_argument("--out", type=Path, required=True, help="where the site's files are written")
    ap.add_argument("--fresh", action="store_true", help="extract again even if the cache exists")
    ap.add_argument(
        "--facts",
        type=Path,
        default=Path("site/src/data/marathon-2026.json"),
        help="the numbers the page's text states",
    )
    ap.add_argument(
        "--publish", action="store_true", help="upload --out to the site bucket after checking it"
    )
    args = ap.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    snapshots = extract(args.work, args.fresh)
    if args.fresh or not (args.work / "base.duckdb").exists():
        base(args.work)
    files = build(args.work)
    problems = check(files)
    if problems:
        sys.exit("not written: " + "; ".join(problems))
    args.out.mkdir(parents=True, exist_ok=True)
    for name, doc in files.items():
        (args.out / name).write_text(json.dumps(doc, separators=(",", ":"), sort_keys=True))
    (args.out / "manifest.json").write_text(
        json.dumps(
            {
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "days": {"marathon": MARATHON, "normal": NORMAL},
                "ingress_snapshots": snapshots,
                "code": "transform/reports/marathon_2026.py",
            },
            indent=1,
        )
    )
    args.facts.write_text(json.dumps(files["facts.json"], indent=1, sort_keys=True) + "\n")
    say(f"wrote {len(files) + 1} files to {args.out}, facts to {args.facts}")
    if args.publish:
        publish(args.out)


if __name__ == "__main__":
    sys.exit(main())
