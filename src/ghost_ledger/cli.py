"""Smoke-test CLI. Every command runs against GHOST_LEDGER_ENV (default dev); see config.py.

The poller itself is week-1 step 3 (docs/PLAN.md).
"""

import argparse
import logging
import signal
import threading

import psycopg

from ghost_ledger.bustracker import BusTrackerClient, raise_for_error
from ghost_ledger.config import (
    DAILY_CALL_QUOTA,
    get_settings,
    redacted_url,
    require_bus_key,
)


def cmd_routes(_: argparse.Namespace) -> None:
    settings = get_settings()
    with BusTrackerClient(require_bus_key(settings)) as client:
        result = raise_for_error(client.get_routes())
    routes = result.body.get("routes", [])
    print(f"HTTP {result.http_status}: {len(routes)} routes ({result.latency_ms} ms)")
    for r in routes[:5]:
        print(f"  {r['rt']:>4}  {r['rtnm']}")


def cmd_vehicles(args: argparse.Namespace) -> None:
    settings = get_settings()
    with BusTrackerClient(require_bus_key(settings)) as client:
        result = raise_for_error(client.get_vehicles(args.rt))
    vehicles = result.body.get("vehicle", [])
    print(f"HTTP {result.http_status}: {len(vehicles)} vehicles on {','.join(args.rt)}")
    for err in result.api_errors:
        print(f"  note: {err}")
    fields = ("vid", "rt", "tmstmp", "pid", "pdist", "stsd", "stst", "tatripid")
    for v in vehicles[:5]:
        print("  " + "  ".join(f"{f}={v.get(f)}" for f in fields))


def cmd_env(_: argparse.Namespace) -> None:
    s = get_settings()
    calls = s.vehicle_calls_per_day()
    print(f"env                      {s.env}")
    print(f"database_url             {redacted_url(s.database_url)}")
    print(f"bus api key              {'set' if s.bus_api_key else 'MISSING'}")
    print(f"train api key            {'set' if s.train_api_key else 'not set'}")
    print(f"poll interval            {s.poll_interval_seconds} s")
    print(f"poll routes              {','.join(s.poll_routes) if s.poll_routes else 'all'}")
    print(f"assignment grace         {s.assignment_grace_minutes} min")
    print(f"getvehicles calls/day    ~{calls:,} of {DAILY_CALL_QUOTA:,} key quota")


def cmd_check_db(_: argparse.Namespace) -> None:
    settings = get_settings()
    print(f"env={settings.env}  db={redacted_url(settings.database_url)}")
    with psycopg.connect(settings.database_url) as conn:
        wal = conn.execute("SHOW wal_level").fetchone()[0]
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'ops' AND table_name NOT LIKE 'bus\\_positions\\_%' "
            "ORDER BY table_name"
        ).fetchall()
        published = conn.execute(
            "SELECT pubname, schemaname || '.' || tablename FROM pg_publication_tables "
            "ORDER BY 1, 2"
        ).fetchall()
        partitions = conn.execute(
            "SELECT inhrelid::regclass::text FROM pg_inherits "
            "WHERE inhparent = 'ops.bus_positions'::regclass ORDER BY 1"
        ).fetchall()
    print(f"wal_level={wal}")
    print("ops tables:", ", ".join(t[0] for t in tables) or "(none)")
    print("published for CDC:", ", ".join(f"{p} → {t}" for p, t in published) or "(none)")
    print("bus_positions partitions:", ", ".join(p[0] for p in partitions) or "(none)")


def cmd_poll(args: argparse.Namespace) -> None:
    from ghost_ledger.ingest.poller import Poller

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())  # finish the current sweep, then exit
    with BusTrackerClient(require_bus_key(settings)) as client:
        Poller(settings, client).run(stop=stop, max_sweeps=args.sweeps)


def main() -> None:
    parser = argparse.ArgumentParser(prog="ghost-ledger")
    sub = parser.add_subparsers(required=True)
    sub.add_parser("env", help="Show resolved settings (secrets redacted)").set_defaults(
        func=cmd_env
    )
    sub.add_parser("routes", help="List bus routes (checks the API key)").set_defaults(
        func=cmd_routes
    )
    vehicles = sub.add_parser("vehicles", help="Live buses for up to 10 routes")
    vehicles.add_argument("--rt", nargs="+", default=["79"])
    vehicles.set_defaults(func=cmd_vehicles)
    poll = sub.add_parser("poll", help="Run the bus poller (Ctrl-C stops after the sweep)")
    poll.add_argument("--sweeps", type=int, default=None, help="stop after N sweeps")
    poll.set_defaults(func=cmd_poll)
    sub.add_parser("check-db", help="Check Postgres, WAL level and ops schema").set_defaults(
        func=cmd_check_db
    )
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
