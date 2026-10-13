"""The Marathon retro's timelapse: when each stop was left without a bus (marathon_2026.py's
stops.json).

A unit (a physical stop, any route; or a route-direction at a stop) on a test day is DARK from its
last bus to its next one when that gap holds a moment with no bus for W = 90 min, where every
reference day (the normal Sundays) had at least K = 3 in the same 90 min, and where every reference
day had a bus in the hour before (the stop is normally in service then). So a stop is dark from its
last bus, not 90 minutes later; never in a frame a bus came in. Frames every 10 minutes,
05:00-23:50. Calibrated (Oct 12) so that normal Sundays judged against each other stay nearly
empty: median 20-45 dark stops a frame.

Visits are GPS-checked stop passes plus departures from a trip's first stop (never a pass). To never
call a stop dark when a bus may have served it, two kinds of possible service count as service:
  drive-by  a bus of a route that normally serves the stop, whatever pattern CTA labelled it with,
            passing within 12 m of it in its direction (two reports <= 75 s and >= 30 m apart);
  twin      a visit at another stop id within 60 m going the same way (a reroute's temporary stop
            at the same corner; some corners have two ids), by a route that normally serves it.
Stops of special shuttles CTA announced but Bus Tracker never showed are "can't tell", never dark.

Every table goes into the connection given (the retro's base DuckDB file): pos, ping, pairs, passes,
ppoints and pmeta from marathon_2026.py's base stage.
"""

import duckdb

F0, NF, STEP = 5 * 3600, 114, 600  # frames: seconds after midnight of the first, how many, apart
W_MIN, K = 90, 3
DRIVEBY_M = (
    12  # a drive-by: the stop this near a bus's line (25 caught Outer LSD buses 17-25 m away)
)

# CTA's alerts 117747 (#22 Clark, Irving Park - Belmont), 117779 (#66 Navy Pier from Chicago/Clark)
# and 117785 (#146 Museum Campus): no bus reported within 30 m of these stops 08:00-14:00 on Oct 11
SHUTTLES = {
    "#22 Clark shuttle": (
        "stpnm ILIKE 'Clark &%' AND list_contains(string_split(routes_normal, ','), '22')"
        " AND lat BETWEEN 41.9390 AND 41.9550"
    ),
    "#66 Navy Pier shuttle": (
        "((stpnm ILIKE 'Chicago &%' AND lon > -87.6312"
        " AND list_contains(string_split(routes_normal, ','), '66'))"
        " OR (lat BETWEEN 41.8890 AND 41.8975 AND lon > -87.6235))"
    ),
    "#146 Museum Campus shuttle": (
        "(stpnm ILIKE '%Solidarity%' OR stpnm ILIKE '%Soldier Field%'"
        " OR stpnm ILIKE '%Field Museum%'"
        " OR stpnm ILIKE '%Planetarium%' OR stpnm ILIKE '%Aquarium%')"
    ),
}


def midnight(day: str) -> str:
    return f"epoch(TIMESTAMPTZ '{day} 00:00:00 America/Chicago')"


def visits(con: duckdb.DuckDBPyConnection, days: list[str]) -> None:
    """visits (one per bus, trip and stop; a pass wins over a departure), stop_dim (each stop's
    name, place and the routes serving it on normal days and on the test day)."""
    con.execute("CREATE OR REPLACE TABLE day0 (day VARCHAR, t0 BIGINT)")
    for d in days:
        con.execute(f"INSERT INTO day0 SELECT '{d}', {midnight(d)}")
    con.execute("""CREATE OR REPLACE TABLE first_stop AS
      SELECT p.pid, p.stpid, p.stpnm, p.pdist, p.lat, p.lon
      FROM ppoints p JOIN (SELECT pid, min(seq) AS seq FROM ppoints
                           WHERE kind = 'pt' AND typ = 'S' GROUP BY pid) f USING (pid, seq)
      WHERE p.kind = 'pt'""")
    # a trip's first forward on-route pair whose earlier report is <= 1,000 ft past the first
    # stop = departure
    con.execute("""CREATE OR REPLACE TABLE departures AS
      WITH fp AS (SELECT day, vid, rt, rtdir, pid, stsd, stst, min(t0) AS t0, arg_min(d0, t0) AS d0,
                         arg_min(t, t0) AS t, arg_min(pdist, t0) AS pdist
                  FROM pairs GROUP BY ALL)
      SELECT fp.day, fp.vid, fp.rt, fp.rtdir, fp.pid, fs.stpid, fs.stpnm, fs.lat, fs.lon,
             CASE WHEN fp.d0 < fs.pdist
                  THEN fp.t0 + (fs.pdist - fp.d0) / (fp.pdist - fp.d0) * (fp.t - fp.t0)
                  ELSE fp.t0 END AS tp,
             fp.stst, fp.stsd
      FROM fp JOIN first_stop fs USING (pid)
      WHERE fp.d0 <= fs.pdist + 1000 AND fp.pdist >= fs.pdist""")
    con.execute("""CREATE OR REPLACE TABLE visits AS
      WITH u AS (
        SELECT day, vid, rt, rtdir, pid, stpid, any_value(stpnm) AS stpnm, stst, stsd,
               'pass' AS kind, min(tp) AS tp
        FROM passes GROUP BY day, vid, rt, rtdir, pid, stpid, stst, stsd
        UNION ALL
        SELECT day, vid, rt, rtdir, pid, stpid, stpnm, stst, stsd, 'dep' AS kind, tp
        FROM departures)
      SELECT u.*, tp - d.t0 AS sec FROM u JOIN day0 d USING (day)
      QUALIFY row_number() OVER (PARTITION BY day, vid, pid, stst, stsd, stpid
                                 ORDER BY kind DESC, tp) = 1""")


