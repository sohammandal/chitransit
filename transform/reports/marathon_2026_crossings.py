"""The Marathon retro's wall: every time a bus crossed the course line, and where it went under it.

A crossing is an exact intersection of a bus's track (two consecutive reports of one bus, at most
120 s apart and at most 28 m/s) with the course line, where the bus segment is at least 20 m long,
meets the course at 30 degrees or more, and doesn't have both ends within 25 m of the line (that's a
bus driving along a course street, GPS jitter and all). Hits of one bus within 120 s and 150 m of
course are one crossing; an even number of hits means it touched the line and turned back (dropped).
A crossing is "under" where an expressway passes beneath the course and the bus was on one of CTA's
reroutes (the Marathon's detours sent buses through those underpasses).

Every table goes into the connection given (the retro's base DuckDB file); reads pos and pmeta.
"""

import math

import duckdb

LAT0, LON0 = 41.88, -87.64
PHI = math.radians(LAT0)
KY = 111132.954 - 559.822 * math.cos(2 * PHI) + 1.175 * math.cos(4 * PHI)
KX = 111412.84 * math.cos(PHI) - 93.5 * math.cos(3 * PHI) + 0.118 * math.cos(5 * PHI)
CELL, NEAR, BUF = 100.0, 25.0, 30.0  # grid cell, "on the course street", course cell buffer (m)

# Expressways under the course: (name, course miles from-to, lat from-to, lon from-to)
UNDERPASSES = [
    ("I-290 under Halsted St", 17.0, 17.4, 41.8748, 41.8766, -87.650, -87.644),
    ("I-55 under Michigan Ave", 24.3, 24.8, 41.8462, 41.8494, -87.626, -87.621),
    ("I-90/94 under 26th St", 21.8, 22.2, 41.8440, 41.8470, -87.632, -87.628),
]


