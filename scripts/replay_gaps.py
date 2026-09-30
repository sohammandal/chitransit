"""Replay a stored day through the live feed's gap logic and score it (the gate for any change
to ghost_ledger.live's waits).

Ingress keeps every bus report, so a past day can be fed, sweep by sweep, to the production
LiveBoard exactly as the poller would (DDIA ch. 11: replay the log). Each sample of a bus is
scored against what hindsight knows, and the pass definition itself is checked against the
schedule:

  truth     the last pass at the bus's next stop by any other bus of the route and direction,
            from the whole day's reports (the same pass rules, all information). Samples whose
            true last pass came under 60 s before are left out: live learns of it one sweep on.
  old       gap_s (same pattern, at the bus's exact spot) vs truth at the next stop
  new       stop_gap_s vs truth
  flags     "long wait" (over twice the route's scheduled gap that hour, site_headways' rule):
            wrong when truth at that stop isn't a long wait
  coverage  per stop, observed passes (hindsight) / scheduled departures in the window, with
            the nightly's current rule (int_stop_passes) and the live rule side by side: a stop
            far below its route's median loses passes (e.g. #146's first stops, Sep 28)
  speed     seconds per snapshot (the poller has 30 s per sweep)

Gates (exit 1 on failure): new within 60 s >= 98.5%; by the site's rule (over twice the stop's
schedule, 2+ due): wrong flags <= 0.5% of flags, missed long waits <= 2% of real ones;
snapshot p95 <= 3 s.

  eval "$(aws configure export-credentials --profile ghost-ledger --format env)"
  uv run --group transform python scripts/replay_gaps.py --date 2026-09-28 [--start 05:00]
      [--until 24:00] [--routes 66,146]
Reads Ingress read-only; caches the day under data/replay/<date>/.
"""

import argparse
import importlib.util
import json
import math
import statistics
import sys
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ghost_ledger.live as live_rules  # noqa: E402
from ghost_ledger.live import (  # noqa: E402
    STALE,
    STEP,
    TRAIL,
    LiveBoard,
    Schedule,
    departed_at,
    runs,
    trip_passes,
)

CHI = ZoneInfo("America/Chicago")
ROOT = Path(__file__).resolve().parents[1]
GTFS = ROOT / "data" / "gtfs_parquet"
SWEEP = timedelta(seconds=30)
NEAR_M = 150  # the GPS check: a bus this close to a stop was at it
SAME_PASS = timedelta(minutes=5)
LONG_BLACKOUT = timedelta(minutes=30)


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)) * 111_320
    return math.hypot(x, (lat2 - lat1) * 110_540)


LOOKAHEAD = timedelta(seconds=60)


def at(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, tzinfo=CHI) + timedelta(hours=h, minutes=m)