def stop_dim(con: duckdb.DuckDBPyConnection, normal: list[str], test: str) -> None:
    con.execute(f"""CREATE OR REPLACE TABLE stop_dim AS
      WITH pts AS (SELECT stpid, mode(stpnm) AS stpnm, avg(lat) AS lat, avg(lon) AS lon FROM ppoints
                   WHERE typ = 'S' AND stpid IS NOT NULL GROUP BY stpid),
      r AS (SELECT stpid,
                   string_agg(DISTINCT rt, ',' ORDER BY rt)
                     FILTER (WHERE day IN ('{normal[0]}', '{normal[1]}')) AS routes_normal,
                   string_agg(DISTINCT rt, ',' ORDER BY rt)
                     FILTER (WHERE day = '{test}') AS routes_test
            FROM visits GROUP BY stpid)
      SELECT pts.*, r.routes_normal, r.routes_test FROM pts LEFT JOIN r USING (stpid)""")


def counts(
    con: duckdb.DuckDBPyConnection, unit: list[str], name: str, days: dict[str, str], src: str
) -> None:
    """Table name(<unit>, f, T, and per day letter L: L60 (visits in (T-60 min, T]), Lw (in
    (T-W, T]), Lp (its last visit at or before T, -1: none)). Every unit with a visit on any day
    gets every frame."""
    u = ", ".join(unit)
    dl = ", ".join(f"'{d}'" for d in days)

    def on(a, b):
        return " AND ".join(f"{a}.{c} = {b}.{c}" for c in unit)

    con.execute(f"""CREATE OR REPLACE TABLE {name} AS
      WITH v AS (SELECT day, {u}, sec FROM {src} WHERE day IN ({dl})),
      units AS (SELECT DISTINCT {u} FROM v),
      fr AS (SELECT units.*, f, {F0} + {
        STEP
    } * f AS T FROM units CROSS JOIN (SELECT unnest(range(0, {NF})) AS f)),
      ex AS (SELECT day, {u}, ceil((sec - {F0}) / {STEP})::INT AS f0, sec FROM v),
      c AS (SELECT day, {u}, f, count(*) FILTER (WHERE f < f0 + 6) AS n60, count(*) AS nw
            FROM (SELECT day, {u}, f0, unnest(range(f0, f0 + {
        W_MIN // 10
    })) AS f FROM ex) WHERE f BETWEEN 0 AND {NF - 1} GROUP BY ALL),
      dd AS (SELECT unnest([{dl}]) AS day),
      frd AS (SELECT dd.day, fr.* FROM dd CROSS JOIN fr),
      p AS (SELECT frd.*, v.sec AS prev FROM frd ASOF LEFT JOIN v ON frd.day = v.day AND {
        on("frd", "v")
    } AND frd.T >= v.sec)
      SELECT {", ".join(f"p.{c}" for c in unit)}, p.f, any_value(p.T) AS T,
        {
        ", ".join(
            f"coalesce(max(c.n60) FILTER (WHERE p.day = '{d}'), 0)::INT AS {x}60, "
            f"coalesce(max(c.nw) FILTER (WHERE p.day = '{d}'), 0)::INT AS {x}w, "
            f"coalesce(max(p.prev) FILTER (WHERE p.day = '{d}'), -1)::DOUBLE AS {x}p"
            for d, x in days.items()
        )
    }
      FROM p LEFT JOIN c ON c.day = p.day AND {on("c", "p")} AND c.f = p.f
      GROUP BY ALL""")