def build(con: duckdb.DuckDBPyConnection, line: list, at: list, days: list[str]) -> None:
    """Table xing (day, vid, rt, pid, t, mile, kind 'cross' or 'under') from the course `line`
    ([lon, lat] points) with `at` (miles along it at each point)."""
    rows = [
        (
            j,
            (a[0] - LON0) * KX,
            (a[1] - LAT0) * KY,
            (b[0] - LON0) * KX,
            (b[1] - LAT0) * KY,
            at[j],
            at[j + 1],
        )
        for j, (a, b) in enumerate(zip(line, line[1:], strict=False))
    ]
    con.execute(
        "CREATE OR REPLACE TABLE cseg (j INT, ax DOUBLE, ay DOUBLE, ex DOUBLE, ey DOUBLE, "
        "mile0 DOUBLE, mile1 DOUBLE)"
    )
    con.executemany("INSERT INTO cseg VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"""CREATE OR REPLACE TABLE ccell AS
      WITH a AS (SELECT j,
                        unnest(range(floor((least(ax, ex) - {BUF}) / {CELL})::BIGINT,
                                     floor((greatest(ax, ex) + {BUF}) / {CELL})::BIGINT + 1)) AS cx,
                        floor((least(ay, ey) - {BUF}) / {CELL})::BIGINT AS cy0,
                        floor((greatest(ay, ey) + {BUF}) / {CELL})::BIGINT AS cy1
                 FROM cseg)
      SELECT j, cx, unnest(range(cy0, cy1 + 1)) AS cy FROM a""")
    x0, x1, y0, y1 = con.execute(
        "SELECT min(least(ax, ex)), max(greatest(ax, ex)), min(least(ay, ey)), "
        "max(greatest(ay, ey)) FROM cseg"
    ).fetchone()
    days_sql = ", ".join(f"'{d}'" for d in days)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE xrep AS
      SELECT day, vid, t, rt, pid, (lon - ({LON0})) * {KX} AS x, (lat - {LAT0}) * {KY} AS y
      FROM pos WHERE day IN ({days_sql})""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE bseg AS
      SELECT row_number() OVER () AS sid, * FROM (
        SELECT day, vid, lag(t) OVER w AS t1, t AS t2, lag(x) OVER w AS x1, lag(y) OVER w AS y1,
               x AS x2, y AS y2, rt, pid
        FROM xrep WINDOW w AS (PARTITION BY day, vid ORDER BY t))
      WHERE t1 IS NOT NULL AND t2 - t1 BETWEEN 1 AND 120 AND sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2) > 0
        AND sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2) / (t2 - t1) <= 28
        AND greatest(x1, x2) >= {x0 - BUF} AND least(x1, x2) <= {x1 + BUF}
        AND greatest(y1, y2) >= {y0 - BUF} AND least(y1, y2) <= {y1 + BUF}""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE cand AS
      WITH b AS (SELECT sid,
                        unnest(range(floor(least(x1, x2) / {CELL})::BIGINT,
                                     floor(greatest(x1, x2) / {CELL})::BIGINT + 1)) AS cx,
                        floor(least(y1, y2) / {CELL})::BIGINT AS cy0,
                        floor(greatest(y1, y2) / {CELL})::BIGINT AS cy1
                 FROM bseg),
      bc AS (SELECT sid, cx, unnest(range(cy0, cy1 + 1)) AS cy FROM b)
      SELECT DISTINCT bc.sid, c.j FROM bc JOIN ccell c USING (cx, cy)""")
    last = len(rows) - 1
    con.execute(f"""CREATE OR REPLACE TEMP TABLE xraw AS
      WITH k AS (SELECT s.*, c.j, c.ax, c.ay, c.ex, c.ey, c.mile0, c.mile1,
                        s.x2 - s.x1 AS rx, s.y2 - s.y1 AS ry, c.ex - c.ax AS sx, c.ey - c.ay AS sy
                 FROM cand JOIN bseg s USING (sid) JOIN cseg c USING (j)),
      d AS (SELECT *, rx * sy - ry * sx AS den, (ax - x1) * sy - (ay - y1) * sx AS tn,
                   (ax - x1) * ry - (ay - y1) * rx AS un
            FROM k)
      SELECT day, vid, rt, pid, x1, y1, x2, y2, t1 + (tn / den) * (t2 - t1) AS tx,
             mile0 + (un / den) * (mile1 - mile0) AS mile,
             x1 + (tn / den) * rx AS xx, y1 + (tn / den) * ry AS yx,
             abs(den) / (sqrt(rx * rx + ry * ry) * sqrt(sx * sx + sy * sy)) AS sin_ang,
             sqrt(rx * rx + ry * ry) AS seg_m
      FROM d
      WHERE den <> 0 AND tn / den >= 0 AND tn / den < 1 AND un / den >= 0
        AND (un / den < 1 OR (j = {last} AND un / den <= 1))""")
    # how far each segment's ends are from the course line (to drop buses driving along a course
    # street)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE xends AS
      WITH p AS (SELECT DISTINCT x, y
                 FROM (SELECT x1 AS x, y1 AS y FROM xraw UNION ALL SELECT x2, y2 FROM xraw)),
      k AS (SELECT p.x, p.y, s.ax, s.ay, s.ex - s.ax AS sx, s.ey - s.ay AS sy FROM p
            JOIN ccell c ON c.cx = floor(p.x / {CELL})::BIGINT
                        AND c.cy = floor(p.y / {CELL})::BIGINT
            JOIN cseg s USING (j)),
      w AS (SELECT *, greatest(0, least(1, ((x - ax) * sx + (y - ay) * sy)
                                           / (sx * sx + sy * sy))) AS u
            FROM k)
      SELECT x, y, min(sqrt((x - ax - u * sx) ^ 2 + (y - ay - u * sy) ^ 2)) AS dist
      FROM w GROUP BY x, y""")
    under = " OR ".join(
        f"(mile BETWEEN {m0} AND {m1} AND lat BETWEEN {a0} AND {a1} AND lon BETWEEN {o0} AND {o1})"
        for _, m0, m1, a0, a1, o0, o1 in UNDERPASSES
    )
    con.execute(f"""CREATE OR REPLACE TABLE xing AS
      WITH f AS (
        SELECT x.* FROM xraw x
        LEFT JOIN xends e1 ON e1.x = x.x1 AND e1.y = x.y1
        LEFT JOIN xends e2 ON e2.x = x.x2 AND e2.y = x.y2
        WHERE x.seg_m >= 20 AND x.sin_ang >= 0.5
          AND NOT (coalesce(e1.dist, 1e9) <= {NEAR} AND coalesce(e2.dist, 1e9) <= {NEAR})),
      o AS (SELECT *,
                   CASE WHEN tx - lag(tx) OVER w <= 120
                         AND abs(mile - lag(mile) OVER w) <= 150 / 1609.344 THEN 0
                        ELSE 1 END AS brk
            FROM f WINDOW w AS (PARTITION BY day, vid ORDER BY tx, mile)),
      c AS (SELECT *, sum(brk) OVER (PARTITION BY day, vid ORDER BY tx, mile) AS cl FROM o),
      e AS (SELECT day, vid, cl, count(*) % 2 = 0 AS touch, arg_min(tx, tx) AS t,
                   arg_min(mile, tx) AS mile, arg_min(rt, tx) AS rt, arg_min(pid, tx) AS pid,
                   {LAT0} + arg_min(yx, tx) / {KY} AS lat, {LON0} + arg_min(xx, tx) / {KX} AS lon
            FROM c GROUP BY day, vid, cl)
      SELECT e.day, e.vid, e.rt, e.pid, e.t, e.mile,
             CASE WHEN ({under}) AND coalesce(m.is_detour, false) THEN 'under'
                  ELSE 'cross' END AS kind
      FROM e LEFT JOIN pmeta m USING (pid) WHERE NOT e.touch""")