def extract(day: date, start: datetime, until: datetime, cache: Path) -> None:
    """Positions (from TRAIL before start) and the last pattern version before `until`: its
    stops, and its whole shape (stops and waypoints) for the live board's on-route check."""
    files = ("pos.parquet", "stops.parquet", "shape.parquet")
    if all((cache / f).exists() for f in files):
        return
    cache.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    for ext in ("httpfs", "aws", "iceberg"):
        c.execute(f"INSTALL {ext}; LOAD {ext}")
    c.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    c.execute("ATTACH '354894954112' AS glue (TYPE iceberg, ENDPOINT_TYPE glue)")
    lo, hi = (start - TRAIL - timedelta(minutes=15)).isoformat(), until.isoformat()
    patterns = f"""
        WITH v AS (
            SELECT pid, rt, rtdir, pattern FROM glue.ghost_ledger_ingress.ghost_ops_patterns
            WHERE __op != 'd' AND changed_at < '{hi}'::timestamptz
            QUALIFY row_number() OVER (PARTITION BY pid ORDER BY changed_at DESC, __lsn DESC) = 1
        ),
        pts AS (
            SELECT pid, rt, rtdir, (pattern->>'ln')::double AS length_ft,
                unnest(from_json(json_extract(pattern, '$.pt'), '["JSON"]')) AS pt
            FROM v
        )"""  # noqa: S608
    if not (cache / "shape.parquet").exists():
        c.execute(f"""COPY ({patterns}
            SELECT pid, (pt->>'pdist')::double AS pdist, (pt->>'lat')::double AS lat,
                (pt->>'lon')::double AS lon
            FROM pts ORDER BY pid, pdist
        ) TO '{cache}/shape.parquet' (FORMAT parquet)""")  # noqa: S608
    if (cache / "pos.parquet").exists() and (cache / "stops.parquet").exists():
        return
    c.execute(f"""COPY (
        SELECT vid, observed_at, rt, pid, pdist, stsd, stst, lat, lon
        FROM glue.ghost_ledger_ingress.ghost_ops_bus_positions
        WHERE observed_at >= '{lo}'::timestamptz AND observed_at < '{hi}'::timestamptz
            AND pid IS NOT NULL AND pdist IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY vid, observed_at ORDER BY __lsn) = 1
    ) TO '{cache}/pos.parquet' (FORMAT parquet)""")  # noqa: S608 - our own dates
    c.execute(f"""COPY ({patterns}
        SELECT pid, rt, rtdir, length_ft, (pt->>'pdist')::double AS pdist, pt->>'stpid' AS stpid,
            pt->>'stpnm' AS name, (pt->>'lat')::double AS lat, (pt->>'lon')::double AS lon
        FROM pts WHERE (pt->>'typ') = 'S' AND (pt->>'stpid') IS NOT NULL
    ) TO '{cache}/stops.parquet' (FORMAT parquet)""")  # noqa: S608


def schedule(day: date) -> tuple[dict, dict, Schedule]:
    """(route headway s by (rt, dir word, hour), scheduled departures by (rt, stop_id), the
    live schedule the poller would have) from the local GTFS Parquet. The route rule is
    site_headways' (60 min / trips starting that hour); the live schedule is the nightly's
    (transform/live_schedule.py: every departure but each trip's last stop)."""
    c = duckdb.connect()
    ymd, dow = day.strftime("%Y%m%d"), day.strftime("%A").lower()
    feed = c.sql(f"""SELECT max(feed_id) FROM read_parquet('{GTFS}/calendar/**/*.parquet',
        hive_partitioning=true) WHERE start_date <= '{ymd}'""").fetchone()[0]  # noqa: S608
    src = lambda t: f"read_parquet('{GTFS}/{t}/feed_id={feed}/*.parquet')"  # noqa: E731
    c.execute(f"""CREATE TABLE active AS
        SELECT service_id FROM {src("calendar")}
        WHERE '{ymd}' BETWEEN start_date AND end_date AND {dow} = '1'
        UNION SELECT service_id FROM {src("calendar_dates")}
            WHERE date = '{ymd}' AND exception_type = '1'
        EXCEPT SELECT service_id FROM {src("calendar_dates")}
            WHERE date = '{ymd}' AND exception_type = '2'
    """)  # noqa: S608
    c.execute(f"""CREATE TABLE st AS
        SELECT t.route_id AS rt, t.direction, t.trip_id, s.stop_id,
            s.stop_sequence::int = max(s.stop_sequence::int)
                OVER (PARTITION BY t.trip_id) AS is_last,
            split_part(s.departure_time, ':', 1)::int * 3600
                + split_part(s.departure_time, ':', 2)::int * 60
                + split_part(s.departure_time, ':', 3)::int AS dep_s
        FROM {src("trips")} t JOIN active USING (service_id)
        JOIN {src("stop_times")} s USING (trip_id)
    """)  # noqa: S608
    route = {
        (rt, d, h): 3600 / n
        for rt, d, h, n in c.sql("""
            SELECT rt, direction, (dep_s // 3600) % 24 AS h, count(*)
            FROM (SELECT rt, direction, trip_id, min(dep_s) AS dep_s FROM st GROUP BY ALL)
            GROUP BY ALL""").fetchall()
    }
    deps: dict[tuple[str, str], list[int]] = defaultdict(list)
    for rt, stop, dep in c.sql("SELECT rt, stop_id, dep_s FROM st").fetchall():
        deps[(rt, stop)].append(dep)
    midnight = int(at(day, "00:00").timestamp())
    rows = [
        (rt, stop, day, midnight + dep)
        for rt, stop, dep in c.sql("SELECT rt, stop_id, dep_s FROM st WHERE NOT is_last").fetchall()
    ]
    spec = importlib.util.spec_from_file_location(
        "live_schedule", ROOT / "transform/live_schedule.py"
    )
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    return route, deps, Schedule(writer.build(rows))