def dark(name: str, unit: list[str], x: str, refs: list[str]) -> str:
    """unit, f, T, gap (the test day's last visit before T, -1: none), confirm, dark."""
    u = ", ".join(unit)
    confirm = f"{x}w = 0 AND " + " AND ".join(f"{r}w >= {K}" for r in refs)
    in_service = " AND ".join(f"{r}60 >= 1" for r in refs)
    # never dark in a frame a bus came in: a lone bus between two long gaps shows (Oct 12 referee:
    # 49 of 752 red runs went straight through one)
    return f"""SELECT {u}, f, T, {x}p AS gap, ({confirm}) AS confirm,
                 bool_or({confirm}) OVER (PARTITION BY {u}, {x}p) AND {in_service}
                   AND NOT {x}p > T - {STEP} AS dark
               FROM {name}"""


# config: (count table, unit, test day letter, reference letters)
PHYS, RDIR = ["stpid"], ["rt", "rtdir", "stpid"]
CFG = {
    "m": ("cs", PHYS, "m", ["s", "o"]),
    "r": ("cr", RDIR, "m", ["s", "o"]),
    "s_o": ("cs", PHYS, "s", ["o"]),
    "o_s": ("cs", PHYS, "o", ["s"]),
}


def rules(con: duckdb.DuckDBPyConnection, letters: dict[str, str], src: str, sfx: str) -> None:
    counts(con, PHYS, f"cs{sfx}", letters, src)
    counts(con, RDIR, f"cr{sfx}", letters, src)
    for name, (table, unit, x, refs) in CFG.items():
        con.execute(f"CREATE OR REPLACE TABLE d_{name}{sfx} AS {dark(table + sfx, unit, x, refs)}")
        u = ", ".join(unit)

        def on(a, b, unit=unit):
            return " AND ".join(f"{a}.{c} = {b}.{c}" for c in unit)

        day = {v: k for k, v in letters.items()}[x]
        con.execute(f"""CREATE OR REPLACE TABLE outages_{name}{sfx} AS
          WITH o AS (SELECT {u}, gap AS gap_start, min(T) AS dark_from, max(T) AS dark_to
                     FROM d_{name}{sfx} WHERE dark GROUP BY ALL),
          v AS (SELECT {u}, sec FROM {src} WHERE day = '{day}')
          SELECT o.*, v.sec AS gap_end
          FROM o ASOF LEFT JOIN v ON {on("o", "v")} AND v.sec > o.gap_start""")


