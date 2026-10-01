"""Write the site's static data files from the built analytics tables (docs/SITE.md).

Usage: uv run --group transform python transform/site_data.py
    --db transform/target/ghost_ledger.duckdb --out site/public/data

Writes, under --out:
  v1/manifest.json          when and from which commit the data was built
  v1/patterns/{pid}.json    {"rt", "dir", "c": [[lon, lat], ...],
                             "s": [[lon, lat, name, pdist, routes, stop id], ...]};
                             routes = how many routes serve that intersection (major cross
                             streets); stop id = CTA's, to read the stop's own wait (stops.json)
  v1/routes.json            {"49": "Western", ...}
  v1/stops.json             [[lon, lat, name, [pid, ...]], ...]: every stop riders can board
                             at, same-named stops within STOP_GROUP_M merged (both directions
                             of a corner), with the patterns that serve it (not a pattern's
                             last stop: nobody boards a bus there). The map's tappable stops.
  v1/headways.json          {"2026-09-27": {"49|North": {"14": 10.0, ...}}}: scheduled gap
                             (minutes) by Chicago hour, for today and tomorrow
  v1/summary.json           {"date": "2026-09-26", "routes": {"49": {"scheduled", "ran",
                             "announced", "silent_proven", "silent_possible", "not_judged"}}}
  v1/report.json            the report card, every watched day so far (from the published
                            trip_status history, --history): {"days": ["2026-09-26", ...],
                            "routes": {"49": [[scheduled, ran, announced, proven, unclear], ...]}}
                            one row per day, aligned with "days"; "city" holds the totals.
                            ran = ran + likely_ran; unclear = everything else (couldn't tell).

The nightly workflow syncs --out to the site bucket's data/ prefix; in dev, --out is the Astro
public folder so the dev server serves the same paths.

The files are a contract with the site (site/src/lib/types.ts). check() enforces it before
anything is written: a broken build exits non-zero, writes nothing, and the site keeps
yesterday's files.
"""

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

COUNTS = ["scheduled", "ran", "announced", "silent_proven", "silent_possible", "not_judged"]
# A day counts toward the report card only if we were watching: at most this share of its
# trips had no coverage at all (Sep 23-25 predate the poller and are ~100% unknown).
MAX_UNWATCHED = 0.10
# The report card file keeps this many recent watched days (the site shows the last 30).
REPORT_DAYS = 90
# Same-named stops this close are one corner (a street's two sides, a route's two directions)
STOP_GROUP_M = 150


def corner(name: str) -> str:
    """One key for both spellings of a corner: CTA names #9's stop "Ashland & Chicago" and #66's
    "Chicago & Ashland" (site/src/lib/stops.ts matches the same way)."""
    return " & ".join(sorted(name.split(" & ")))


def stops_index(patterns: dict) -> list:
    """[[lon, lat, name, [pid, ...]], ...]: see v1/stops.json above."""
    groups: dict[str, list[dict]] = {}
    for pid, doc in sorted(patterns.items()):
        for lon, lat, name, *_ in doc["s"][:-1]:
            near = [
                g for g in groups.setdefault(corner(name), [])
                if ((g["lon"] - lon) * 82_990) ** 2 + ((g["lat"] - lat) * 110_540) ** 2
                < STOP_GROUP_M**2
            ]  # fmt: skip
            if near:
                near[0]["pids"].add(pid)
                near[0]["at"].append((lon, lat))
            else:
                groups[corner(name)].append(
                    {"name": name, "lon": lon, "lat": lat, "pids": {pid}, "at": [(lon, lat)]}
                )
    # drawn at the middle of its stops: both curbs of a corner -> the middle of the street
    return sorted(
        [
            round(sum(x for x, _ in g["at"]) / len(g["at"]), 5),
            round(sum(y for _, y in g["at"]) / len(g["at"]), 5),
            g["name"],
            sorted(g["pids"]),
        ]
        for gs in groups.values()
        for g in gs
    )


def check_report(report: dict) -> list[str]:
    """What's wrong with report.json (empty when it's fine)."""
    days = report.get("days", [])
    bad = [] if days and days == sorted(set(days)) else ["report.json: no days, or not in order"]
    for rt, rows in {"city": report.get("city", []), **report.get("routes", {})}.items():
        if len(rows) != len(days):
            bad.append(f"report.json: {rt} has {len(rows)} rows for {len(days)} days")
            continue
        for row in rows:
            if len(row) != 5 or any(not isinstance(v, int) or v < 0 for v in row):
                bad.append(f"report.json: bad row for {rt}")
            elif sum(row[1:]) != row[0]:
                bad.append(f"report.json: {rt} outcomes don't add up to scheduled")
    return bad


def check(routes: dict, headways: dict, summary: dict, today: date) -> list[str]:
    """What's wrong with the files the site reads (empty when they're fine)."""
    bad = []
    if not routes or not all(isinstance(k, str) and isinstance(v, str) for k, v in routes.items()):
        bad.append("routes.json: empty or not {rt: name}")
    if str(today) not in headways:
        bad.append(f"headways.json: no schedule for today ({today})")
    for day, keys in headways.items():
        for key, hours in keys.items():
            if key.count("|") != 1 or not all(h.isdigit() and 0 <= int(h) < 24 for h in hours):
                bad.append(f"headways.json: bad entry {day} {key}")
            elif not all(isinstance(m, int | float) and m > 0 for m in hours.values()):
                bad.append(f"headways.json: non-positive gap {day} {key}")
    if not summary.get("routes"):
        bad.append("summary.json: no routes")
    elif not (today - timedelta(days=3) <= date.fromisoformat(summary["date"]) < today):
        bad.append(f"summary.json: date {summary['date']} isn't one of the last 3 days")
    for rt, c in summary.get("routes", {}).items():
        if sorted(c) != sorted(COUNTS) or any(not isinstance(v, int) or v < 0 for v in c.values()):
            bad.append(f"summary.json: bad counts for {rt}")
        elif c["silent_proven"] > c["silent_possible"]:
            bad.append(f"summary.json: {rt} proven > possible")
        elif c["ran"] + c["announced"] + c["silent_possible"] + c["not_judged"] != c["scheduled"]:
            bad.append(f"summary.json: {rt} counts don't add up to scheduled")
    return bad


