"""The live train feed, rewritten after each committed train sweep (ingest/trains.py). Display
only: CTA's own data, as CTA sent it, no claims of ours (those wait for grading, as for
buses). Two files beside vehicles.json:

  trains.json    {"generated_at": ISO, "build": commit, "data_at": epoch | None,
                  "trains": [{"rn", "rt", "dir", "dest", "next", "next_name", "arr", "app",
                              "dly", "lat", "lon", "hdg", "t"}],
                  "status": {rt: CTA's words for the line}}
                 every train on the eight lines; fetched by the site every sweep.
  stations.json  {"generated_at": ISO, "stations": {mapid: {"at": epoch, "eta": [{"rn", "rt",
                  "dest", "stop", "stop_desc", "arr", "t", "sch", "app", "dly", "flt"}]}}}
                 CTA's predictions at each station, as of that station's last arrivals call;
                 fetched only while a station is open.

Times are epoch seconds (UTC). rt is the line's Train Tracker code in both files (red, blue,
brn, g, org, p, pink, y). A train CTA can't place has lat/lon null (CTA sends "0").
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ghost_ledger.traintracker import as_list

CHICAGO = ZoneInfo("America/Chicago")
# Customer Alerts' ServiceId for each line -> its Train Tracker code
LINE_CODES = {"Red": "red", "Blue": "blue", "Brn": "brn", "G": "g", "Org": "org", "P": "p",
              "Pink": "pink", "Y": "y"}  # fmt: skip
# A prediction this far past its time is dropped from stations.json (CTA drops it on arrival).
STALE_ETA = timedelta(seconds=30)


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
    arrivals call: one round covers every station in ~30 s by day) and the lines' status."""

    def __init__(self) -> None:
        self.trains: list[dict[str, Any]] = []
        self.trains_ok = False  # this sweep's positions call worked: trains.json is due
        self.stations: dict[str, dict[str, Any]] = {}
        self.status: dict[str, str] = {}

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
            elif call.endpoint == "ttarrivals":
                found = etas_from(r.body, call.fetched_at)
                at = int(call.fetched_at.timestamp())
                for mapid in call.params.get("mapid", "").split(","):
                    # every station asked for: one with no predictions now loses its old ones
                    self.stations[mapid] = {"at": at, "eta": found.get(mapid, [])}
            elif call.endpoint == "routes":
                self.status = status_from(r.body) or self.status

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
            stamps = [t["t"] for t in self.trains if t["t"] is not None]
            docs.append(("trains.json", {
                "generated_at": generated, "build": os.environ.get("GIT_SHA", "unknown"),
                "data_at": max(stamps, default=None), "trains": self.trains, "status": self.status,
            }))  # fmt: skip
        docs.append(("stations.json", {"generated_at": generated, "stations": stations}))
        return docs