def nightly_rule(points, stops):
    """The nightly's current rule (int_stop_passes), for the coverage comparison: forward steps
    from the running furthest point of the whole trip, earliest pass per stop."""
    out, furthest, seen = [], float("-inf"), set()
    for (t0, d0), (t1, d1) in zip(points, points[1:], strict=False):
        at_furthest = d0 >= furthest
        furthest = max(furthest, d0)
        if not (at_furthest and d1 > d0 and t1 - t0 <= STEP):
            continue
        for k, (feet, sid, _) in enumerate(stops):
            if d0 <= feet <= d1 and k not in seen:
                seen.add(k)
                out.append((t0 + (t1 - t0) * ((feet - d0) / (d1 - d0)), sid))
    return out


DUMPED = ("vid", "rt", "dir", "pid", "pdist", "at_terminal", "next_stop", "next_stpid",
          "stop_gap_s", "last_by", "gap_s", "ahead", "sched_s", "sched_due", "long_wait",
          "together_with", "together_s", "behind_vid", "behind_stops")  # fmt: skip


def dump_row(v: dict, t: datetime, truth: dict) -> dict:
    """One sampled bus for --dump: its live fields, and hindsight at its next stop (the last
    pass by another bus before now; when it got there itself, and the passes just before and
    after its own: the true headways in front and behind)."""
    passes = truth.get((v["rt"], v["dir"], v.get("next_stpid")), [])
    k = bisect_right(passes, (t, "\uffff", t))
    last = next(((w, o) for w, o, _ in reversed(passes[:k]) if o != v["vid"]), None)
    row = {f: v.get(f) for f in DUMPED}
    row["t"] = t.isoformat()
    row["truth_s"] = None if last is None else round((t - last[0]).total_seconds())
    row["truth_by"] = None if last is None else last[1]
    mine = next((w for w, o, _ in passes if o == v["vid"] and w >= t), None)
    if mine is not None:
        before = [w for w, o, _ in passes if o != v["vid"] and w < mine]
        after = [w for w, o, _ in passes if o != v["vid"] and w > mine]
        row["true_front_s"] = round((mine - max(before)).total_seconds()) if before else None
        row["true_behind_s"] = round((min(after) - mine).total_seconds()) if after else None
        row["arrive_s"] = round((mine - t).total_seconds())
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, type=date.fromisoformat)
    ap.add_argument("--start", default="05:00")
    ap.add_argument("--until", default="24:00", help="HH:MM, may pass 24:00 (next day)")
    ap.add_argument("--every", type=int, default=5, help="minutes between scored snapshots")
    ap.add_argument("--routes", default="", help="comma list: only these routes")
    ap.add_argument("--show-wrong", type=int, default=0, help="print N wrong new flags")
    ap.add_argument(
        "--dump",
        help="write every sampled bus (terminal buses too) with its live"
        " claim and the hindsight last pass, one JSON line each",
    )
    args = ap.parse_args()
    day, cache = args.date, ROOT / "data" / "replay" / f"{args.date.isoformat()}-v2"
    start, until = at(day, args.start), at(day, args.until)
    extract(day, start, until, cache)
    routes = set(filter(None, args.routes.split(",")))

    board = LiveBoard()
    stop_geo: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for pid, rt, rtdir, length, feet, sid, name, lat, lon in duckdb.sql(
        "SELECT pid, rt, rtdir, length_ft, pdist, stpid, name, lat, lon"
        f" FROM '{cache}/stops.parquet'"
        " ORDER BY pid, pdist"  # noqa: S608
    ).fetchall():
        stop_geo[(rt, rtdir, sid)].append((lat, lon))
        board.stop_ids.setdefault(int(pid), []).append((float(feet), sid, name or ""))
        board.stops.setdefault(int(pid), []).append((float(feet), name or ""))
        board.routes[int(pid)], board.dirs[int(pid)] = rt, rtdir
        if length:
            board.lengths[int(pid)] = float(length)
    for pid, feet, lat, lon in duckdb.sql(f"SELECT * FROM '{cache}/shape.parquet'").fetchall():
        if None not in (feet, lat, lon):
            board.shapes.setdefault(int(pid), []).append((float(feet), float(lat), float(lon)))
    where = f"WHERE rt IN ({','.join(repr(r) for r in routes)})" if routes else ""
    rows = duckdb.sql(
        f"SELECT vid, observed_at, rt, pid, pdist, stsd, stst, lat, lon FROM '{cache}/pos.parquet' {where} ORDER BY observed_at"  # noqa: S608, E501
    ).fetchall()
    print(f"{len(rows):,} reports, {len(board.stop_ids)} patterns")
    # Every report's GPS position by route, for the independent check below
    geo: dict[str, list] = defaultdict(list)
    last_seen: dict[str, tuple[int, float]] = {}  # vid -> (pid, pdist) of its previous report
    for vid, t, rt, pid, pdist, lat, lon in duckdb.sql(
        f"SELECT vid, observed_at, rt, pid, pdist, lat, lon FROM '{cache}/pos.parquet' {where}"
        " ORDER BY observed_at"  # noqa: S608
    ).fetchall():
        if lat is not None:
            prev = last_seen.get(vid)
            before = prev[1] if prev and prev[0] == int(pid) else None
            geo[rt].append((t, vid, int(pid), float(pdist), lat, lon, before))
            last_seen[vid] = (int(pid), float(pdist))
    geo_times = {rt: [g[0] for g in rows_] for rt, rows_ in geo.items()}

    # Hindsight: every pass of the day, by the live rule and by the nightly's current rule.
    trips: dict[tuple, list] = defaultdict(list)  # by CTA trip id: the nightly's old rule only
    trails: dict[str, list] = defaultdict(list)  # as the live board keeps them
    for vid, t, _rt, pid, pdist, stsd, stst, lat, lon in rows:
        if not board.on_route({"pid": pid, "pdist": pdist, "lat": lat, "lon": lon}):
            continue  # as the live board does
        trip = (stsd, stst) if stsd is not None and stst is not None else None
        trails[vid].append((t, int(pid), float(pdist), trip))
        if trip is not None:
            trips[(vid, int(pid), stsd, stst)].append((t, float(pdist)))
    live_runs = [(vid, pid, pts) for vid, tr in trails.items() for pid, pts in runs(tr)]
    truth: dict[tuple, list] = defaultdict(list)
    unsure: set[tuple] = set()  # (vid, passed_at) timed only as a bound across a long blackout
    at_stop: dict[tuple, list] = defaultdict(list)  # (arrived, left, vid) of buses that stood
    counts = {"live": defaultdict(int), "nightly": defaultdict(int)}
    for vid, pid, points in live_runs:
        stops, rt, rtdir = board.stop_ids.get(pid), board.routes.get(pid), board.dirs.get(pid)
        if not stops or rt is None:
            continue
        left = departed_at(points)
        times = [p[0] for p in points]
        # When it got there (the crossing, before any time standing at the stop): between
        # arriving and leaving, a bus is at the stop and nobody there is waiting.
        # (fixed at 100 ft here, whatever the rules under test say)
        saved, live_rules.DWELL_FT = live_rules.DWELL_FT, -1
        arrivals = trip_passes(points, stops[:-1])
        live_rules.DWELL_FT = 100
        departures = trip_passes(points, stops[:-1])
        live_rules.DWELL_FT = saved
        for (arr, _), (dep, sid) in zip(arrivals, departures, strict=False):
            if dep > arr:
                at_stop[(rt, rtdir, sid)].append((arr, dep, vid))
        for when, sid in trip_passes(points, stops[:-1]):
            # knowable from the passing bus's next report, and not before its trip got going
            k = bisect_right(times, when)
            known = max(times[min(k, len(times) - 1)], left or when)
            # a pass stamped across a long blackout is only a bound ("no later than"): when it
            # really happened is unknown, so it can't prove a claim wrong
            j = bisect_left(times, when)
            if j > 0 and times[j - 1] < when - LONG_BLACKOUT:
                unsure.add((vid, when))
            truth[(rt, rtdir, sid)].append((when, vid, known))
            if start <= when < until:
                counts["live"][(rt, sid)] += 1

    for (_vid, pid, _, _), points in trips.items():
        stops, rt = board.stop_ids.get(pid), board.routes.get(pid)
        if not stops or rt is None:
            continue
        for when, sid in nightly_rule(points, stops):
            if start <= when < until:
                counts["nightly"][(rt, sid)] += 1
    for v in truth.values():
        v.sort()

    route_hw, deps, live_schedule = schedule(day)
    board.schedule = live_schedule
    midnight = at(day, "00:00")

    # Sweeps every 30 s. Each carries every report stored since the last one (the poller saw
    # each of them: that's why they're in Ingress; its sweeps just don't fall on this grid)
    # and the other buses' latest reports again, as the API repeats them.
    latest, i, t, snap_s = {}, 0, start - TRAIL, []
    samples = []
    next_sample = start
    while t <= until:
        new = []
        while i < len(rows) and rows[i][1] <= t:
            vid, obs, rt, pid, pdist, stsd, stst, lat, lon = rows[i]
            latest[vid] = {"vid": vid, "rt": rt, "pid": int(pid), "pdist": float(pdist),
                           "observed_at": obs, "stsd": stsd, "stst": stst, "lat": lat,
                           "lon": lon}  # fmt: skip
            new.append(latest[vid])
            i += 1
        fresh = {p["vid"] for p in new}
        repeats = [p for v, p in latest.items() if v not in fresh and t - p["observed_at"] <= STALE]
        board.update(repeats + new, t)
        if t >= next_sample:
            t0 = time.perf_counter()
            snap = board.snapshot(t)
            snap_s.append(time.perf_counter() - t0)
            if args.dump:
                with open(args.dump, "a") as out:
                    for v in snap["vehicles"]:
                        out.write(json.dumps(dump_row(v, t, truth)) + "\n")
            for v in snap["vehicles"]:
                if v["at_terminal"] or v["pid"] is None or v["next_stpid"] is None:
                    continue
                key = (v["rt"], v["dir"], v["next_stpid"])
                passes = truth.get(key, [])
                k = bisect_right(passes, (t, "￿", t))
                last = next(((w, kn) for w, o, kn in reversed(passes[:k]) if o != v["vid"]), None)
                hour = ((t - midnight).seconds // 3600) % 24
                hw = route_hw.get((v["rt"], (v["dir"] or "").replace("bound", ""), hour))
                samples.append((t, v, last, hw))
            next_sample += timedelta(minutes=args.every)
        t += SWEEP

    def score(field):
        """Against hindsight. Skipped: samples whose last pass only became knowable in the next
        sweep (live catches up within 30 s). Misses: real long waits (truth over twice the
        schedule by more than a minute) that live leaves unflagged, as unknown or measured
        under the line."""
        n = within = over = unknown = flags = wrong = true_long = miss_unknown = miss_under = 0
        beyond = late = 0
        by_rt: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # wrong, flags, missed
        for t, v, last, hw in samples:
            if last is None or t < last[1] <= t + LOOKAHEAD:
                continue
            n += 1
            g, true_s = v[field], (t - last[0]).total_seconds()
            if true_s > TRAIL.total_seconds():
                beyond += 1  # longer than live keeps (first bus after a service gap): not scored
                continue
            if hw and true_s > 2 * hw + 60:
                true_long += 1
                if g is None or g <= 2 * hw:
                    miss_unknown += g is None
                    miss_under += g is not None
                    by_rt[v["rt"]][2] += 1
            if g is None:
                unknown += 1
                continue
            within += abs(g - true_s) <= 60
            over += g - true_s > 120
            if hw and g > 2 * hw:
                flags += 1
                bad = true_s <= 2 * hw
                wrong += bad
                late += bad and last[1] > t  # the pass wasn't knowable yet (next report late)
                by_rt[v["rt"]][0] += bad
                by_rt[v["rt"]][1] += 1
        known = n - unknown
        return {
            "samples": n, "unknown": unknown, "within60": within / known if known else 0,
            "over2min": over / known if known else 0, "flags": flags, "wrong": wrong,
            "true_long": true_long, "miss_unknown": miss_unknown, "miss_under": miss_under,
            "beyond": beyond, "late": late,
            "by_rt": sorted(by_rt.items(), key=lambda kv: -(kv[1][0] + kv[1][2]))[:8],
        }  # fmt: skip

    old, new = score("gap_s"), score("stop_gap_s")
    shown = 0
    for t, v, last, hw in samples:
        g = v["stop_gap_s"]
        if shown >= args.show_wrong or last is None or t < last[1] <= t + LOOKAHEAD or not hw:
            continue
        true_s = (t - last[0]).total_seconds()
        wrong = g is not None and g > 2 * hw and true_s <= 2 * hw
        missed = TRAIL.total_seconds() >= true_s > 2 * hw + 60 and (g is None or g <= 2 * hw)
        if wrong or missed:
            shown += 1
            print(
                f"  {'wrong' if wrong else 'missed'}: {t:%H:%M} rt {v['rt']} {v['dir']}"
                f" vid {v['vid']} pid {v['pid']} at {v['pdist']} ft, next {v['next_stop']}"
                f" ({v['next_stpid']}): live {g} s by {v['last_by']}, truth {true_s:.0f} s,"
                f" scheduled {hw:.0f} s"
            )
    print(f"\nsamples every {args.every} min, {args.start}–{args.until} {day}: {len(samples):,}")
    for name, s in (("old gap_s", old), ("new stop_gap_s", new)):
        print(f"  {name:15} within 60 s {s['within60']:.1%}  overstated >2 min {s['over2min']:.2%}"
              f"  unknown {s['unknown']:,}")  # fmt: skip
        print(f"  {'':15} flags {s['flags']:,}, wrong {s['wrong']:,}"
              f" ({s['wrong'] / max(1, s['flags']):.1%});  real long waits {s['true_long']:,},"
              f" missed {s['miss_unknown'] + s['miss_under']:,} ({s['miss_unknown']:,} unknown,"
              f" {s['miss_under']:,} measured under)")  # fmt: skip
        print(f"  {'':15} longer than live keeps (not scored): {s['beyond']:,};"
              f" wrong flags whose pass wasn't knowable yet: {s['late']:,}")  # fmt: skip
        print("     worst routes (wrong/flags/missed):",
              ", ".join(f"{rt} {w}/{f}/{m}" for rt, (w, f, m) in s["by_rt"]))  # fmt: skip

    # The proposed site rule: over twice the stop's scheduled gap (sched_s) AND at least two
    # scheduled departures due since the last bus (sched_due): buses were due and didn't come.
    n = flags = wrong = true_long = missed = no_sched = not_due = 0
    why: dict[str, int] = defaultdict(int)
    geo_hits: list[str] = []
    geo_now = 0
    miss_why: dict[str, int] = defaultdict(int)
    examples: list[str] = []
    for t, v, last, _hw in samples:
        if last is None or t < last[1] <= t + LOOKAHEAD or (t - last[0]) > TRAIL:
            continue
        s_, g, due = v["sched_s"], v["stop_gap_s"], v["sched_due"]
        if not s_:
            no_sched += 1
            continue
        n += 1
        true_s = (t - last[0]).total_seconds()
        true_due = live_schedule.judge(v["rt"], v["next_stpid"], t, last[0])[1] or 0
        key_ = (v["rt"], v["dir"], v["next_stpid"])
        standing = [a for a, d, o in at_stop.get(key_, ()) if a <= t < d and o != v["vid"]]
        if standing:
            true_s = true_due = 0  # a bus is standing at the stop now: no one is waiting
            if all(t - a < SAME_PASS for a in standing):
                last = (standing[0], t + LOOKAHEAD * 2)  # just got there: live hears next sweep
        flagged = g is not None and g > 2 * s_ and (due or 0) >= 2  # the site's rule
        not_due += g is not None and g > 2 * s_ and (due or 0) < 2
        if flagged:
            # Independent of the pass logic: was any bus of the route and direction at the
            # stop (GPS within NEAR_M) during the claimed wait? A bus still lingering right
            # after its own pass (up to SAME_PASS) doesn't count.
            key = (v["rt"], v["dir"], v["next_stpid"])
            spots = stop_geo.get(key, [])
            own = {o: w for w, o, _ in truth.get(key, []) if w <= last[0] + LOOKAHEAD / 2}
            lo_i = bisect_right(geo_times.get(v["rt"], []), last[0] + LOOKAHEAD / 2)
            hi_i = bisect_right(geo_times.get(v["rt"], []), t)
            for gt, gvid, gpid, gft, glat, glon, gprev in geo.get(v["rt"], [])[lo_i:hi_i]:
                if gvid == v["vid"] or board.dirs.get(gpid) != v["dir"]:
                    continue
                # only a bus that serves this stop (its pattern has it) and has reached it:
                # one still approaching hasn't ended the wait yet; an express skipping it isn't
                # service there
                at_ft = [f for f, sid, _ in board.stop_ids.get(gpid, ()) if sid == v["next_stpid"]]
                # ...moving forward along its pattern, from before the stop: a bus driving the
                # other way on its next trip's pattern (J14 4307, Sep 28) goes past the stop's
                # corner without serving it
                if gprev is None or gft <= gprev:
                    continue
                if not any(gft >= f - 50 and gprev <= f + 50 for f in at_ft):
                    continue
                if gvid in own and gt <= own[gvid] + SAME_PASS:
                    continue
                if any(metres(glat, glon, a, b) <= NEAR_M for a, b in spots):
                    if t - gt <= timedelta(seconds=90):
                        geo_now += 1  # arriving as we speak: the wait ends now (report lag)
                        break
                    geo_hits.append(f"{t:%H:%M} rt {v['rt']} {v['dir']} vid {v['vid']} at"
                                    f" {v['next_stop']}: claimed {g // 60} min since"
                                    f" {last[0]:%H:%M:%S};"
                                    f" {gvid} was at the stop {gt:%H:%M:%S}")  # fmt: skip
                    break
            flags += 1
            bad = not (true_s > 2 * s_ and true_due >= 2)
            wrong += bad
            if bad:
                by_bus = next((o for w, o, _ in truth.get(key_, ()) if w == last[0]), None)
                kind = (
                    "tracker lag"
                    if last[1] > t  # a bus passed but its report hadn't come
                    else "unknowable (the passing bus was unseen 30+ min)"
                    if (by_bus, last[0]) in unsure
                    else "within 60 s at the line"
                    if abs(g - true_s) <= 60
                    else "other"
                )
                why[kind] += 1
                if kind == "other" and len(examples) < 8:
                    examples.append(f"{t:%H:%M} rt {v['rt']} vid {v['vid']} next {v['next_stop']}"
                                    f" live {g}s due {due} / truth {true_s:.0f}s due {true_due},"
                                    f" sched {s_}s")  # fmt: skip
        if true_s > 2 * s_ + 60 and true_due >= 2:
            true_long += 1
            missed += not flagged
            if not flagged:
                miss_why[
                    "couldn't tell (a dark bus may have passed)"
                    if g is None
                    else "fewer than 2 due live"
                    if (due or 0) < 2
                    else "measured under the line"
                ] += 1
    print(
        f"  stop rule (> 2x sched_s and >= 2 due): flags {flags:,}, wrong {wrong:,}"
        f" ({wrong / max(1, flags):.1%}); real long waits {true_long:,}, missed {missed:,}"
        f" ({missed / max(1, true_long):.1%}); not judged (nothing scheduled) {no_sched:,};"
        f" long but under 2 due {not_due:,}"
    )
    print("     wrong, by cause:", dict(why))
    print(f"     GPS check: a bus at the stop within the last 90 s (arriving now) {geo_now:,};"
          f" earlier in the claimed wait {len(geo_hits):,}")  # fmt: skip
    for h in geo_hits[:40]:
        print("       ", h)
    print("     missed, by cause:", dict(miss_why))
    for e in examples:
        print("     other:", e)

    # Coverage: observed passes per scheduled departure, per stop, in the window.
    lo_s, hi_s = (start - midnight).total_seconds(), (until - midnight).total_seconds()
    ratios: dict[str, dict[str, list]] = {"live": defaultdict(list), "nightly": defaultdict(list)}
    worst = []
    # A pattern's first and last stops are left out: buses lay over there (no wait is judged),
    # and trips often start past the first stop (#146 at the Planetarium).
    ends = {(board.routes[p], st[i][1]) for p, st in board.stop_ids.items() for i in (0, -1)}
    for (rt, stop), times in deps.items():
        n_sched = sum(lo_s <= d < hi_s for d in times)
        if n_sched < 10 or (routes and rt not in routes) or (rt, stop) in ends:
            continue
        for rule in ("live", "nightly"):
            ratios[rule][rt].append((counts[rule].get((rt, stop), 0) / n_sched, stop, n_sched))
    for rule in ("live", "nightly"):
        low = 0
        for rt, rs in ratios[rule].items():
            med = statistics.median(r for r, _, _ in rs)
            for r, stop, n_sched in rs:
                if r < 0.6 * med:
                    low += 1
                    if rule == "live":
                        worst.append((r / med if med else 0, rt, stop, n_sched))
        total = sum(len(rs) for rs in ratios[rule].values())
        print(
            f"  coverage, {rule:7} rule: {low} of {total} stops below 60% of their route's median"
        )
    for rel, rt, stop, n_sched in sorted(worst)[:10]:
        print(f"     route {rt} stop {stop}: {rel:.0%} of the route's median ({n_sched} scheduled)")

    p95 = sorted(snap_s)[int(0.95 * (len(snap_s) - 1))] if snap_s else 0
    print(f"  snapshot time: median {statistics.median(snap_s):.2f} s, p95 {p95:.2f} s")
    gates = {
        "new within 60 s >= 98.5%": new["within60"] >= 0.985,
        # the site's rule (the stop's own schedule, 2+ due), not the route-hour one above
        "wrong flags <= 0.5%": wrong <= 0.005 * max(1, flags),
        "missed long waits <= 2%": missed <= 0.02 * max(1, true_long),
        "snapshot p95 <= 3 s": p95 <= 3,
    }
    for g, ok in gates.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {g}")
    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
