"""Evaluate a poller run against week 1's done-when checks (docs/PLAN.md, steps 3 and 4f).

Usage: uv run python scripts/check_run.py --since "2026-09-25 20:37" [--until ...] [--rt 79]
Times are Chicago local. Reads the database named by GHOST_LEDGER_ENV (dev by default) and
the GTFS zip in data/gtfs/. Prototype of the daily integrity checks Dagster runs in week 4.
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from spike_match import load_scheduled_by_pid  # noqa: E402

from ghost_ledger.config import get_settings  # noqa: E402

CHICAGO = ZoneInfo("America/Chicago")
GRACE = timedelta(minutes=10)
# Bus Tracker stst is the time at pattern distance 0; GTFS starts at the first stop, which
# can sit a few hundred feet in (#3: 361 ft, 28 s later). Accept a GTFS start 0-120 s later.
START_TOLERANCE_S = 120


def local(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=CHICAGO)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True)
    p.add_argument("--until")
    p.add_argument("--rt", default="79", help='route to match, or "all"')
    args = p.parse_args()
    since = local(args.since)
    until = local(args.until) if args.until else datetime.now(CHICAGO)
    settings = get_settings()

    with psycopg.connect(settings.database_url) as conn:
        sweeps = conn.execute(
            "SELECT started_at, calls_total, calls_ok, vehicles FROM ops.sweeps "
            "WHERE feed = 'bus' AND started_at >= %s AND started_at < %s ORDER BY started_at",
            (since, until),
        ).fetchall()
        calls = conn.execute(
            "SELECT endpoint, COUNT(*), COUNT(*) FILTER (WHERE error IS NOT NULL), "
            "COUNT(*) FILTER (WHERE attempts > 1), MAX(latency_ms) "
            "FROM ops.raw_polls WHERE fetched_at >= %s AND fetched_at < %s GROUP BY 1 ORDER BY 1",
            (since, until),
        ).fetchall()
        trips = conn.execute(
            "SELECT DISTINCT rt, pid, stsd, stst FROM ops.bus_positions "
            "WHERE fetched_at >= %s AND fetched_at < %s AND (rt = %s OR %s = 'all') "
            "AND stsd IS NOT NULL",
            (since, until, args.rt, args.rt),
        ).fetchall()
        sightings = conn.execute(
            "SELECT p.vid, p.fetched_at, p.rt, p.stsd, p.stst FROM ops.bus_positions p "
            "WHERE p.fetched_at >= %s AND p.fetched_at < %s ORDER BY p.vid, p.fetched_at",
            (since, until),
        ).fetchall()
        # every sweep a bus appears in, including repeats that ON CONFLICT skipped, is in
        # raw_polls only as counts, so presence comes from positions (one row per new report)

    minutes = (until - since).total_seconds() / 60
    expected = int(minutes * 60 // settings.poll_interval_seconds)
    starts = [s[0] for s in sweeps]
    pairs = zip(starts, starts[1:], strict=False)
    gaps = [(a, b) for a, b in pairs if (b - a).total_seconds() > 45]
    ok_sweeps = sum(1 for s in sweeps if s[2] == s[1])
    print(f"window        {since:%Y-%m-%d %H:%M} -> {until:%H:%M} Chicago ({minutes:.0f} min)")
    print(
        f"sweeps        {len(sweeps)} of ~{expected} expected "
        f"({len(sweeps) / max(expected, 1):.1%}); all calls OK in {ok_sweeps}"
    )
    print(f"gaps > 45 s   {len(gaps)}")
    for a, b in gaps:
        print(f"  {a:%H:%M:%S} -> {b:%H:%M:%S}")
    for endpoint, n, failed, retried, max_ms in calls:
        print(f"calls         {endpoint}: {n}, failed {failed}, retried {retried}, max {max_ms} ms")

    # Route match against the GTFS schedule on (rt, pid, service day, start). After midnight,
    # buses report the previous service day with stst past 24:00 (e.g. Sep 25 at 24:47), while
    # CTA's GTFS lists the same trip on the next day at 00:47: try the reported day, then the
    # rolled-over one. A trip matches when exactly one GTFS trip starts 0-120 s after stst.
    routes = {t[0] for t in trips}
    sched: dict[tuple, list] = {}
    for d in {t[2] for t in trips} | {t[2] + timedelta(days=1) for t in trips}:
        for (rt, pid), starts in load_scheduled_by_pid(routes, d).items():
            sched[(rt, pid, d)] = starts

    def candidates(rt: str, pid: int, stsd, stst: int) -> list:
        for day, start in ((stsd, stst), (stsd + timedelta(days=1), stst - 86400)):
            found = [
                s for s in sched.get((rt, pid, day), []) if 0 <= s[0] - start <= START_TOLERANCE_S
            ]
            if found:
                return found
        return []

    unmatched = [(t, n) for t in trips if (n := len(candidates(*t))) != 1]
    exact = len(trips) - len(unmatched)
    label = f"#{args.rt}" if args.rt != "all" else "all"
    print(
        f"match {label:<7} {exact}/{len(trips)} trips match exactly one GTFS trip "
        f"({exact / max(len(trips), 1):.1%})"
    )
    for t, n in unmatched:
        print(f"  unmatched: {t} ({n} candidates)")

    # churn: gaps in a bus's reports on the same trip
    by_vid: dict[str, list] = defaultdict(list)
    for vid, fetched_at, rt, stsd, stst in sightings:
        by_vid[vid].append((fetched_at, (rt, stsd, stst)))
    absorbed = false_goodbyes = 0
    for rows in by_vid.values():
        for (t0, trip0), (t1, trip1) in zip(rows, rows[1:], strict=False):
            gap = t1 - t0
            if trip0 == trip1 and gap > timedelta(minutes=1):
                if gap < GRACE:
                    absorbed += 1
                else:
                    false_goodbyes += 1
    print(
        f"churn         {len(by_vid)} buses; {absorbed} dropouts of 1-10 min absorbed by the "
        f"grace period; {false_goodbyes} returns on the same trip after >=10 min "
        "(delete + re-insert)"
    )
    print(
        "note          a 'dropout' here is >1 min without a NEW report; a bus repeating its "
        "last report counts as present to the poller but adds no row, so this overcounts"
    )


if __name__ == "__main__":
    main()