def main() -> None:
    import duckdb  # transform dependency group; check() stays importable without it

    p = argparse.ArgumentParser()
    p.add_argument("--db", default="transform/target/ghost_ledger.duckdb")
    p.add_argument("--out", default="site/public/data")
    p.add_argument("--history", required=True, help="published analytics, e.g. s3://…/analytics")
    args = p.parse_args()
    out = Path(args.out) / "v1"

    con = duckdb.connect(args.db, read_only=True)
    patterns = {
        pid: {
            "rt": rt,
            "dir": f"{direction}bound",
            "c": coords,
            "s": [
                [s["lon"], s["lat"], s["name"], s["pdist"], s["routes"], s["stop_id"]]
                for s in stops or []
            ],
        }
        for pid, rt, direction, coords, stops in con.execute(
            "SELECT pid, rt, direction, coords, stops FROM site_patterns"
        ).fetchall()
    }
    routes = dict(con.execute("SELECT rt, name FROM site_routes ORDER BY rt").fetchall())

    headways: dict = {}
    for day, rt, direction, hour, minutes in con.execute(
        "SELECT service_date, rt, direction, hour, headway_min FROM site_headways"
    ).fetchall():
        headways.setdefault(str(day), {}).setdefault(f"{rt}|{direction}", {})[str(hour)] = minutes

    last = con.execute("SELECT max(service_date) FROM site_route_summary").fetchone()[0]
    rows = con.execute(
        f"SELECT rt, {', '.join(COUNTS)} FROM site_route_summary WHERE service_date = ?", [last]
    ).fetchall()
    by_route = {r[0]: dict(zip(COUNTS, r[1:], strict=True)) for r in rows}
    summary = {"date": str(last), "routes": by_route}

    report = report_card(duckdb.connect(), args.history)

    today = datetime.now(ZoneInfo("America/Chicago")).date()
    bad = check(routes, headways, summary, today) + check_report(report)
    if not patterns:
        bad.append("site_patterns: no patterns")
    if bad:
        sys.exit("site data failed its checks, nothing written:\n  " + "\n  ".join(bad))

    (out / "patterns").mkdir(parents=True, exist_ok=True)
    compact = {"separators": (",", ":")}
    for pid, doc in patterns.items():
        (out / "patterns" / f"{pid}.json").write_text(json.dumps(doc, **compact))
    (out / "routes.json").write_text(json.dumps(routes, **compact))
    (out / "stops.json").write_text(json.dumps(stops_index(patterns), **compact))
    (out / "headways.json").write_text(json.dumps(headways, **compact))
    (out / "summary.json").write_text(json.dumps(summary, **compact))
    (out / "report.json").write_text(json.dumps(report, **compact))
    manifest = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method_version": os.environ.get("GITHUB_SHA", "local")[:7],
        "patterns": len(patterns),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(patterns)} patterns, {len(routes)} routes, report {report['days']} -> {out}")


def report_card(con, history: str) -> dict:
    """Every watched day's outcomes per route, from the published trip_status history."""
    if history.startswith("s3://"):
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
        con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    src = f"read_parquet('{history}/trip_status/*/*.parquet', hive_partitioning = true)"
    rows = con.execute(
        f"""
        WITH trips AS (SELECT service_date, rt, status, strict_evidence FROM {src}
                       WHERE status != 'pending'),
        watched AS (SELECT service_date FROM trips GROUP BY 1
                    HAVING avg((status = 'unknown')::int) <= {MAX_UNWATCHED})
        SELECT strftime(service_date, '%Y-%m-%d'), rt, count(*),
               count(*) FILTER (WHERE status IN ('ran', 'likely_ran')),
               count(*) FILTER (WHERE status = 'flagged'),
               count(*) FILTER (WHERE status = 'silent' AND strict_evidence)
        FROM trips WHERE service_date IN (SELECT service_date FROM watched)
        GROUP BY ALL ORDER BY 1, 2
        """
    ).fetchall()
    days = sorted({r[0] for r in rows})[-REPORT_DAYS:]
    rows = [r for r in rows if r[0] in set(days)]
    index = {d: i for i, d in enumerate(days)}
    empty = lambda: [[0, 0, 0, 0, 0] for _ in days]  # noqa: E731 - a route can skip a day
    routes: dict[str, list[list[int]]] = {}
    city = empty()
    for day, rt, scheduled, ran, announced, proven in rows:
        row = [scheduled, ran, announced, proven, scheduled - ran - announced - proven]
        routes.setdefault(rt, empty())[index[day]] = row
        city[index[day]] = [a + b for a, b in zip(city[index[day]], row, strict=True)]
    return {"days": days, "city": city, "routes": routes}


if __name__ == "__main__":
    main()
