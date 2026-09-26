"""Week-1 spike: poll a few routes every 30 s into JSONL (no Postgres yet).

Usage: uv run python scripts/spike_collect.py --rt 79 --minutes 60
Writes data/spike/vehicles_<rt>.jsonl (one line per poll) and patterns_<rt>.json.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from ghost_ledger.bustracker import BusTrackerClient
from ghost_ledger.config import get_settings, require_bus_key

OUT = Path("data/spike")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rt", nargs="+", default=["79"])
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    tag = "_".join(args.rt)
    key = require_bus_key(get_settings())

    with BusTrackerClient(key) as client:
        for rt in args.rt:
            (OUT / f"patterns_{rt}.json").write_text(json.dumps(client.get_patterns(rt).body))

        deadline = time.monotonic() + args.minutes * 60
        next_tick = time.monotonic()
        polls = 0
        with (OUT / f"vehicles_{tag}.jsonl").open("a") as f:
            while time.monotonic() < deadline:
                fetched_at = datetime.now(UTC).isoformat()
                result = client.get_vehicles(args.rt)
                record: dict = {
                    "fetched_at": fetched_at,
                    "status": result.http_status,
                    "attempts": result.attempts,
                    "body": result.body,
                }
                if not result.ok:
                    record["error"] = result.error
                f.write(json.dumps(record) + "\n")
                f.flush()
                polls += 1
                n = len(record.get("body", {}).get("vehicle", []))
                print(f"{fetched_at} poll {polls}: {n} vehicles {record.get('error', '')}")
                next_tick += args.interval
                time.sleep(max(0.0, next_tick - time.monotonic()))


if __name__ == "__main__":
    main()