def possible_service(con: duckdb.DuckDBPyConnection, letters: dict[str, str]) -> None:
    """visits_aug: visits plus drive-bys and twins inside every dark gap (module docstring)."""
    # direction of travel at each stop: from the nearest pattern point >= 40 m before to the nearest
    # >= 40 m after (CTA puts a waypoint on every stop, so its neighbours give no direction)
    con.execute("""CREATE OR REPLACE TABLE stop_bearing AS
      WITH pts AS (SELECT pid, seq, typ, stpid, lat, lon FROM ppoints WHERE kind = 'pt'),
      st AS (SELECT * FROM pts WHERE typ = 'S'),
      nb AS (SELECT st.pid, st.seq, st.stpid, st.lat, st.lon,
                    arg_max(p.lat, p.seq) FILTER (WHERE p.seq < st.seq) AS lat0,
                    arg_max(p.lon, p.seq) FILTER (WHERE p.seq < st.seq) AS lon0,
                    arg_min(p.lat, p.seq) FILTER (WHERE p.seq > st.seq) AS lat1,
                    arg_min(p.lon, p.seq) FILTER (WHERE p.seq > st.seq) AS lon1
             FROM st JOIN pts p ON p.pid = st.pid AND p.seq BETWEEN st.seq - 15 AND st.seq + 15
             WHERE sqrt(pow((p.lat - st.lat) * 110540, 2) + pow((p.lon - st.lon) * 82990, 2)) >= 40
             GROUP BY ALL)
      SELECT DISTINCT stpid,
             round(degrees(atan2((coalesce(lon1, lon) - coalesce(lon0, lon)) * 82990,
                                 (coalesce(lat1, lat) - coalesce(lat0, lat)) * 110540)))::INT
             AS brg
      FROM nb WHERE lat0 IS NOT NULL OR lat1 IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TABLE stop_rtdir AS
      SELECT DISTINCT m.rt, m.rtdir, p.stpid FROM ppoints p JOIN pmeta m USING (pid)
      WHERE p.kind = 'pt' AND p.typ = 'S' AND NOT m.is_detour""")
    windows = " UNION ALL ".join(
        f"""SELECT '{[d for d, k in letters.items() if k == x][0]}' AS day, stpid,
                   greatest(gap_start, dark_from - 600) AS w0,
                   coalesce(gap_end, 100800) AS w1 FROM outages_{name}"""
        for name, (_, _, x, _) in CFG.items()
    )
    con.execute(f"""CREATE OR REPLACE TABLE aug_windows AS
      SELECT w.day, w.stpid, w.w0, w.w1, sd.lat, sd.lon,
             string_split(sd.routes_normal, ',') AS routes
      FROM (SELECT DISTINCT * FROM ({windows})) w JOIN stop_dim sd USING (stpid)
      WHERE sd.routes_normal IS NOT NULL""")
    parts = []
    for day in letters:
        t = f"driveby_{day.replace('-', '')}"
        con.execute(f"""CREATE OR REPLACE TEMP TABLE {t} AS
          WITH oc AS (SELECT w.*, round(lat * 250)::INT + dy AS cy, round(lon * 250)::INT + dx AS cx
                      FROM aug_windows w, (SELECT unnest([-1, 0, 1]) dy),
                           (SELECT unnest([-1, 0, 1]) dx)
                      WHERE w.day = '{day}'),
          seg AS (SELECT vid, rt, t - {midnight(day)} AS sec, lat, lon,
                         lag(t) OVER w - {midnight(day)} AS sec0,
                         lag(lat) OVER w AS lat0, lag(lon) OVER w AS lon0
                  FROM ping WHERE day = '{day}' WINDOW w AS (PARTITION BY vid ORDER BY t)),
          s2 AS (SELECT *, round(lat * 250)::INT AS cy, round(lon * 250)::INT AS cx,
                        (lat - lat0) * 110540 AS dy_m, (lon - lon0) * 82990 AS dx_m
                 FROM seg WHERE sec0 IS NOT NULL AND sec - sec0 <= 75),
          j AS (SELECT oc.stpid, s2.vid, s2.rt, s2.sec0, s2.sec, s2.dx_m, s2.dy_m,
                       sqrt(s2.dx_m * s2.dx_m + s2.dy_m * s2.dy_m) AS len,
                       (oc.lat - s2.lat0) * 110540 AS py, (oc.lon - s2.lon0) * 82990 AS px
                FROM oc JOIN s2 USING (cy, cx)
                WHERE s2.sec0 >= oc.w0 AND s2.sec <= oc.w1 AND list_contains(oc.routes, s2.rt)),
          k AS (SELECT *, greatest(0, least(1, (px * dx_m + py * dy_m) / (len * len))) AS u,
                       degrees(atan2(dx_m, dy_m)) AS brg
                FROM j WHERE len >= 30)
          SELECT DISTINCT '{day}' AS day, k.stpid, k.rt, k.vid,
                 k.sec0 + k.u * (k.sec - k.sec0) AS sec, 'driveby' AS kind
          FROM k
          WHERE sqrt(pow(k.px - k.u * k.dx_m, 2) + pow(k.py - k.u * k.dy_m, 2)) <= {DRIVEBY_M}
            AND EXISTS (SELECT 1 FROM stop_bearing sb WHERE sb.stpid = k.stpid
                          AND abs(((k.brg - sb.brg + 540) % 360) - 180) <= 45)
            -- a stop on the closed course, its route still detoured: the road beside it
            -- (marathon_2026.py closed_course; Outer Lake Shore Drive is within 12 m of some)
            AND NOT EXISTS (SELECT 1 FROM course_closed cc
                            WHERE cc.day = '{day}' AND cc.stpid = k.stpid AND cc.rt = k.rt
                              AND {midnight(day)} + k.sec0 + k.u * (k.sec - k.sec0)
                                  BETWEEN cc.t0 AND cc.t1)""")
        parts.append(f"SELECT * FROM {t}")
    con.execute("""CREATE OR REPLACE TEMP TABLE twins AS
      WITH pairs AS (
        SELECT DISTINCT w.day, w.stpid, w.w0, w.w1, w.routes, s2.stpid AS twin
        FROM aug_windows w
        JOIN stop_dim s2 ON abs(s2.lat - w.lat) < 0.0006 AND abs(s2.lon - w.lon) < 0.0008
                        AND s2.stpid <> w.stpid
        WHERE sqrt(pow((s2.lat - w.lat) * 110540, 2) + pow((s2.lon - w.lon) * 82990, 2)) <= 60
          AND EXISTS (SELECT 1 FROM stop_bearing a
                      JOIN stop_bearing c ON abs(((a.brg - c.brg + 540) % 360) - 180) <= 45
                      WHERE a.stpid = w.stpid AND c.stpid = s2.stpid))
      SELECT DISTINCT p.day, p.stpid, v.rt, v.vid, v.sec, 'twin' AS kind
      FROM pairs p
      JOIN visits v ON v.day = p.day AND v.stpid = p.twin AND v.sec > p.w0 AND v.sec < p.w1
      WHERE list_contains(p.routes, v.rt)""")
    parts.append("SELECT * FROM twins")
    con.execute(f"""CREATE OR REPLACE TABLE visits_aug AS
      SELECT day, vid, rt, rtdir, pid, stpid, stpnm, stst, stsd, kind, tp, sec FROM visits
      UNION ALL
      SELECT e.day, e.vid, e.rt, coalesce(r.rtdir, '?'), NULL, e.stpid, NULL, NULL, NULL, e.kind,
             e.sec + d.t0, e.sec
      FROM ({" UNION ALL ".join(parts)}) e JOIN day0 d USING (day)
      LEFT JOIN stop_rtdir r ON r.rt = e.rt AND r.stpid = e.stpid""")


