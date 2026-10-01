"""The live feed for the public site: where every bus is now, and the claims about it.

After each committed sweep the poller hands this module the sweep's vehicle reports. It keeps
each bus's recent trail in memory and writes one JSON snapshot (docs/SITE.md, "live feed"):

  {"generated_at": ISO, "build": commit, "no_data_since": ISO | None, "vehicles": [{vid, rt,
   pid, hdg, lat, lon, dly, des, dir, t, pdist, at_terminal, near, ...the fields below}]}
  build  the commit the running poller was built from (CI waits for it after a deploy)
  no_data_since  when the last call got through, once none has for over HOLE (CTA's API down
         or unreachable): the site says so instead of "0 buses on the road"; else None
  t      the position's own time (epoch seconds); the site shows its age

  near   the name of the stop nearest the bus ("Clark & Belmont"), so the site can say where.

  Measured at the bus's next stop, counting every bus of the route in the same direction
  (short turns and branches too: a bus on another pattern that stopped here is service):
  stop_gap_s   how long ago the last bus passed the next stop (a measured wait: a rider there has
               been waiting that long); None ("can't tell") when no trail reaches back that far,
               when a bus went dark short of the stop since the last pass (it may have passed),
               or across a hole in what the poller could see (HOLE, BACKWARD_FT)
  last_by      the bus that made that pass
  next_stop    the stop's name; next_stpid: its CTA stop id
  A pass is interpolated between two reports of one trip (pid, stsd, stst), forward progress
  only, reports at most 5 minutes apart, as the nightly's int_stop_passes. At a pattern's
  start (its first TERMINAL_FT) a bus laying over can creep past its first stops long before
  it leaves, so there the pass is its last crossing before it leaves, and only once it has.
  (Sep 28: 16% of trips still sat at their first stop 2+ minutes after the naive pass.)

  Only serving passes count: a pass at a stop on the bus's own pattern, never its last stop
  (a bus arriving to end its trip serves nobody waiting there: #66 short turns end at Pulaski).
  The next stop is likewise the next stop the bus serves; a bus whose only stop left is its
  last is finishing and has none.

  Against the schedule at that stop (the nightly's live schedule, Schedule; None without it):
  sched_s    the scheduled gap there now: 3,600 s / departures in the past hour (all trips of
             the route), None when none were scheduled (no judgment in a service gap)
  sched_due  scheduled departures there since the last bus passed: a long wait means buses
             were due and didn't come, not just a long time (the first bus after a midday or
             overnight break has waited hours, with nothing due)

  The claims the site shows, decided here once:
  long_wait      stop_gap_s over twice sched_s (by LONG_MARGIN_S) with at least 2 departures due
  together_with  the bus just ahead at this bus's last serving pass (any pattern), when it
                 passed that stop under a quarter of the scheduled gap before this one, within
                 the last TOGETHER_FRESH; together_s: that headway
  trip           why a bus has no next stop: "starting" (not under way yet: laying over or
                 just pulled out) or "finishing" (only its last stop left); None otherwise
  behind_vid     the nearest bus behind (same route and direction, any pattern, laying over at
                 a start too) that will stop at this bus's next stop; behind_stops: how many of
                 this bus's stops it is back (0: heading for the same stop). Positions only, no
                 forecast: minutes behind were a forecast, wrong by 5+ min 1 time in 10 (Sep 29).

The snapshot is derived and disposable (DDIA ch. 12: a cache of the log, rebuilt from the next
sweeps), so publishing is best-effort: a failed write is logged and never fails a sweep.
Schedule-relative judgments (a gap "twice the scheduled headway") are made by the site, which
joins this feed with the nightly schedule file; the poller only reports what it saw.
"""

import base64
import gzip
import json
import logging
import math
import os
import sys
from array import array
from bisect import bisect_left, bisect_right
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

log = logging.getLogger("ghost_ledger.live")
CHICAGO = ZoneInfo("America/Chicago")

