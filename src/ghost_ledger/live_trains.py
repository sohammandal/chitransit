"""The live train feed, rewritten after each committed train sweep (ingest/trains.py): CTA's own
data as CTA sent it, and our claims about gaps and bunching, decided here once as the buses'
are in live.py (the same rules; graded before the site shows them). Two files beside
vehicles.json:

  trains.json    {"generated_at": ISO, "build": commit, "data_at": epoch | None,
                  "trains": [{"rn", "rt", "dir", "dest", "next", "stop", "next_name", "arr",
                              "app", "dly", "lat", "lon", "hdg", "t", ...the claims below}],
                  "status": {rt: CTA's words for the line}}
                 every train on the eight lines; fetched by the site every sweep.
  stations.json  {"generated_at": ISO, "stations": {mapid: {"at": epoch, "eta": [{"rn", "rt",
                  "dest", "stop", "stop_desc", "arr", "t", "sch", "app", "dly", "flt"}]}}}
                 CTA's predictions at each station, as of that station's last arrivals call;
                 fetched only while a station is open.

Times are epoch seconds (UTC). rt is the line's Train Tracker code in both files (red, blue,
brn, g, org, p, pink, y). A train CTA can't place has lat/lon null (CTA sends "0"). stop is the
platform of its next station (stpId: one side of a station, so one direction).

Passes: a train passed a platform when CTA's next platform for it moves on (it left; known to
one snapshot, 20 s). Between two snapshots a train can pass a close station unseen (2% of moves,
Oct 11), so moves along a path are filled in from l_paths.json: up to MAX_SKIP stations over,
timed; further (a jump: 1.4% of moves were onto no path we know or far along it), its passes
are only bounds. A move onto no known path, or a train unseen for longer than HOLE, may have
passed any platform after its last one unseen: those platforms are in doubt until a train
passes them again. As for buses (live.py), measured at each train's next platform, all trains
of the line counting:
  gap_s          how long ago the last train passed it (a rider there has waited that long);
                 None ("can't tell") with no pass seen, a doubt since, or snapshots missing
                 (HOLE) since; last_pass: when (epoch); last_by: that train ("red:812")
  ahead_s        seconds between it and that train, by CTA's own times: when each is due at the
                 station the one ahead reaches next (both from CTA's last answer there); else
                 when this one is due at its next station after the one ahead left it. The
                 site's minutes between trains (so a line needs no stations.json)
  long_wait      gap_s over twice the scheduled gap there now (by LONG_MARGIN_S), with at least
                 2 departures due since the last pass (the nightly's live schedule, rail
                 platforms keyed by line code)
  together_with  the train just ahead at this train's last timed pass, when it passed that
                 platform under a quarter of the scheduled gap before (by TOGETHER_MARGIN_S),
                 within TOGETHER_FRESH, and is still on the line toward the same place;
                 together_s: that headway
"""

import json
import os
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any
from zoneinfo import ZoneInfo

from ghost_ledger.live import Schedule
from ghost_ledger.traintracker import as_list

CHICAGO = ZoneInfo("America/Chicago")
# Customer Alerts' ServiceId for each line -> its Train Tracker code
LINE_CODES = {"Red": "red", "Blue": "blue", "Brn": "brn", "G": "g", "Org": "org", "P": "p",
              "Pink": "pink", "Y": "y"}  # fmt: skip
# A prediction this far past its time is dropped from stations.json (CTA drops it on arrival).
STALE_ETA = timedelta(seconds=30)
# Positions further apart than this (a failed call, the poller down) may hide passes.
HOLE = timedelta(seconds=60)
# Stations a train can pass between two snapshots and still be timed (close stations downtown).
MAX_SKIP = 1
TRAIL = timedelta(minutes=150)  # passes kept
# The buses' margins (live.py): a pass is known to a snapshot, as a bus's to tens of seconds.
TOGETHER_FRESH = timedelta(minutes=10)
LONG_MARGIN_S = 60
TOGETHER_MARGIN_S = 30


Path = list[tuple[str, str]]  # (station, platform), in order


def load_paths() -> dict[str, list[Path]]:
    """Each line's paths as (station, platform) in order (l_paths.json, scripts/l_lines.py)."""
    doc = json.loads(resources.files("ghost_ledger").joinpath("l_paths.json").read_text())
    return {rt: [[(s, q) for s, q in path] for path in ps] for rt, ps in doc["paths"].items()}