def run(con: duckdb.DuckDBPyConnection, marathon: str, normal: list[str]) -> None:
    """The final tables: d_<cfg>_x (with possible service) and shuttle_stops."""
    letters = {normal[0]: "s", normal[1]: "o", marathon: "m"}
    visits(con, list(letters))
    stop_dim(con, normal, marathon)
    rules(con, letters, "visits", "")
    possible_service(con, letters)
    rules(con, letters, "visits_aug", "_x")
    con.execute("CREATE OR REPLACE TABLE shuttle_stops (stpid VARCHAR, shuttle VARCHAR)")
    for name, cond in SHUTTLES.items():
        con.execute(
            f"INSERT INTO shuttle_stops SELECT DISTINCT stpid, '{name}' FROM stop_dim "
            f"WHERE routes_normal IS NOT NULL AND {cond}"
        )


LATER = 600  # a route out this much longer than its stop as a whole (back later, gone sooner)
PARKED_M = 8  # a bus reported this near a stop is at it, whatever its pattern or pdist say


def present(con: duckdb.DuckDBPyConnection, marathon: str) -> None:
    """usual_present: a bus of a stop's usual routes at it on the Marathon that its passes and
    drive-bys can miss (Oct 12 GPS check of the stop finder's claims, 25 of 1,493 wrong): parked
    within PARKED_M of it (a #70 laid over at Division & LaSalle 6:18-6:56 AM; #29s held on State
    at 11th; a #66 at Chicago & LaSalle from 2:09 PM, its pass at 2:20 PM as it left), or moving
    its way within DRIVEBY_M with reports up to 120 s apart (#73s at Armitage & Halsted)."""
    con.execute(f"""CREATE OR REPLACE TABLE usual_present AS
      WITH st AS (SELECT DISTINCT u.stpid, u.rt, sd.lat, sd.lon
                  FROM usual_rd u JOIN stop_dim sd USING (stpid)),
      sc AS (SELECT st.*, round(lat * 250)::INT + dy AS cy, round(lon * 250)::INT + dx AS cx
             FROM st, (SELECT unnest([-1, 0, 1]) dy), (SELECT unnest([-1, 0, 1]) dx)),
      seg AS (SELECT vid, rt, t - {midnight(marathon)} AS sec, lat, lon,
                     lag(t) OVER w - {midnight(marathon)} AS sec0,
                     lag(lat) OVER w AS lat0, lag(lon) OVER w AS lon0
              FROM ping WHERE day = '{marathon}' WINDOW w AS (PARTITION BY vid ORDER BY t)),
      s2 AS (SELECT *, (lat - lat0) * 110540 AS dy_m, (lon - lon0) * 82990 AS dx_m FROM seg
             WHERE sec0 IS NOT NULL AND sec - sec0 <= 120),
      ends AS (SELECT *, round(lat * 250)::INT AS cy, round(lon * 250)::INT AS cx FROM s2
               UNION
               SELECT *, round(lat0 * 250)::INT AS cy, round(lon0 * 250)::INT AS cx FROM s2),
      j AS (SELECT DISTINCT sc.stpid, e.rt, e.vid, e.sec0, e.sec, e.dx_m, e.dy_m,
                   sqrt(e.dx_m * e.dx_m + e.dy_m * e.dy_m) AS len,
                   (sc.lat - e.lat0) * 110540 AS py, (sc.lon - e.lon0) * 82990 AS px
            FROM sc JOIN ends e USING (cy, cx, rt)),
      k AS (SELECT *, CASE WHEN len > 0
                           THEN greatest(0, least(1, (px * dx_m + py * dy_m) / (len * len)))
                           ELSE 0 END AS u
            FROM j)
      SELECT DISTINCT stpid, rt, vid,
             CASE WHEN len >= 8 THEN sec0 + u * (sec - sec0) ELSE sec0 END AS sec
      FROM k
      WHERE ((len < 8 AND sqrt(px * px + py * py) <= {PARKED_M})
             OR (len >= 8 AND sqrt(pow(px - u * dx_m, 2) + pow(py - u * dy_m, 2)) <= {DRIVEBY_M}
                 AND EXISTS (SELECT 1 FROM stop_bearing sb WHERE sb.stpid = k.stpid
                               AND abs(((degrees(atan2(dx_m, dy_m)) - sb.brg + 540) % 360)
                                       - 180) <= 45)))
        AND NOT EXISTS (SELECT 1 FROM course_closed cc
                        WHERE cc.day = '{marathon}' AND cc.stpid = k.stpid AND cc.rt = k.rt
                          AND {midnight(marathon)} + k.sec0 BETWEEN cc.t0 AND cc.t1)""")