# Longer than any realistic gap: Sep 28 morning, J14 riders past Lake Shore Drive waited 79+ min
# in a jam, and a 75-min window could only call that unknown. ~0.7 s per snapshot at 150.
TRAIL = timedelta(minutes=150)
# A bus this close to either end of its pattern is laying over at a terminal, not serving
# riders: it gets no gap and is nobody's leader (verified on #9, Sep 26: pairs at 0 ft and
# at the far end showed "bunched" while parked).
TERMINAL_FT = 1000
# A bus still repeating a report older than this is left off the map (a frozen dot misleads).
STALE = timedelta(minutes=3)
# A dark bus only casts doubt on a stop it could have reached: buses dark for a while moved
# at most ~1,700 ft/min in 95% of Sep 28's blackouts (~4,000 on Lake Shore Drive); 3,000 ft/min
# (34 mph) is a generous ceiling. And only for so long: long blackouts are mostly parked buses
# (Sep 28: 20+ min, 68% hadn't moved), and replaying the day, doubting for 5 minutes hid the
# fewest real long waits without adding false ones.
DARK_REACH_FT_PER_MIN = 3000
# ...and in practice about its own pace: twice its last speed, at least this (the median of
# buses that moved while dark, Sep 28: ~630 ft/min). Replayed on Sep 28 against the fixed
# ceiling: the same false flags, a quarter fewer missed long waits (81 vs 105).
DARK_REACH_FLOOR = 600
DARK_DOUBT = timedelta(minutes=5)
# A report this overdue (a sweep and a half) may hide a pass: a bus last seen just short of a
# stop has likely gone past it by now.
OVERDUE = timedelta(seconds=45)
# A trip is under way once the bus is this far past the lowest point it reported on the trip:
# a bus laying over creeps a few dozen feet at most, while a bus that left, even in downtown
# rush-hour traffic, covers more within a report or two (Sep 28 #146: 0 -> 523 ft in 9 s).
START_FT = 150
# A bus this close to a stop is at it (GPS and pole placement put a standing bus a few dozen
# feet either side)
DWELL_FT = 100
# A trip first seen this soon after a stop passed it (a stop spacing is ~660 ft)
FIRST_SEEN_FT = 500
# A report counts only if its GPS is within this of its pattern at its pdist. Otherwise the
# two disagree and neither can be trusted for passes: a bus assigned its next trip while still
# driving from the garage creeps along the pattern kilometres away (Sep 29 rush: 1.2% of
# mid-route reports, e.g. #66 8677 "at" 1,092 ft while 5 km off), or CTA sends a new GPS with
# the old pdist after a blackout (#3 8405: back downtown with 79th Street's pdist). Such a
# report still puts the bus on the map (where it is is not a claim) but makes no passes and no
# claims. Where the pattern's points are far apart (Lake Shore Drive: 16,000 ft without a
# waypoint) a straight line between them strays from the road, so the tolerance grows with
# the gap: OFF_ROUTE_SHARE of it.
OFF_ROUTE_M = 300
OFF_ROUTE_SHARE = 0.15
# "Together" is judged at the last stop a bus passed; an older pass says little about now.
TOGETHER_FRESH = timedelta(minutes=10)
# A long wait is claimed only this far past twice the schedule: pass times are known to tens of
# seconds (the GPS referee's passes vs ours, Sep 28-30: median 4 s, 99th percentile 54 s), and
# 566 of 680 wrong long waits were claims within 30 s of the line, flipping either way.
LONG_MARGIN_S = 60
# ...and "together" only this far inside a quarter of the schedule, for the same reason: on Sep 30
# most wrong together claims were pairs a few seconds over the line; 30 s keeps 81% of claims
# and leaves 0.27% more than 60 s over it (the GPS referee).
TOGETHER_MARGIN_S = 30
# Two reports further apart than this aren't interpolated (the bus dropped out of the feed in
# between), as in the nightly's int_stop_passes.
STEP = timedelta(minutes=5)
# A route the poller couldn't see for longer than this (every call covering it failed, or the
# poller was down) may have had buses pass stops unseen, and a bus that ends its trip in the
# dark never passes the stops it drove by. Sep 30: CTA's API was unreachable 12:41-13:01, and in
# the half hour after, about 30% of long waits claimed were wrong (#22 at Clark & Van Buren:
# "no bus since 12:35" at 13:05, but 1522, 1,142 ft short of it at 12:40:53, came back at 13:01
# on its return trip: it passed in the dark). So a claim whose time spans such a
# hole is "can't tell" until a bus passes the stop again. A sweep and a half: one slow or failed
# call (a 30-60 s gap) doesn't count.
HOLE = timedelta(seconds=90)
# A bus whose pdist falls this much between reports while its GPS really moves is running
# against its pattern: its direction label is wrong (J14, Sep 28 morning: CTA tagged buses with
# the opposite direction; 4037 drove north past 1100 S Columbus "southbound", so northbound
# riders there were told 2 hours since the last bus in a 6-minute rush). It makes no passes and
# no claims, and a stop of the route's other direction it drove past (within BACKWARD_NEAR_M of
# its path) can't tell a wait that spans it. Only those stops: buses driving back to start
# their next trip do this on dozens of routes a day, and doubting the whole direction hid ~370
# real long waits a day (Sep 30).
BACKWARD_FT = 300
BACKWARD_MOVED_M = 80
BACKWARD_NEAR_M = 150
# An untrusted report (off route, or running backwards) whose GPS is this close to a stop of
# its own pattern, away from its terminals, puts a bus at that stop: it counts as a pass then
# (#147 4398, Sep 30: its pdist lagged its GPS by a kilometre, and its GPS was at Michigan &
# Ontario at 14:52 while riders there were told nobody had come since 14:41). A pass, not
# "can't tell": that hid ~120 real long waits a day for a few wrong ones avoided (Sep 28).
AT_STOP_M = 50
# Short and never stale: the file changes every 30 s and readers time their fetch to it.
CACHE_CONTROL = "public, max-age=5"


