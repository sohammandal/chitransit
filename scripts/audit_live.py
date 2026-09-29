"""Audit the long waits the live site is claiming right now, against Ingress.

For every bus the site would flag (lib/waits.ts: stop_gap_s > 2x sched_s and sched_due >= 2),
look for any sign in the raw reports that a bus of the route and direction was at that stop
during the claimed wait, independently of the live pass logic (ghost_ledger.live):

  crossing  a bus reported on both sides of the stop, on a pattern that has it, with no
            report at the stop in between: it passed (any gap between the reports, any trip,
            no departure rule)
  near      a bus of the route and direction reported within NEAR_M of the stop

A claim with neither is confirmed; otherwise it's refuted, with the evidence. Ingress lags the
feed by a few minutes (CDC), so the last minutes before now can't refute anything: they're
reported as unseen.

  eval "$(aws configure export-credentials --profile ghost-ledger --format env)"
  uv run --group transform python scripts/audit_live.py [--min-wait 40] [--routes 30,49]
"""

import argparse
import gzip
import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb

FEED = "https://www.chitransit.fyi/live/vehicles.json"
NEAR_M = 150
SLACK = timedelta(seconds=30)  # a pass this close to the claimed last one is that one
SAME_PASS = timedelta(minutes=5)  # the last bus lingering at the stop is still that pass
CHI = ZoneInfo("America/Chicago")


def feed() -> dict:
    req = urllib.request.Request(FEED, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - our own site
        body = r.read()
    return json.loads(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)


def claims(doc: dict, min_wait: int, routes: set[str]) -> list[dict]:
    out = []
    for v in doc["vehicles"]:
        g, s, due = v.get("stop_gap_s"), v.get("sched_s"), v.get("sched_due")
        if routes and v["rt"] not in routes:
            continue
        if v.get("at_terminal") or g is None or not s or due is None or due < 2 or g <= 2 * s:
            continue
        if g >= min_wait * 60:
            out.append(v)
    return sorted(out, key=lambda v: -v["stop_gap_s"])


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)) * 111_320
    return math.hypot(x, (lat2 - lat1) * 110_540)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-wait", type=int, default=0, help="only claims of at least N minutes")
    ap.add_argument("--routes", default="")
    args = ap.parse_args()
    doc = feed()
    now = datetime.fromisoformat(doc["generated_at"]).astimezone(CHI)
    todo = claims(doc, args.min_wait, set(filter(None, args.routes.split(","))))
    print(
        f"feed {doc['generated_at']} (build {str(doc.get('build'))[:7]}):"
        f" {len(todo)} long waits to audit"
    )
    if not todo:
        return 0

    c = duckdb.connect()
    c.execute("SET TimeZone='America/Chicago'")
    for ext in ("httpfs", "aws", "iceberg"):
        c.execute(f"INSTALL {ext}; LOAD {ext}")
    c.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    c.execute("ATTACH '354894954112' AS glue (TYPE iceberg, ENDPOINT_TYPE glue)")
    rts = sorted({v["rt"] for v in todo})
    since = now - timedelta(seconds=max(v["stop_gap_s"] for v in todo)) - timedelta(minutes=15)
    rt_list = ",".join(f"'{r}'" for r in rts)
    pos = c.sql(f"""
        SELECT vid, observed_at, rt, pid, pdist, lat, lon
        FROM glue.ghost_ledger_ingress.ghost_ops_bus_positions
        WHERE rt IN ({rt_list}) AND observed_at >= '{since.isoformat()}'::timestamptz
        QUALIFY row_number() OVER (PARTITION BY vid, observed_at ORDER BY __lsn) = 1
        ORDER BY vid, observed_at
    """).fetchall()  # noqa: S608 - our own values
    ingress_to = max((p[1] for p in pos), default=since)
    stops = c.sql(f"""
        WITH v AS (
            SELECT pid, rt, rtdir, pattern FROM glue.ghost_ledger_ingress.ghost_ops_patterns
            WHERE __op != 'd' AND rt IN ({rt_list})
            QUALIFY row_number() OVER (PARTITION BY pid ORDER BY changed_at DESC, __lsn DESC) = 1
        ),
        pts AS (SELECT pid, rt, rtdir, unnest(from_json(json_extract(pattern, '$.pt'), '["JSON"]')) AS pt FROM v)
        SELECT pid, rt, rtdir, pt->>'stpid', (pt->>'pdist')::double, (pt->>'lat')::double, (pt->>'lon')::double
        FROM pts WHERE (pt->>'typ') = 'S'
    """).fetchall()  # noqa: S608, E501
    dirs = {pid: (rt, d) for pid, rt, d, *_ in stops}
    at_stop: dict[tuple[str, str, str], list[tuple[int, float, float, float]]] = {}
    for pid, rt, d, sid, ft, lat, lon in stops:
        at_stop.setdefault((rt, d, sid), []).append((pid, ft, lat, lon))

    confirmed = 0
    for v in todo:
        key = (v["rt"], v["dir"], v["next_stpid"])
        last = now - timedelta(seconds=v["stop_gap_s"])
        where = at_stop.get(key, [])
        pids = {p for p, *_ in where}
        found = []
        prev = None
        for vid, t, _rt, pid, pdist, lat, lon in pos:
            if dirs.get(pid, (None, None)) != (v["rt"], v["dir"]) or vid == v["vid"]:
                prev = (vid, t, pid, pdist)
                continue
            if vid == v["last_by"] and t <= last + SAME_PASS:
                prev = (vid, t, pid, pdist)
                continue  # the claimed last bus itself, still at or just past the stop
            if t > last + SLACK:
                near = min((metres(lat, lon, la, lo) for _, _, la, lo in where), default=1e9)
                if near <= NEAR_M:
                    found.append(f"{vid} reported {near:.0f} m from the stop at {t:%H:%M:%S}")
                if prev and prev[0] == vid and prev[2] == pid and pid in pids:
                    ft = next(f for p, f, *_ in where if p == pid)
                    if prev[3] < ft <= pdist and prev[1] > last - SLACK:
                        found.append(
                            f"{vid} crossed it between {prev[1]:%H:%M:%S} and {t:%H:%M:%S}"
                        )
            prev = (vid, t, pid, pdist)
        verdict = "REFUTED" if found else "confirmed"
        confirmed += not found
        print(f"\n{verdict}: rt {v['rt']} {v['dir']} bus {v['vid']} at {v['next_stop']}"
              f" ({v['next_stpid']}): {v['stop_gap_s'] // 60} min since {v['last_by']} passed at"
              f" {last:%H:%M:%S}, scheduled every {v['sched_s'] // 60} min,"
              f" {v['sched_due']} due")  # fmt: skip
        for f in sorted(set(found))[:6]:
            print("   ", f)
    print(f"\n{confirmed} of {len(todo)} confirmed; Ingress has reports up to {ingress_to:%H:%M:%S}"
          f" (feed {now:%H:%M:%S}): anything after that couldn't be checked")  # fmt: skip
    return 0 if confirmed == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