def stop_days(con: duckdb.DuckDBPyConnection, marathon: str, normal: list[str]) -> dict:
    """Per stop id, what the stop finder says of it (stops.json, see frames): its usual
    route-directions ("22N,36N": a pass or departure on both normal Sundays), the way they go
    there (compass letters, from their regular patterns: CTA's names don't always say it, #60
    "Westbound" runs south on Racine) and its Marathon. A stretch comes from its usual routes'
    own buses (passes, departures, drive-bys and twins: visits_aug; on the Marathon also any of
    them at the stop: present), judged by the dark rule above on those buses alone: another
    route's bus never ends one (Oct 12 check: 9 did) and a bus of its routes always does. Times
    are the buses' own, to the minute."""
    letters = {normal[0]: "s", normal[1]: "o", marathon: "m"}
    con.execute(f"""CREATE OR REPLACE TABLE usual_rd AS
      SELECT stpid, rt, rtdir FROM visits WHERE day IN ('{normal[0]}', '{normal[1]}')
      GROUP BY ALL HAVING count(DISTINCT day) = 2""")
    present(con, marathon)
    con.execute(f"""CREATE OR REPLACE TABLE usual_visits AS
      SELECT day, rt, rtdir, stpid, sec FROM visits_aug
      WHERE (stpid, rt) IN (SELECT DISTINCT stpid, rt FROM usual_rd)
      UNION ALL
      SELECT '{marathon}', p.rt, u.rtdir, p.stpid, p.sec
      FROM usual_present p JOIN usual_rd u USING (stpid, rt)""")
    for name, unit in (("u", PHYS), ("ur", RDIR)):
        u = ", ".join(unit)
        on = " AND ".join(f"o.{c} = v.{c}" for c in unit)
        counts(con, unit, f"c{name}", letters, "usual_visits")
        con.execute(
            f"CREATE OR REPLACE TABLE d_{name} AS {dark(f'c{name}', unit, 'm', ['s', 'o'])}"
        )
        # each dark gap: its last bus (-1: none that day), next (NULL: none by 4 AM), how many
        # of its frames were judged dark and the first
        con.execute(f"""CREATE OR REPLACE TABLE stretch_{name} AS
          WITH o AS (SELECT {u}, gap, count(*) AS n, min(T) AS t0 FROM d_{name} WHERE dark
                     GROUP BY ALL),
          v AS (SELECT {u}, sec FROM usual_visits WHERE day = '{marathon}')
          SELECT o.*, v.sec AS next FROM o ASOF LEFT JOIN v ON {on} AND v.sec > o.gap""")
    # the way each usual route-direction's regular patterns go at the stop: from the nearest
    # point >= 40 m before it to the nearest >= 40 m after (as stop_bearing)
    con.execute("""CREATE OR REPLACE TABLE usual_travel AS
      WITH pts AS (SELECT p.pid, m.rt, m.rtdir, p.seq, p.typ, p.stpid, p.lat, p.lon
                   FROM ppoints p JOIN pmeta m USING (pid)
                   WHERE p.kind = 'pt' AND NOT m.is_detour),
      st AS (SELECT * FROM pts
             WHERE typ = 'S' AND (stpid, rt, rtdir) IN (SELECT stpid, rt, rtdir FROM usual_rd)),
      nb AS (SELECT st.pid, st.seq, st.stpid, st.lat, st.lon,
                    arg_max(p.lat, p.seq) FILTER (WHERE p.seq < st.seq) AS lat0,
                    arg_max(p.lon, p.seq) FILTER (WHERE p.seq < st.seq) AS lon0,
                    arg_min(p.lat, p.seq) FILTER (WHERE p.seq > st.seq) AS lat1,
                    arg_min(p.lon, p.seq) FILTER (WHERE p.seq > st.seq) AS lon1
             FROM st JOIN pts p ON p.pid = st.pid AND p.seq BETWEEN st.seq - 15 AND st.seq + 15
             WHERE sqrt(pow((p.lat - st.lat) * 110540, 2) + pow((p.lon - st.lon) * 82990, 2)) >= 40
             GROUP BY ALL),
      b AS (SELECT stpid,
                   degrees(atan2((coalesce(lon1, lon) - coalesce(lon0, lon)) * 82990,
                                 (coalesce(lat1, lat) - coalesce(lat0, lat)) * 110540)) AS brg
            FROM nb WHERE lat0 IS NOT NULL OR lat1 IS NOT NULL),
      w AS (SELECT stpid, ['N', 'E', 'S', 'W'][floor((brg + 405) % 360 / 90)::INT + 1] AS way
            FROM b)
      SELECT stpid, string_agg(DISTINCT way, '' ORDER BY way) AS travel FROM w GROUP BY stpid""")

    def minute(sec):
        return None if sec is None or sec < 0 else int(sec / 60 + 0.5)

    routes = dict(
        con.execute("""SELECT stpid, string_agg(rt || left(rtdir, 1), ','
                         ORDER BY try_cast(regexp_extract(rt, '^\\d+') AS INT) NULLS LAST,
                                  rt, rtdir)
                       FROM usual_rd GROUP BY stpid""").fetchall()
    )
    travel = dict(con.execute("SELECT stpid, travel FROM usual_travel").fetchall())
    whole, ur, came = {}, {}, {}
    for stpid, *s in con.execute("SELECT stpid, gap, n, t0, next FROM stretch_u").fetchall():
        whole.setdefault(stpid, []).append(s)
    for stpid, rd, *s in con.execute(
        "SELECT stpid, rt || left(rtdir, 1), gap, n, t0, next FROM stretch_ur ORDER BY ALL"
    ).fetchall():
        ur.setdefault(stpid, []).append((rd, *s))
    for stpid, rt, sec in con.execute(f"""
        SELECT stpid, rt, sec FROM visits_aug WHERE day = '{marathon}'
          AND (stpid, rt) NOT IN (SELECT DISTINCT stpid, rt FROM usual_rd)
        ORDER BY sec""").fetchall():
        came.setdefault(stpid, []).append((rt, sec))
    shuttles = {s for (s,) in con.execute("SELECT stpid FROM shuttle_stops").fetchall()}
    # the strip's dark frames (the dark rule: d_m_x, d_r_x) with a bus of its routes at the stop
    # (present) in them: where that leaves no stretch here, the stop finder can't tell
    close = {}
    for stpid, rt, a, b, buses in con.execute(f"""
        WITH r AS (SELECT stpid, T FROM d_m_x WHERE dark
                   UNION SELECT stpid, T FROM d_r_x WHERE dark)
        SELECT r.stpid, p.rt, min(p.sec), max(p.sec), count(DISTINCT p.vid)
        FROM r JOIN usual_present p ON p.stpid = r.stpid AND p.sec > r.T - {STEP} AND p.sec <= r.T
        GROUP BY ALL ORDER BY ALL""").fetchall():
        close.setdefault(stpid, []).append([rt, minute(a), minute(b), buses])

    def others(stpid, since, until):
        """Other routes' buses in (since, until): [route, first, last, buses]."""
        by = {}
        for rt, sec in came.get(stpid, []):
            if sec > since and (until is None or sec < until):
                by.setdefault(rt, []).append(sec)
        return [[rt, minute(v[0]), minute(v[-1]), len(v)] for rt, v in sorted(by.items())]

    def first_back(cut):
        return sorted(cut, key=lambda c: (c[2] is None, c[2] or 0, c[0]))

    def day(stpid):
        if stpid not in routes:  # no route on both normal Sundays: what came on the Marathon
            return [None, None, 0, [], others(stpid, -1, None)]
        if stpid in whole:  # the longest judged (ties: the earliest)
            gap, _, t0, nxt = max(whole[stpid], key=lambda s: (s[1], -s[0]))
            cut = []
            for rd, rgap, _, rt0, rnext in ur.get(stpid, []):
                if rgap > gap or (rnext is not None and (nxt is None or rnext < nxt)):
                    continue  # not its gap that holds the stop's
                # gone sooner: judged dark sooner, from its own last bus on (not one the night
                # before: "no #6 from 1:14 AM" when it doesn't run then anyway)
                sooner = rt0 < t0 and rt0 <= rgap + 2 * STEP and 0 <= rgap <= gap - LATER
                later = nxt is not None and (rnext is None or rnext - nxt >= LATER)
                if sooner or later:
                    cut.append([rd, minute(rgap) if sooner else None, minute(rnext)])
            n = len(whole[stpid])
            return [minute(gap), minute(nxt), n, first_back(cut), others(stpid, gap, nxt)]
        if stpid in ur:  # always a bus of its routes, but not of each: each one's longest
            best = {}
            for rd, rgap, rn, _, rnext in ur[stpid]:
                if rd not in best or rn > best[rd][0]:
                    best[rd] = (rn, rgap, rnext)
            cut = [[rd, minute(g), minute(x)] for rd, (_, g, x) in best.items()]
            return [None, None, 0, first_back(cut), []]
        if stpid in close:  # n = -1: can't tell; came: its buses that came close
            return [None, None, -1, [], close[stpid]]
        return None

    return {
        s: [routes.get(s, ""), travel.get(s, ""), None if s in shuttles else day(s)]
        for s in set(routes) | set(came)
    }


