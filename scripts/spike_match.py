"""Week-1 spike: do observed Bus Tracker trips match GTFS trips on (rt, direction, stsd, stst)?

Usage: uv run python scripts/spike_match.py --rt 79 9
Reads data/spike/*.jsonl + patterns and data/gtfs/google_transit.zip.
"""

import argparse
import csv
import io
import json
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

SPIKE = Path("data/spike")
GTFS = Path("data/gtfs/google_transit.zip")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def gtfs_rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def active_services(zf: zipfile.ZipFile, day: date) -> set[str]:
    ymd = day.strftime("%Y%m%d")
    active = {
        r["service_id"]
        for r in gtfs_rows(zf, "calendar.txt")
        if r["start_date"] <= ymd <= r["end_date"] and r[WEEKDAYS[day.weekday()]] == "1"
    }
    for r in gtfs_rows(zf, "calendar_dates.txt"):
        if r["date"] == ymd:
            (active.add if r["exception_type"] == "1" else active.discard)(r["service_id"])
    return active


def hms_to_seconds(value: str) -> int:
    h, m, s = (int(x) for x in value.split(":"))
    return h * 3600 + m * 60 + s


def load_scheduled(routes: set[str], day: date) -> dict[tuple, list[dict]]:
    """(rt, direction word, stst seconds) -> GTFS trips active on `day`."""
    with zipfile.ZipFile(GTFS) as zf:
        services = active_services(zf, day)
        trips = {
            r["trip_id"]: r
            for r in gtfs_rows(zf, "trips.txt")
            if r["route_id"] in routes and r["service_id"] in services
        }
        first: dict[str, tuple[int, int]] = {}  # trip_id -> (stop_sequence, departure seconds)
        for r in gtfs_rows(zf, "stop_times.txt"):
            tid = r["trip_id"]
            if tid in trips:
                seq = int(r["stop_sequence"])
                if tid not in first or seq < first[tid][0]:
                    first[tid] = (seq, hms_to_seconds(r["departure_time"]))
    index: dict[tuple, list[dict]] = defaultdict(list)
    for tid, trip in trips.items():
        index[(trip["route_id"], trip["direction"], first[tid][1])].append(trip)
    return index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rt", nargs="+", default=["79"])
    args = parser.parse_args()
    tag = "_".join(args.rt)

    pid_dir = {}
    for rt in args.rt:
        for p in json.loads((SPIKE / f"patterns_{rt}.json").read_text()).get("ptr", []):
            pid_dir[int(p["pid"])] = p["rtdir"].removesuffix("bound")  # "Westbound" -> "West"

    # Observed trips: one key per (rt, dir, stsd, stst), with evidence about each.
    observed: dict[tuple, dict] = {}
    polls, first_fetch, last_fetch = 0, None, None
    for line in (SPIKE / f"vehicles_{tag}.jsonl").open():
        rec = json.loads(line)
        polls += 1
        first_fetch = first_fetch or rec["fetched_at"]
        last_fetch = rec["fetched_at"]
        for v in rec.get("body", {}).get("vehicle", []):
            key = (v["rt"], pid_dir.get(v["pid"], f"?pid{v['pid']}"), v["stsd"], v["stst"])
            o = observed.setdefault(
                key, {"vids": set(), "tatripid": set(), "orig": set(), "block": set(), "n": 0}
            )
            o["vids"].add(v["vid"])
            o["tatripid"].add(v["tatripid"])
            o["orig"].add(v["origtatripno"])
            o["block"].add(v["tablockid"])
            o["n"] += 1

    print(f"{polls} polls, {first_fetch} -> {last_fetch}, {len(observed)} observed trip keys\n")

    days = {date.fromisoformat(k[2]) for k in observed}
    sched: dict[tuple, list[dict]] = {}
    for d in days:
        for (rt, direction, stst), trips in load_scheduled(set(args.rt), d).items():
            sched[(rt, direction, d.isoformat(), stst)] = trips

    outcome = Counter()
    examples: dict[str, list] = defaultdict(list)
    for key, o in sorted(observed.items()):
        cands = sched.get(key, [])
        kind = "exactly one" if len(cands) == 1 else ("none" if not cands else "ambiguous")
        outcome[(key[0], kind)] += 1
        if kind != "exactly one" or len(examples[kind]) < 3:
            nearest = (
                min(
                    ((abs(s[3] - key[3]), s[3]) for s in sched if s[:3] == key[:3]),
                    default=None,
                )
                if kind == "none"
                else None
            )
            examples[kind].append((key, o, cands[:3], nearest))

    for rt in args.rt:
        total = sum(v for (r, _), v in outcome.items() if r == rt)
        one = outcome[(rt, "exactly one")]
        print(
            f"route {rt}: {one}/{total} matched exactly one GTFS trip ({one / total:.1%}); "
            f"none={outcome[(rt, 'none')]} ambiguous={outcome[(rt, 'ambiguous')]}"
        )

    for kind, rows in examples.items():
        print(f"\n--- {kind} (sample) ---")
        for key, o, cands, nearest in rows[:8]:
            print(
                f"  {key}  vids={sorted(o['vids'])} sightings={o['n']} "
                f"tatripid={sorted(o['tatripid'])} orig={sorted(o['orig'])} "
                f"block={sorted(o['block'])}"
            )
            for c in cands:
                print(
                    f"      gtfs trip_id={c['trip_id']} schd_trip_id={c['schd_trip_id']} "
                    f"block_id={c['block_id']} service={c['service_id']}"
                )
            if nearest:
                print(f"      nearest same-dir GTFS start: {nearest[1]} (off by {nearest[0]} s)")

    # Reverse view: scheduled trips starting inside the window - were they seen?
    t0 = datetime.fromisoformat(first_fetch).astimezone().replace(tzinfo=None)
    t1 = datetime.fromisoformat(last_fetch).astimezone().replace(tzinfo=None)
    day = t0.date()
    start_s = (t0 - datetime.combine(day, datetime.min.time())).seconds
    end_s = (t1 - datetime.combine(day, datetime.min.time())).seconds - 600  # 10 min grace
    print(f"\nScheduled trips starting {start_s}-{end_s} s after midnight on {day}:")
    for rt in args.rt:
        due = [
            k for k in sched if k[0] == rt and k[2] == day.isoformat() and start_s <= k[3] <= end_s
        ]
        seen = [k for k in due if k in observed]
        print(f"  route {rt}: {len(seen)}/{len(due)} seen")
        for k in sorted(set(due) - set(seen)):
            print(f"    not seen: {k}")


if __name__ == "__main__":
    main()