class Schedule:
    """Scheduled departures at every stop, today and tomorrow (transform/live_schedule.py)."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self.dates = set(doc["dates"])
        self.built_at = doc.get("built_at")
        # 8-byte arrays: ~2.3M departures take ~18 MB (a list of ints would take ~80)
        self.times: dict[str, array[int]] = {}
        for key, packed in doc["stops"].items():
            deltas = array("i", base64.b64decode(packed))  # little-endian int32
            if sys.byteorder != "little":
                deltas.byteswap()
            times = array("q", deltas)
            times[0] += doc["base"]
            for i in range(1, len(times)):
                times[i] += times[i - 1]
            self.times[key] = times

    def covers(self, now: datetime) -> bool:
        return str(now.astimezone(CHICAGO).date()) in self.dates

    def judge(
        self, rt: str, stpid: str, now: datetime, last_pass: datetime | None
    ) -> tuple[int | None, int | None]:
        """(scheduled gap at the stop now, departures due there since last_pass)."""
        times = self.times.get(f"{rt}|{stpid}")
        if not times or not self.covers(now):
            return None, None
        t = now.timestamp()
        in_hour = bisect_left(times, t) - bisect_left(times, t - 3600)
        due = None
        if last_pass is not None:
            due = bisect_right(times, t) - bisect_right(times, last_pass.timestamp())
        return (round(3600 / in_hour) if in_hour else None), due


def schedule_version(target: str) -> str:
    """A cheap fingerprint of the schedule file (S3 ETag, or a local file's mtime), so the
    poller reloads it only when the nightly has written a new one."""
    if target.startswith("s3://"):
        import boto3

        bucket, key = target.removeprefix("s3://").split("/", 1)
        return boto3.client("s3").head_object(Bucket=bucket, Key=key)["ETag"]
    return str(Path(target).stat().st_mtime_ns)


def load_schedule(target: str) -> Schedule:
    """From s3://bucket/key or a local path; gzipped JSON."""
    if target.startswith("s3://"):
        import boto3

        bucket, key = target.removeprefix("s3://").split("/", 1)
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    else:
        body = Path(target).read_bytes()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return Schedule(json.loads(body))


class LiveBoard:
    def __init__(self) -> None:
        # vid -> [(observed_at, pid, pdist, trip)], oldest first; trip = (stsd, stst) or None
        self.trails: dict[str, deque[tuple[datetime, int, float, tuple | None]]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.lengths: dict[int, float] = {}  # pid -> pattern length (ft), from the poller
        self.dirs: dict[int, str] = {}  # pid -> rtdir ("Eastbound"), from the poller
        self.stops: dict[int, list[tuple[float, str]]] = {}  # pid -> [(feet, name)], sorted
        self.stop_ids: dict[int, list[tuple[float, str, str]]] = {}  # pid -> [(feet, stpid, name)]
        self.routes: dict[int, str] = {}  # pid -> rt, from the poller and the reports
        self.schedule: Schedule | None = None  # the nightly's live schedule, if loaded
        self.shapes: dict[int, list[tuple[float, float, float]]] = {}  # pid -> [(feet, lat, lon)]
        # When each route was last polled successfully, and the holes (HOLE) in between
        self.polled_at: dict[str, datetime] = {}
        self.holes: dict[str, list[tuple[datetime, datetime]]] = {}
        self.seeded_to: datetime | None = None  # the newest stored report after a restart
        self.stop_geo: dict[str, tuple[float, float]] = {}  # stpid -> (lat, lon)
        # stpid -> [(time, vid)]: untrusted reports with the bus at that stop (AT_STOP_M)
        self.untrusted_at: dict[str, list[tuple[datetime, str]]] = {}
        # rt -> [(from, to, rtdir, GPS from, GPS to)]: a bus of rt ran against its rtdir pattern
        self.backwards: dict[str, list[tuple[datetime, datetime, str, tuple, tuple]]] = {}

    def seed(self, rows) -> None:
        """Rebuild trails from stored positions (vid, observed_at, pid, pdist[, stsd, stst[, lat,
        lon]]), oldest first; reports off their route (OFF_ROUTE_M) are left out."""
        for vid, t, pid, pdist, *rest in rows:
            stsd, stst, lat, lon = (list(rest) + [None] * 4)[:4]
            self.seeded_to = t if self.seeded_to is None else max(self.seeded_to, t)
            if not self.on_route({"pid": pid, "pdist": pdist, "lat": lat, "lon": lon}):
                continue
            key = (stsd, stst) if None not in (stsd, stst) else None
            self.trails.setdefault(vid, deque()).append((t, int(pid), float(pdist), key))

    def update(
        self, positions: list[dict[str, Any]], now: datetime, polled: set[str] | None = None
    ) -> None:
        """Record this sweep's reports (parsed vehicles, repeats included) and forget the rest.
        polled: the routes whose call succeeded (None: all of them). A route not polled keeps
        its buses' last reports (a failed call isn't an empty route), and a route unseen for
        longer than HOLE is recorded as a hole."""
        if polled is not None:
            for rt in polled:
                prev = self.polled_at.get(rt, self.seeded_to)
                if prev is not None and now - prev > HOLE:
                    self.holes.setdefault(rt, []).append((prev, now))
                self.polled_at[rt] = now
            for rt, holes in list(self.holes.items()):
                self.holes[rt] = [h for h in holes if h[1] >= now - TRAIL]
        present = {}
        if polled is not None:
            present = {
                v: p for v, p in self.latest.items()
                if p.get("rt") not in polled and now - p["observed_at"] <= STALE
            }  # fmt: skip
        for p in positions:
            if not self.on_route(p):
                present[p["vid"]] = {**p, "off_route": True}  # on the map; no passes, no claims
                self.note_untrusted(p)
                continue
            if self.went_backwards(self.latest.get(p["vid"]), p):
                present[p["vid"]] = {**p, "off_route": True, "backwards": True}
                self.note_untrusted(p)
                continue
            present[p["vid"]] = p
            if p.get("pid") is None or p.get("pdist") is None:
                continue
            trail = self.trails.setdefault(p["vid"], deque())
            if p.get("rt"):
                self.routes.setdefault(int(p["pid"]), p["rt"])
            trip = (p["stsd"], p["stst"]) if p.get("stsd") and p.get("stst") is not None else None
            point = (p["observed_at"], int(p["pid"]), float(p["pdist"]), trip)
            if not trail or trail[-1][0] < point[0]:
                trail.append(point)
        self.latest = present
        cutoff = now - TRAIL
        for rt, spans in list(self.backwards.items()):
            self.backwards[rt] = [x for x in spans if x[1] >= cutoff]
        for sid, times in list(self.untrusted_at.items()):
            self.untrusted_at[sid] = [x for x in times if x[0] >= cutoff]
        for vid in list(self.trails):
            trail = self.trails[vid]
            while trail and trail[0][0] < cutoff:
                trail.popleft()
            if not trail:
                del self.trails[vid]

    def went_backwards(self, prev: dict[str, Any] | None, p: dict[str, Any]) -> bool:
        """Whether report p runs against its pattern since prev, the bus's previous report
        (BACKWARD_FT); if so, records it in self.backwards."""
        if not prev or prev.get("pid") != p.get("pid") or p["observed_at"] <= prev["observed_at"]:
            return False
        if prev.get("off_route") and not prev.get("backwards"):
            return False  # an untrusted pdist proves nothing (#96 8777, Sep 28: its first report)
        if None in (p.get("pdist"), prev.get("pdist"), p.get("lat"), prev.get("lat")):
            return False
        moved = math.hypot(
            (float(p["lat"]) - float(prev["lat"])) * 110_540,
            (float(p["lon"]) - float(prev["lon"])) * 82_990,
        )
        if float(p["pdist"]) >= float(prev["pdist"]) - BACKWARD_FT or moved <= BACKWARD_MOVED_M:
            return False
        rt, rtdir = self.routes.get(int(p["pid"])), self.dirs.get(int(p["pid"]))
        if rt is not None and rtdir is not None:
            gps = ((float(prev["lat"]), float(prev["lon"])), (float(p["lat"]), float(p["lon"])))
            self.backwards.setdefault(rt, []).append(
                (prev["observed_at"], p["observed_at"], rtdir, *gps)
            )
        return True

    def note_untrusted(self, p: dict[str, Any]) -> None:
        """Record an untrusted report whose GPS is at a stop of its own pattern (AT_STOP_M), away
        from its terminals (buses lay over off their pattern's line there)."""
        if None in (p.get("pid"), p.get("pdist"), p.get("lat"), p.get("lon")) or self.at_terminal(
            p
        ):
            return
        here = (float(p["lat"]), float(p["lon"]))
        for _, sid, _ in self.stop_ids.get(int(p["pid"]), ()):
            at = self.stop_geo.get(sid)
            if at is not None and near_segment(at, here, here) <= AT_STOP_M:
                self.untrusted_at.setdefault(sid, []).append((p["observed_at"], p["vid"]))

    def against(self, rt: str, rtdir: str, stpid: str, start: datetime, end: datetime) -> bool:
        """Whether a bus of rt ran against a pattern of another direction past stop stpid (of
        rtdir) between start and end: it may have served the stop unseen. True for a stop whose
        place is unknown."""
        spans = [x for x in self.backwards.get(rt, ()) if x[0] < end and x[1] > start]
        spans = [x for x in spans if x[2] != rtdir]
        if not spans:
            return False
        at = self.stop_geo.get(stpid)
        return at is None or any(near_segment(at, a, b) <= BACKWARD_NEAR_M for *_, a, b in spans)

    def blind(self, rt: str, start: datetime, end: datetime, now: datetime) -> bool:
        """Whether route rt went unseen for longer than HOLE at some time between start and end
        (a hole still open now counts from the last successful poll)."""
        holes = self.holes.get(rt, [])
        last = self.polled_at.get(rt)
        if last is not None and now - last > HOLE:
            holes = [*holes, (last, now)]
        return any(a < end and b > start for a, b in holes)

    def on_route(self, p: dict[str, Any]) -> bool:
        """Whether a report's GPS is within OFF_ROUTE_M (or OFF_ROUTE_SHARE of the gap between
        the pattern's points there) of its pattern at its pdist; True with nothing to check."""
        pid, d, lat, lon = p.get("pid"), p.get("pdist"), p.get("lat"), p.get("lon")
        shape = self.shapes.get(int(pid)) if pid is not None else None
        if not shape or None in (d, lat, lon):
            return True
        d = float(d)
        i = bisect_left(shape, (d,))
        a, b = shape[max(0, i - 1)], shape[min(i, len(shape) - 1)]
        f = (d - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
        f = min(1.0, max(0.0, f))
        dy = (float(lat) - (a[1] + f * (b[1] - a[1]))) * 110_540
        dx = (float(lon) - (a[2] + f * (b[2] - a[2]))) * 82_990
        allowed = max(OFF_ROUTE_M, OFF_ROUTE_SHARE * (b[0] - a[0]) * 0.3048)
        return (dx * dx + dy * dy) ** 0.5 <= allowed

    def position_at(
        self, vid: str, pid: int, when: datetime, since: datetime | None = None
    ) -> float | None:
        """Where bus `vid` was on pattern `pid` at its last report at or before `when` (and not
        before `since`)."""
        d = None
        for t, p, dist, _ in self.trails.get(vid, ()):
            if since is not None and t < since:
                continue
            if t > when:
                break
            d = dist if p == pid else None
        return d

    def passes(self) -> dict[tuple[str, str, str], list[tuple[datetime, str]]]:
        """Every serving stop pass in the trails: (rt, rtdir, stpid) -> [(passed_at, vid)]."""
        return self.pass_log()[0]

    def pass_log(
        self,
    ) -> tuple[
        dict[tuple[str, str, str], list[tuple[datetime, str]]], dict[str, dict[str, datetime]]
    ]:
        """(every serving pass by (rt, rtdir, stpid), each bus's passes on its current run by
        stpid). Serving: a stop on the bus's pattern other than its last. Derived from the
        trails on each snapshot, so it is the same after a restart, eviction or a pattern that
        arrives late (no second copy to keep in step)."""
        out: dict[tuple[str, str, str], list[tuple[datetime, str]]] = {}
        current: dict[str, dict[str, datetime]] = {}
        for vid, trail in self.trails.items():
            rs = runs(trail)
            for n, (pid, points) in enumerate(rs):
                stops, rt, rtdir = self.stop_ids.get(pid), self.routes.get(pid), self.dirs.get(pid)
                if not stops or rt is None or rtdir is None:
                    continue
                for passed_at, stpid in trip_passes(points, stops[:-1]):
                    out.setdefault((rt, rtdir, stpid), []).append((passed_at, vid))
                    if n == len(rs) - 1:
                        mine = current.setdefault(vid, {})
                        mine[stpid] = max(passed_at, mine.get(stpid, passed_at))
        return out, current

    def under_way(self, vid: str) -> bool:
        """Whether the bus's current run has left its start (departed_at)."""
        trail = self.trails.get(vid)
        if not trail:
            return True
        return departed_at(runs(trail)[-1][1]) is not None

    def dark_buses(
        self, now: datetime
    ) -> dict[tuple[str, str], list[tuple[str, datetime, int, float, float]]]:
        """Buses whose report is overdue (OVERDUE) that had got under way, by (rt, rtdir) of
        their last report: [(vid, last report time, pid, pdist, reach in ft/min)]. The reach is
        how fast it could plausibly have gone on since: twice its last speed, at least
        DARK_REACH_FLOOR, at most DARK_REACH_FT_PER_MIN. Built once per snapshot."""
        out: dict[tuple[str, str], list[tuple[str, datetime, int, float, float]]] = {}
        for vid, trail in self.trails.items():
            t, pid, d, trip = trail[-1]
            if now - t < OVERDUE or d < TERMINAL_FT or not self.under_way(vid):
                continue  # reporting, never left its first stop, or still at its start
            key = (self.routes.get(pid), self.dirs.get(pid))
            if None in key:
                continue
            speed = 0.0
            if len(trail) > 1:
                t0, p0, d0, k0 = trail[-2]
                if (p0, k0) == (pid, trip) and t > t0:
                    speed = max(0.0, (d - d0) / ((t - t0).total_seconds() / 60))
            reach = min(DARK_REACH_FT_PER_MIN, max(2 * speed, DARK_REACH_FLOOR))
            out.setdefault(key, []).append((vid, t, pid, d, reach))
        return out

    def stop_gap(
        self,
        vid: str,
        pid: int,
        pdist: float,
        index: dict[tuple[str, str, str], list[tuple[datetime, str]]],
        dark: dict[tuple[str, str], list[tuple[str, datetime, int, float, float]]],
        stop_at: dict[int, dict[str, list[float]]],
        now: datetime,
    ) -> tuple[int | None, str | None, tuple[str, str] | None]:
        """(seconds since any bus of the route and direction passed this bus's next stop, the bus
        that did, (next stpid, name)). dark: dark_buses(); stop_at: pid -> stpid -> [feet
        along], for finding the stop on another bus's pattern."""
        stops, rt, rtdir = self.stop_ids.get(pid), self.routes.get(pid), self.dirs.get(pid)
        if not stops or rt is None or rtdir is None:
            return None, None, None
        i = bisect_left(stops, (pdist,))
        if i >= len(stops) - 1:
            return None, None, None  # only its last stop left: finishing, serves no one
        at, stpid, name = stops[i]
        # Its own passes count from earlier trips: on a loop served by one bus, the last bus at
        # the stop is this one, a lap ago (#171 Sep 28: 8935 passed Hyde Park & 55th every 30
        # min, and riders were told 2.5 hours, since another bus). Not on this trip: standing
        # at the stop, it hasn't left it yet.
        trail = self.trails.get(vid)
        this_trip = runs(trail)[-1][1][0][0] if trail else None
        last, last_by = None, None
        for t, other in [*index.get((rt, rtdir, stpid), ()), *self.untrusted_at.get(stpid, ())]:
            mine = other == vid and (this_trip is None or t >= this_trip)
            if not mine and t <= now and (last is None or t > last):
                last, last_by = t, other
        # A bus that went dark short of the stop, after the last pass and nearer the stop than
        # this bus was then, may have passed since: claim no wait.
        for other, t_dark, p_dark, d_dark, rate in dark.get((rt, rtdir), ()):
            if other == vid or (last is not None and t_dark <= last) or now - t_dark > DARK_DOUBT:
                continue
            ahead = [a for a in stop_at.get(p_dark, {}).get(stpid, ()) if a >= d_dark]
            if not ahead:
                continue  # the stop isn't ahead of it on its pattern (or it went past)
            reach = rate * (now - t_dark).total_seconds() / 60
            if min(ahead) - d_dark > reach:
                continue  # too far to have got there yet
            mine = self.position_at(vid, pid, t_dark)
            if mine is None or at - mine >= min(ahead) - d_dark:
                return None, None, (stpid, name)
        if (
            last is None
            or self.blind(rt, last, now, now)
            or self.against(rt, rtdir, stpid, last, now)
        ):
            # never seen, the route went unseen since (HOLE), or a bus of the route ran against
            # its pattern since (BACKWARD_FT): one may have passed here unseen
            return None, None, (stpid, name)
        return max(0, round((now - last).total_seconds())), last_by, (stpid, name)

    def trip_state(self, p: dict[str, Any]) -> str | None:
        """ "starting" or "finishing" for a bus with no next stop (see the module docstring)."""
        if p.get("pid") is None or p.get("pdist") is None:
            return None
        stops = self.stop_ids.get(int(p["pid"]))
        if not stops:
            return None
        if not self.under_way(p["vid"]):
            return "starting"
        if bisect_left(stops, (float(p["pdist"]),)) >= len(stops) - 1:
            return "finishing"
        return None

    def heading_for(self, vid: str, pid: int, pdist: float) -> tuple[str, float] | None:
        """The next stop a bus will serve and how far it is from it (feet): for a bus still at
        its start, the first stop from where it has been waiting; None when only its last stop
        is left."""
        stops = self.stop_ids.get(pid)
        if not stops:
            return None
        d = pdist
        if not self.under_way(vid):
            trail = self.trails.get(vid)
            if trail:
                d = min(x for _, x in runs(trail)[-1][1])
        i = bisect_left(stops, (d - 1,))
        return None if i >= len(stops) - 1 else (stops[i][1], stops[i][0] - pdist)

    def behind(
        self, fresh: list[dict[str, Any]], stop_gaps: dict[str, tuple[Any, Any, Any]]
    ) -> dict[str, tuple[str, int]]:
        """vid -> (the bus behind, stops back): see the module docstring (behind_vid)."""
        serves: dict[int, dict[str, int]] = {}  # pid -> stpid -> first index (not its last stop)
        for pid, stops in self.stop_ids.items():
            serves[pid] = {}
            for i, (_, sid, _) in enumerate(stops[:-1]):
                serves[pid].setdefault(sid, i)
        # (rt, dir) -> [(vid, pid, the stop it's heading for, feet to go)]
        lines: dict[tuple[str, str], list[tuple[str, int, str, float]]] = {}
        for p in fresh:
            if p.get("pid") is None or p.get("pdist") is None:
                continue
            pid = int(p["pid"])
            rt, rtdir = self.routes.get(pid), self.dirs.get(pid)
            heading = self.heading_for(p["vid"], pid, float(p["pdist"]))
            if rt is not None and rtdir is not None and heading is not None:
                lines.setdefault((rt, rtdir), []).append((p["vid"], pid, *heading))
        out: dict[str, tuple[str, int]] = {}
        for buses in lines.values():
            for vid_a, pid_a, _, to_go_a in buses:
                _, _, nxt = stop_gaps.get(vid_a, (None, None, None))
                mine = serves.get(pid_a, {})
                if nxt is None or nxt[0] not in mine:
                    continue
                k_a, best = mine[nxt[0]], None
                for vid_b, pid_b, s_b, to_go_b in buses:
                    if vid_b == vid_a or nxt[0] not in serves.get(pid_b, {}):
                        continue  # itself, or it won't stop at this bus's next stop
                    k_b = mine.get(s_b)
                    if k_b is None or k_b > k_a:
                        continue  # ahead of this bus, or not on its line yet (a branch)
                    if k_b == k_a and to_go_b <= to_go_a:
                        continue  # heading for the same stop but nearer to it: ahead (#147 Sep 29)
                    if best is None or (k_b, -to_go_b) > (best[1], -best[2]):
                        best = (vid_b, k_b, to_go_b)
                if best is not None:
                    out[vid_a] = (best[0], k_a - best[1])
        return out

    def together(
        self,
        fresh: list[dict[str, Any]],
        index: dict[tuple[str, str, str], list[tuple[datetime, str]]],
        current: dict[str, dict[str, datetime]],
        terminal: set[str],
        now: datetime,
    ) -> dict[str, tuple[str, int]]:
        """vid -> (the bus just ahead, headway s) for buses together: see the module docstring.
        The bus ahead must still be on the road with it: in the feed, on the same route and
        direction, not laying over (#66 Sep 29: 8174 was just ahead at the last stop, then
        reached Austin and turned around)."""
        out: dict[str, tuple[str, int]] = {}
        line: dict[str, tuple[str | None, str | None]] = {}
        for p in fresh:
            if p.get("pid") is not None and p["vid"] not in terminal:
                line[p["vid"]] = (self.routes.get(int(p["pid"])), self.dirs.get(int(p["pid"])))
        for p in fresh:
            if p.get("pid") is None or self.schedule is None:
                continue
            pid = int(p["pid"])
            rt, rtdir = self.routes.get(pid), self.dirs.get(pid)
            mine = current.get(p["vid"])
            if not mine or rt is None or rtdir is None:
                continue
            sid, t_b = max(mine.items(), key=lambda kv: kv[1])
            if now - t_b > TOGETHER_FRESH:
                continue
            passes = index.get((rt, rtdir, sid), ())
            before = [(t, v) for t, v in passes if t < t_b and v != p["vid"]]
            if not before:
                continue
            t_f, front = max(before)
            if any(
                (trail := self.trails.get(v)) and t >= trail[-1][0]
                for v, t in ((p["vid"], t_b), (front, t_f))
            ):
                # One of them hasn't left that stop (a standing bus's pass is its latest report
                # there), so there's no headway yet: X49 8077 stood 49 min at one stop, Sep 30,
                # and was "together" with each bus that went by.
                continue
            if self.blind(rt, t_f - STEP, t_b + STEP, now) or self.against(
                rt, rtdir, sid, t_f - STEP, t_b + STEP
            ):
                continue  # a pass timed across a hole (HOLE) is only a bound
            sched, _ = self.schedule.judge(rt, sid, t_b, None)
            headway = (t_b - t_f).total_seconds()
            if (
                sched
                and headway < 0.25 * sched - TOGETHER_MARGIN_S
                and line.get(front) == (rt, rtdir)
            ):
                out[p["vid"]] = (front, round(headway))
        return out

    def near(self, pid: int | None, pdist: float | None) -> str | None:
        """The name of the stop on pattern `pid` nearest to `pdist` feet along it."""
        stops = self.stops.get(pid) if pid is not None else None
        if not stops or pdist is None:
            return None
        i = bisect_left(stops, (pdist, ""))
        candidates = stops[max(0, i - 1) : i + 1]
        return min(candidates, key=lambda s: abs(s[0] - pdist))[1]

    def at_terminal(self, p: dict[str, Any]) -> bool:
        length = self.lengths.get(int(p["pid"]))
        d = float(p["pdist"])
        return d < TERMINAL_FT or (length is not None and d > length - TERMINAL_FT)

    def snapshot(self, now: datetime) -> dict[str, Any]:
        by_pid: dict[int, list[dict[str, Any]]] = {}
        terminal = set()
        for p in self.latest.values():
            if p.get("pid") is None or p.get("pdist") is None:
                continue
            if self.at_terminal(p):
                terminal.add(p["vid"])
            if p.get("off_route"):
                continue  # on the map, but no claims
            if p["vid"] not in terminal:
                by_pid.setdefault(int(p["pid"]), []).append(p)
        index, current = self.pass_log()
        dark = self.dark_buses(now)
        stop_at: dict[int, dict[str, list[float]]] = {}
        for p_, stops in self.stop_ids.items():
            for feet, sid, _ in stops:
                stop_at.setdefault(p_, {}).setdefault(sid, []).append(feet)
        stop_gaps = {
            bus["vid"]: self.stop_gap(
                bus["vid"], pid, float(bus["pdist"]), index, dark, stop_at, now
            )
            for pid, buses in by_pid.items()
            for bus in buses
            if self.under_way(bus["vid"])  # still at its start: not waited on by anyone yet
        }

        fresh = [p for p in self.latest.values() if now - p["observed_at"] <= STALE]
        on_route = [p for p in fresh if not p.get("off_route")]
        behind = self.behind(on_route, stop_gaps)
        together = self.together(on_route, index, current, terminal, now)

        rows = []
        for p in sorted(fresh, key=lambda p: (p["rt"], p["vid"])):
            stop_gap, last_by, next_stop = stop_gaps.get(p["vid"], (None, None, None))
            sched_s = sched_due = None
            if self.schedule is not None and next_stop is not None:
                last_pass = None if stop_gap is None else now - timedelta(seconds=stop_gap)
                sched_s, sched_due = self.schedule.judge(p["rt"], next_stop[0], now, last_pass)
            pid = None if p.get("pid") is None else int(p["pid"])
            rows.append({
                "vid": p["vid"], "rt": p["rt"], "pid": pid, "hdg": p.get("hdg"),
                "lat": None if p.get("lat") is None else round(float(p["lat"]), 5),
                "lon": None if p.get("lon") is None else round(float(p["lon"]), 5),
                "dly": bool(p.get("dly")), "des": p.get("des"), "dir": self.dirs.get(pid),
                "t": int(p["observed_at"].timestamp()),
                "pdist": None if p.get("pdist") is None else round(float(p["pdist"])),
                "at_terminal": p["vid"] in terminal,
                "near": self.near(pid, p.get("pdist")),
                "stop_gap_s": stop_gap, "last_by": last_by,
                "next_stpid": next_stop[0] if next_stop else None,
                "next_stop": next_stop[1] if next_stop else None,
                "sched_s": sched_s, "sched_due": sched_due,
                "long_wait": bool(stop_gap is not None and sched_s
                                  and stop_gap > 2 * sched_s + LONG_MARGIN_S
                                  and (sched_due or 0) >= 2),
                "together_with": together.get(p["vid"], (None, None))[0],
                "together_s": together.get(p["vid"], (None, None))[1],
                "behind_vid": behind.get(p["vid"], (None, None))[0],
                "behind_stops": behind.get(p["vid"], (None, None))[1],
                "trip": None if next_stop or p.get("off_route") else self.trip_state(p),
            })  # fmt: skip
        # No route polled successfully for longer than HOLE: CTA's feed is down (or unreachable),
        # which the site must say, rather than "0 buses on the road" (Sep 30, 12:41-13:01)
        last_poll = max(self.polled_at.values(), default=None)
        down = last_poll if last_poll is not None and now - last_poll > HOLE else None
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "build": os.environ.get("GIT_SHA", "unknown"),
            "no_data_since": down.isoformat(timespec="seconds") if down else None,
            "vehicles": rows,
        }


def runs(trail) -> list[tuple[int, list[tuple[datetime, float]]]]:
    """A bus's trail cut into runs: continuous travel along one pattern, [(pid, [(t, pdist)])].
    A new run starts when the pattern changes, or when the bus is back more than TERMINAL_FT
    on a new trip (it started over, at a terminal). A trip change alone doesn't: CTA reassigns
    a moving bus (#66, Sep 28: 8125 switched trips while driving out from the terminal, and
    the stops it passed in between were lost). Nor does a jump back alone: GPS glitches."""
    out: list[tuple[int, list[tuple[datetime, float]]]] = []
    prev = None
    for t, pid, d, trip in trail:
        if prev is None or pid != prev[0] or (d < prev[1] - TERMINAL_FT and trip != prev[2]):
            out.append((pid, []))
        out[-1][1].append((t, d))
        prev = (pid, d, trip)
    return out


def departed_at(points: list[tuple[datetime, float]]) -> datetime | None:
    """When a trip got under way: its first report TERMINAL_FT past the lowest point it has
    reported so far, or None while it is still at its start. Measured from the trip's own
    start, not the pattern's: #146 buses lay over 2,500 ft into their pattern (Sep 28), and a
    new trip's first reports can jump ahead and back before the bus settles."""
    lowest = float("inf")
    for t, d in points:
        lowest = min(lowest, d)
        if d >= lowest + START_FT:
            return t
    return None


def trip_passes(
    points: list[tuple[datetime, float]], stops: list[tuple[float, str, str]]
) -> list[tuple[datetime, str]]:
    """When one trip passed each stop: [(passed_at, stpid)]. points: its reports (time, pdist),
    oldest first; stops: its pattern's [(feet, stpid, name)], sorted.

    Before the trip gets under way (departed_at), a bus laying over creeps back and forth: each
    stop it crosses keeps its last crossing, which is when the bus left it, and none count
    until it has left. From then on (int_stop_passes has the same rules), a step forward passes
    the stops between the furthest point reached so far (counting from departure) and its end,
    inclusive and interpolated along the step; each stop keeps its earliest pass. So a report
    that jumps back (to 0 at a terminal, or GPS noise) neither passes stops twice nor, once
    the bus is past its furthest point again, loses the stops beyond it. Across a blackout
    (reports more than STEP apart) the time in between is unknown, so
    those stops are passed at the later report: the latest the bus can have passed them. That
    can only understate a wait, never inflate it (Sep 28: J14 buses go dark for 20+ minutes on
    Lake Shore Drive, and the stops after it would otherwise never be passed)."""
    left = departed_at(points)
    if left is None:
        return []
    # The bus sits at its start until the report before the one that shows it gone.
    waited = max((t for t, _ in points if t < left), default=left)
    starting: dict[int, datetime] = {}
    running: dict[int, datetime] = {}
    furthest = float("-inf")
    for (t0, d0), (t1, d1) in zip(points, points[1:], strict=False):
        before = t1 <= left
        lo = d0
        if not before:
            furthest = max(furthest, d0)
            lo = furthest  # only new ground: a jump back and forth passes nothing twice
        if d1 <= lo or d1 <= d0:
            continue
        blackout = t1 - t0 > STEP
        for k in range(bisect_left(stops, (lo,)), len(stops)):
            at = stops[k][0]
            if at > d1:
                break
            when = t1 if blackout else t0 + (t1 - t0) * ((at - d0) / (d1 - d0))
            if before:
                starting[k] = max(when, waited)  # still there until it left
            elif k not in running and k not in starting:
                running[k] = when
    # First seen just past a stop: the bus drove past it (or pulled out there) just before its
    # first report, so it passed no later than that (Sep 28, #3 at King & 83rd: a trip first
    # seen 118 ft past the stop; without this riders there were told 45 min, not 24).
    t_first, d_first = points[0]
    k = bisect_left(stops, (d_first,)) - 1
    if k >= 0 and d_first - stops[k][0] <= FIRST_SEEN_FT and k not in running and k not in starting:
        running[k] = t_first
    passes = {**running, **starting}
    # A bus that waits just past a stop (within DWELL_FT, the dwell rule's "at the stop") is at
    # it, and serves it when it leaves (#201 Sep 29: 8800 began its trip 1,056 ft in, eased
    # back to 51 ft past Howard & Custer and left from there; by pdist it was never short of the
    # stop, and riders there were told 65 min since the last bus).
    lowest = min(d for t, d in points if t <= left)
    for k, (at, _, _) in enumerate(stops):
        if lowest - DWELL_FT <= at <= lowest and k not in passes:
            passes[k] = waited
    # A bus standing at a stop (within DWELL_FT either side) serves it until it leaves: the
    # pass is its last report there before it moves on (Sep 28: #79 stood at Spaulding 11
    # minutes while buses behind were told the last bus came 16-26 minutes ago).
    # Standing means two reports or more there in a row: a moving bus whose next report just
    # happens to land near the stop isn't held there.
    times = [t for t, _ in points]
    for k, when in passes.items():
        at = stops[k][0]
        there = []
        for t, d in points[bisect_right(times, when) :]:
            if d > at + DWELL_FT or d < at - DWELL_FT:
                break
            there.append(t)
        if len(there) >= 2:
            passes[k] = there[-1]
    # Stops closer together than DWELL_FT: a later stop is never passed before an earlier one
    later = None
    for k in sorted(passes, reverse=True):
        if later is not None and passes[k] > later:
            passes[k] = later
        later = passes[k]
    return [(when, stops[k][1]) for k, when in sorted(passes.items())]


def pattern_shape(points: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    """[(feet along, lat, lon)] for a Bus Tracker pattern: its stops and its first point, the
    points whose pdist is real. CTA sends every waypoint with pdist 0 (all 115,965 of them,
    Sep 30), so kept, they piled up at the start and a bus starting its trip was checked against
    whichever of them sorted last: #147 4155, laying over 258 ft into its trip downtown, was
    "off route" (against a waypoint miles north) and its trip never passed Congress Plaza &
    Michigan; 4.55% of Sep 30's reports were rejected so. Past the first stop this is the shape
    the on-route rule was measured with (the waypoints all sat at 0)."""
    ordered = sorted(points or [], key=lambda p: int(p.get("seq") or 0))
    return sorted(
        (float(p["pdist"]), float(p["lat"]), float(p["lon"]))
        for i, p in enumerate(ordered)
        if (p.get("typ") == "S" or i == 0)
        and p.get("pdist") is not None
        and p.get("lat") is not None
        and p.get("lon") is not None
    )


def near_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Metres from point p to the segment a-b (lat, lon pairs; flat earth at city scale)."""
    ax, ay = (a[1] - p[1]) * 82_990, (a[0] - p[0]) * 110_540
    bx, by = (b[1] - p[1]) * 82_990, (b[0] - p[0]) * 110_540
    dx, dy = bx - ax, by - ay
    k = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
    return math.hypot(ax + k * dx, ay + k * dy)


def pattern_stop_geo(points: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    """stpid -> (lat, lon) for a Bus Tracker pattern's stops."""
    return {
        str(p["stpid"]): (float(p["lat"]), float(p["lon"]))
        for p in points or []
        if p.get("typ") == "S" and p.get("stpid") and p.get("lat") is not None
    }


def pattern_stop_ids(points: list[dict[str, Any]]) -> list[tuple[float, str, str]]:
    """[(feet along, stpid, name)] for a Bus Tracker pattern's stops, sorted."""
    return sorted(
        (float(p["pdist"]), str(p["stpid"]), p.get("stpnm") or "")
        for p in points or []
        if p.get("typ") == "S" and p.get("pdist") is not None and p.get("stpid")
    )


def pattern_stops(points: list[dict[str, Any]]) -> list[tuple[float, str]]:
    """[(feet along, stop name)] for a Bus Tracker pattern's points (stops only), sorted."""
    return sorted(
        (float(p["pdist"]), p["stpnm"])
        for p in points or []
        if p.get("typ") == "S" and p.get("pdist") is not None and p.get("stpnm")
    )


class Sink(Protocol):
    def write(self, snapshot: dict[str, Any]) -> None: ...


class FileSink:
    """Dev: write the snapshot where the site's dev server can read it."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def write(self, snapshot: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, separators=(",", ":")))
        tmp.replace(self.path)  # readers never see a half-written file


class S3Sink:
    """Prod: one object, gzip-encoded, short cache life (CloudFront serves it as is)."""

    def __init__(self, url: str) -> None:
        import boto3  # only needed in prod
        from botocore.config import Config

        self.bucket, self.key = url.removeprefix("s3://").split("/", 1)
        # Tight timeouts: a slow S3 must never hold up the next sweep (the feed is best-effort;
        # the next sweep rewrites the whole snapshot anyway).
        self.client = boto3.client(
            "s3",
            config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}),
        )

    def write(self, snapshot: dict[str, Any]) -> None:
        body = gzip.compress(json.dumps(snapshot, separators=(",", ":")).encode(), mtime=0)
        self.client.put_object(
            Bucket=self.bucket, Key=self.key, Body=body, ContentType="application/json",
            ContentEncoding="gzip", CacheControl=CACHE_CONTROL,
        )  # fmt: skip


def make_sink(target: str | None) -> Sink | None:
    if not target:
        return None
    return S3Sink(target) if target.startswith("s3://") else FileSink(target)


def publish(
    board: LiveBoard,
    sink: Sink | None,
    positions: list[dict],
    now: datetime,
    polled: set[str] | None = None,
) -> None:
    """Best-effort: the feed is a disposable view; never let it fail the sweep. polled: the
    routes whose call succeeded (LiveBoard.update)."""
    try:
        board.update(positions, now, polled)
        if sink is not None:
            sink.write(board.snapshot(now))
    except Exception:  # noqa: BLE001 - logged; the next sweep rewrites the whole snapshot
        log.exception("live feed not published this sweep")