def local_epoch(value: Any, fetched_at: datetime) -> int | None:
    """CTA's Chicago-local time with no offset ("2026-10-11T19:52:32", or "20261011 19:52:32"
    in XML) as epoch seconds. In the DST fall-back hour the same local time happens twice:
    take the one nearer the fetch (predictions are at most an hour ahead)."""
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y%m%d %H:%M:%S"):
        try:
            naive = datetime.strptime(text, fmt)  # noqa: DTZ007 - local time, resolved below
            break
        except ValueError:
            continue
    else:
        return None
    candidates = [naive.replace(tzinfo=CHICAGO, fold=f).astimezone(UTC) for f in (0, 1)]
    return int(min(candidates, key=lambda c: abs(c - fetched_at)).timestamp())


def flag(value: Any) -> bool:
    return str(value).strip() == "1"


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coordinate(value: Any) -> float | None:
    n = number(value)
    return None if n == 0 else n  # "0": CTA can't place the train


def trains_from(body: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    """Every train in a ttpositions reply."""
    out = []
    for route in as_list(body.get("route")):
        if not isinstance(route, dict):
            continue
        rt = str(route.get("@name") or "").lower()
        for t in as_list(route.get("train")):
            if not isinstance(t, dict):
                continue
            lat, lon, heading = (
                coordinate(t.get("lat")),
                coordinate(t.get("lon")),
                number(t.get("heading")),
            )
            placed = lat is not None and lon is not None
            out.append({
                "rn": str(t.get("rn") or ""), "rt": rt, "dir": str(t.get("trDr") or ""),
                "dest": t.get("destNm"), "next": str(t.get("nextStaId") or ""),
                "stop": str(t.get("nextStpId") or ""),
                "next_name": t.get("nextStaNm"), "arr": local_epoch(t.get("arrT"), fetched_at),
                "app": flag(t.get("isApp")), "dly": flag(t.get("isDly")),
                "lat": lat if placed else None, "lon": lon if placed else None,
                "hdg": int(heading) if (placed and heading is not None) else None,  # 0 = north
                "t": local_epoch(t.get("prdt"), fetched_at),
            })  # fmt: skip
    return out


def etas_from(body: dict[str, Any], fetched_at: datetime) -> dict[str, list[dict[str, Any]]]:
    """A ttarrivals reply's predictions, by station."""
    out: dict[str, list[dict[str, Any]]] = {}
    for e in as_list(body.get("eta")):
        if not isinstance(e, dict) or not e.get("staId"):
            continue
        rt = str(e.get("rt") or "")
        out.setdefault(str(e["staId"]), []).append({
            "rn": str(e.get("rn") or ""), "rt": LINE_CODES.get(rt, rt.lower()),
            "dest": e.get("destNm"), "stop": str(e.get("stpId") or ""),
            "stop_desc": e.get("stpDe"), "arr": local_epoch(e.get("arrT"), fetched_at),
            "t": local_epoch(e.get("prdt"), fetched_at), "sch": flag(e.get("isSch")),
            "app": flag(e.get("isApp")), "dly": flag(e.get("isDly")), "flt": flag(e.get("isFlt")),
        })  # fmt: skip
    return out


def status_from(body: dict[str, Any]) -> dict[str, str]:
    """CTA's status for each line (routes.aspx?type=rail)."""
    out = {}
    for r in as_list(body.get("RouteInfo")):
        if isinstance(r, dict) and str(r.get("ServiceId")) in LINE_CODES:
            out[LINE_CODES[str(r["ServiceId"])]] = str(r.get("RouteStatus") or "")
    return out


class LiveTrains:
    """What the feed shows: the latest trains, each station's latest predictions (its own
    arrivals call: one round covers every station in ~30 s by day) and the lines' status; and
    the passes the claims are measured from."""

    def __init__(
        self,
        schedule: Callable[[], Schedule | None] = lambda: None,
        paths: dict[str, list[Path]] | None = None,
        publish_claims: bool = False,
    ) -> None:
        # Our claims go out only once graded (300 per claim type, < 1% wrong, as for buses):
        # until then long_wait is False and together_with None; the measurements (gap_s,
        # ahead_s) go out regardless.
        self.publish_claims = publish_claims
        self.trains: list[dict[str, Any]] = []
        self.trains_ok = False  # this sweep's positions call worked: trains.json is due
        self.stations: dict[str, dict[str, Any]] = {}
        self.status: dict[str, str] = {}
        self.schedule = schedule  # the bus poller's live schedule (it carries rail platforms)
        # (line, platform) -> each path through it: what comes after it, and before it (nearest
        # first)
        self.ahead: dict[tuple[str, str], list[Path]] = {}
        self.behind: dict[tuple[str, str], list[Path]] = {}
        for rt, ps in (load_paths() if paths is None else paths).items():
            for path in ps:
                for i, (_, platform) in enumerate(path):
                    self.ahead.setdefault((rt, platform), []).append(path[i + 1 :])
                    self.behind.setdefault((rt, platform), []).append(path[:i][::-1])
        self.where: dict[str, tuple[str, str, int]] = {}  # train -> (next platform, station, seen)
        self.passes: dict[tuple[str, str], deque[tuple[int, str, bool]]] = {}  # (t, train, bound)
        self.last_pass: dict[str, tuple[str, int, bool]] = {}  # train -> (platform, t, bound)
        self.doubt: dict[tuple[str, str], int] = {}  # a train may have passed it unseen by then
        self.blind: list[tuple[int, int]] = []  # snapshots missing between these times
        self.seen_at: int | None = None  # the last positions snapshot

    def update(self, calls: list[Any]) -> None:
        """Take in a committed sweep's calls (ingest.poller.Call)."""
        self.trains_ok = False
        for call in calls:
            r = call.result
            if not r.ok:
                continue
            if call.endpoint == "ttpositions":
                self.trains = trains_from(r.body, call.fetched_at)
                self.trains_ok = True
                self.passed(int(call.fetched_at.timestamp()))
            elif call.endpoint == "ttarrivals":
                found = etas_from(r.body, call.fetched_at)
                at = int(call.fetched_at.timestamp())
                for mapid in call.params.get("mapid", "").split(","):
                    # every station asked for: one with no predictions now loses its old ones
                    self.stations[mapid] = {"at": at, "eta": found.get(mapid, [])}
            elif call.endpoint == "routes":
                self.status = status_from(r.body) or self.status

    def passed(self, t: int) -> None:
        """Note the platforms each train passed since the last snapshot (see the docstring)."""
        if self.seen_at is not None and t - self.seen_at > HOLE.total_seconds():
            self.blind.append((self.seen_at, t))
        self.seen_at = t
        for tr in self.trains:
            key, rt, station, platform = f"{tr['rt']}:{tr['rn']}", tr["rt"], tr["next"], tr["stop"]
            was = self.where.get(key)
            if not platform:
                continue
            if was is None or was[1] == station:
                # first seen, or the same station: on another track (Wilson's), the path's own
                # platform stays; on one of ours (it turned at a terminal), it's that one now
                keep = was is not None and (rt, platform) not in self.ahead
                self.where[key] = (was[0] if keep else platform, station, t)
                continue
            if t - was[2] > HOLE.total_seconds():
                self._doubt(rt, was[0], t)  # unseen a while: where it went is unknown
                self.where[key] = (platform, station, t)
                continue
            found = self._locate(rt, was[0], station, platform)
            if found is None:
                self._doubt(rt, was[0], t)  # onto no path we know
                self.where[key] = (platform, station, t)
                continue
            route, ours, forward = found
            self.where[key] = (ours, station, t)  # the path's own platform (Wilson's other track)
            if not forward:
                # back along its path (CTA's position had jumped ahead): the platforms it seemed to
                # pass may not have been passed
                for q in (ours, *route):
                    self.doubt[(rt, q)] = t
                continue
            bound = len(route) > MAX_SKIP
            for q in (was[0], *route):
                self.passes.setdefault((rt, q), deque()).append((t, key, bound))
            self.last_pass[key] = (route[-1] if route else was[0], t, bound)
        old = t - TRAIL.total_seconds()
        for q in self.passes.values():
            while q and q[0][0] < old:
                q.popleft()
        self.blind = [b for b in self.blind if b[1] >= old]
        for key in [k for k, (_, _, seen) in self.where.items() if seen < old]:
            self.where.pop(key, None)
            self.last_pass.pop(key, None)

    def _locate(
        self, rt: str, frm: str, station: str, platform: str
    ) -> tuple[list[str], str, bool] | None:
        """A move from platform frm to (station, platform), along a path through frm: (the
        platforms between, the path's own platform there, forward or back). A platform not on
        our paths at a station on them counts as that station's (CTA reports some trains on
        another track: Wilson's 30385). None: the move is on no path we know."""
        for way, forward in ((self.ahead, True), (self.behind, False)):
            for rest in way.get((rt, frm), []):
                for i, (s, q) in enumerate(rest):
                    if q == platform or s == station:
                        return [q2 for _, q2 in rest[:i]], q, forward
        return None

    def _doubt(self, rt: str, platform: str, t: int) -> None:
        for rest in self.ahead.get((rt, platform), [[]]):
            for q in (platform, *(q for _, q in rest)):
                self.doubt[(rt, q)] = t

    def _unseen(self, rt: str, platform: str, since: int, until: int) -> bool:
        """Could a train have passed this platform unseen between these times?"""
        return self.doubt.get((rt, platform), 0) > since or any(
            a < until and b > since for a, b in self.blind
        )

    def claims(self, now: datetime) -> None:
        """Each train's gap and claims (see the docstring), on the trains as published."""
        schedule = self.schedule()
        t_now = int(now.timestamp())
        on_line = {f"{t['rt']}:{t['rn']}": (t["rt"], t["dest"]) for t in self.trains}
        trains = {f"{t['rt']}:{t['rn']}": t for t in self.trains}
        for tr in self.trains:
            key, rt = f"{tr['rt']}:{tr['rn']}", tr["rt"]
            platform = self.where.get(key, (tr["stop"],))[0]  # the path's own (another track's)
            last = self.passes.get((rt, platform))
            gap = last_t = last_by = None
            if last and not self._unseen(rt, platform, last[-1][0], t_now):
                last_t, last_by, _ = last[-1]
                gap = t_now - last_t
            sched = due = None
            if schedule is not None and gap is not None:
                sched, due = schedule.judge(rt, platform, now, datetime.fromtimestamp(last_t, UTC))
            tr["gap_s"], tr["last_pass"], tr["last_by"] = gap, last_t, last_by
            tr["ahead_s"] = self.ahead_s(tr, trains.get(last_by) if last_by else None, last_t)
            long = bool(
                sched and gap is not None and gap > 2 * sched + LONG_MARGIN_S and (due or 0) >= 2
            )
            tr["long_wait"] = self.publish_claims and long
            tr["together_with"] = tr["together_s"] = None
            if not self.publish_claims:
                continue
            mine = self.last_pass.get(key)  # its last timed pass, if fresh
            if not mine or mine[2] or t_now - mine[1] > TOGETHER_FRESH.total_seconds():
                continue
            if schedule is None:
                continue
            q, t_b, _ = mine
            before = [p for p in self.passes.get((rt, q), ()) if p[0] < t_b and p[1] != key]
            if not before:
                continue
            t_a, front, bound = before[-1]
            if bound or self._unseen(rt, q, t_a, t_b) or on_line.get(front) != (rt, tr["dest"]):
                continue
            sched_b, _ = schedule.judge(rt, q, datetime.fromtimestamp(t_b, UTC), None)
            if sched_b and t_b - t_a < 0.25 * sched_b - TOGETHER_MARGIN_S:
                tr["together_with"], tr["together_s"] = front, t_b - t_a

    def ahead_s(
        self, tr: dict[str, Any], ahead: dict[str, Any] | None, left: int | None
    ) -> int | None:
        """Seconds between a train and the one ahead of it (see ahead_s in the docstring)."""
        if ahead is not None and ahead["rt"] == tr["rt"]:
            due = self.stations.get(ahead.get("next", ""), {}).get("eta", [])

            def when(t: dict[str, Any]) -> int | None:
                return next((e["arr"] for e in due if e["rn"] == t["rn"] and e["rt"] == t["rt"]
                             and not e["sch"] and e["arr"]), None)  # fmt: skip

            a, b = when(ahead) or ahead.get("arr"), when(tr)
            if a and b and b > a:
                return b - a
        if left is not None and tr.get("arr") and tr["arr"] > left:
            return tr["arr"] - left
        return None

    def documents(self, now: datetime) -> list[tuple[str, dict[str, Any]]]:
        """The files due: trains.json only after a sweep whose positions call worked (an old
        list must not look fresh), stations.json every sweep."""
        generated = now.astimezone(UTC).isoformat(timespec="seconds")
        cutoff = (now - STALE_ETA).timestamp()
        stations = {
            mapid: {**s, "eta": [e for e in s["eta"] if e["arr"] is None or e["arr"] >= cutoff]}
            for mapid, s in self.stations.items()
        }
        docs: list[tuple[str, dict[str, Any]]] = []
        if self.trains_ok:
            self.claims(now)
            stamps = [t["t"] for t in self.trains if t["t"] is not None]
            docs.append(("trains.json", {
                "generated_at": generated, "build": os.environ.get("GIT_SHA", "unknown"),
                "data_at": max(stamps, default=None), "trains": self.trains, "status": self.status,
            }))  # fmt: skip
        docs.append(("stations.json", {"generated_at": generated, "stations": stations}))
        return docs