def frames(
    con: duckdb.DuckDBPyConnection, marathon: str, normal: list[str], box: list[list[float]]
) -> dict:
    """stops.json: each stop in the box, one state a frame: 'd' dark, 'p' partly dark (a bus
    came, but a route-direction that normally serves it was dark), 'u' can't tell (a shuttle
    stop Bus Tracker never showed), 's' served or normally served, '.' no service then; with the
    dark count per frame and the same count between the normal Sundays (each against the other
    alone, the larger). Each stop: [lon, lat, name, states, routes, travel, day] (stop_days):
    routes its usual route-directions ("22N,36N"), travel the way they go there ("N"), day null
    (nothing to tell, or a shuttle stop) or [from, to, n, cut, came]: on the Marathon, its
    longest stretch without a bus of its usual routes, from its last one (minutes after
    midnight; None: none that day) to the next (None: none by 4 AM; past 1440: after midnight);
    n such stretches; cut [route-direction, from, to] its routes out longer than that (from
    None unless gone 10+ min sooner); came [route, first, last, buses] other routes' buses inside
    it. n = 0: no such stretch, but cut has each route-direction that had one (its longest), or,
    at a stop no route served on both normal Sundays, came has every bus of the day. n = -1:
    can't tell (the strip has it without buses, but some of them came close: came)."""
    con.execute("""CREATE OR REPLACE TEMP TABLE st AS
      WITH rd AS (SELECT stpid, f, bool_or(dark) AS any_rd FROM d_r_x GROUP BY ALL)
      SELECT c.stpid, c.f,
        CASE WHEN (d.dark OR coalesce(rd.any_rd, false))
                  AND c.stpid IN (SELECT stpid FROM shuttle_stops) THEN 'u'
             WHEN d.dark THEN 'd'
             WHEN coalesce(rd.any_rd, false) THEN 'p'
             WHEN c.m60 >= 1 OR c.s60 >= 1 OR c.o60 >= 1 THEN 's'
             ELSE '.' END AS code
      FROM cs_x c JOIN d_m_x d USING (stpid, f) LEFT JOIN rd USING (stpid, f)""")
    rows = con.execute(f"""
      SELECT round(sd.lon, 5), round(sd.lat, 5), regexp_replace(trim(sd.stpnm), '\\s+', ' ', 'g'),
             string_agg(s.code, '' ORDER BY s.f), sd.stpid
      FROM st s JOIN stop_dim sd USING (stpid)
      WHERE sd.lon BETWEEN {box[0][0]} AND {box[1][0]}
        AND sd.lat BETWEEN {box[0][1]} AND {box[1][1]}
      GROUP BY sd.stpid, sd.lon, sd.lat, sd.stpnm
      HAVING regexp_matches(string_agg(s.code, ''), '[sdpu]')
      ORDER BY sd.stpid""").fetchall()
    told = stop_days(con, marathon, normal)
    rows = [[*r[:4], *told.get(r[4], ["", "", None])] for r in rows]

    def per(sql):
        return [n for _, n in con.execute(sql).fetchall()]

    dark_n = per("SELECT f, count(*) FILTER (WHERE code = 'd') FROM st GROUP BY f ORDER BY f")
    noise = [
        max(a, b)
        for a, b in zip(
            per("SELECT f, count(*) FILTER (WHERE dark) FROM d_s_o_x GROUP BY f ORDER BY f"),
            per("SELECT f, count(*) FILTER (WHERE dark) FROM d_o_s_x GROUP BY f ORDER BY f"),
            strict=True,
        )
    ]
    return {
        "start": F0 // 60,
        "step": STEP // 60,
        "frames": NF,
        "bounds": box,
        "stops": rows,
        "dark": dark_n,
        "noise": noise,
    }
