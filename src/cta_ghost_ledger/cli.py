"""Smoke-test CLI. The poller itself is week-1 work (see CLAUDE.md)."""

import argparse

import psycopg

from cta_ghost_ledger.bustracker import BusTrackerClient
from cta_ghost_ledger.config import get_settings, require_bus_key


def cmd_routes(_: argparse.Namespace) -> None:
    settings = get_settings()
    with BusTrackerClient(require_bus_key(settings)) as client:
        body, status = client.get_routes()
    routes = body.get("routes", [])
    print(f"HTTP {status}: {len(routes)} routes")
    for r in routes[:5]:
        print(f"  {r['rt']:>4}  {r['rtnm']}")


def cmd_vehicles(args: argparse.Namespace) -> None:
    settings = get_settings()
    with BusTrackerClient(require_bus_key(settings)) as client:
        body, status = client.get_vehicles(args.rt)
    vehicles = body.get("vehicle", [])
    print(f"HTTP {status}: {len(vehicles)} vehicles on {','.join(args.rt)}")
    fields = ("vid", "rt", "tmstmp", "pid", "pdist", "stsd", "stst", "tatripid")
    for v in vehicles[:5]:
        print("  " + "  ".join(f"{f}={v.get(f)}" for f in fields))


def cmd_check_db(_: argparse.Namespace) -> None:
    settings = get_settings()
    with psycopg.connect(settings.database_url) as conn:
        wal = conn.execute("SHOW wal_level").fetchone()[0]
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'ops' ORDER BY table_name"
        ).fetchall()
        pubs = conn.execute("SELECT pubname FROM pg_publication").fetchall()
    print(f"wal_level={wal}")
    print("ops tables:", ", ".join(t[0] for t in tables) or "(none)")
    print("publications:", ", ".join(p[0] for p in pubs) or "(none)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ghost-ledger")
    sub = parser.add_subparsers(required=True)
    sub.add_parser("routes", help="List bus routes (checks the API key)").set_defaults(
        func=cmd_routes
    )
    vehicles = sub.add_parser("vehicles", help="Live buses for up to 10 routes")
    vehicles.add_argument("--rt", nargs="+", default=["79"])
    vehicles.set_defaults(func=cmd_vehicles)
    sub.add_parser("check-db", help="Check Postgres, WAL level and ops schema").set_defaults(
        func=cmd_check_db
    )
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
