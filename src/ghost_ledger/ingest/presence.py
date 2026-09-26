"""Which trip each bus is on, and when a bus has left the feed. Pure logic, no I/O.

"Left the feed" is an inference, not something CTA reports: a bus counts as gone only
after it has been absent for the grace period, and only while its route was actually
polled successfully (a failed API call is not evidence that 10 routes' buses vanished).
Time here is processing time (fetched_at): the question is whether the bus is in the
feed, not how old its position is.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class TripKey:
    """The trip a bus is running, matched to GTFS on its first departure (docs/PLAN.md)."""

    rt: str
    direction: str
    stsd: date
    stst: int


@dataclass(frozen=True)
class Sighting:
    vid: str
    rt: str
    trip: TripKey | None  # None: no stsd/stst, or the pattern's direction is not known yet
    fetched_at: datetime


@dataclass
class PresenceChanges:
    # Buses whose trip is new or different: write an assignment
    assigned: list[Sighting]
    # Buses absent past the grace period on a route that was polled: delete their state
    gone: list[str]


class PresenceTracker:
    def __init__(
        self,
        grace: timedelta,
        trips: dict[str, TripKey] | None = None,
        last_seen: dict[str, tuple[str, datetime]] | None = None,
    ) -> None:
        self.grace = grace
        # vid -> current trip, as stored in ops.bus_assignments
        self.trips: dict[str, TripKey] = dict(trips or {})
        # vid -> (route it was last seen on, fetched_at of that sweep)
        self.last_seen: dict[str, tuple[str, datetime]] = dict(last_seen or {})

    def observe(
        self, sightings: list[Sighting], polled_routes: set[str], now: datetime
    ) -> PresenceChanges:
        assigned = []
        present = set()
        for s in sightings:
            present.add(s.vid)
            self.last_seen[s.vid] = (s.rt, s.fetched_at)
            if s.trip is not None and self.trips.get(s.vid) != s.trip:
                self.trips[s.vid] = s.trip
                assigned.append(s)

        gone = []
        for vid, (rt, seen_at) in list(self.last_seen.items()):
            if vid in present or rt not in polled_routes:
                continue
            if now - seen_at >= self.grace:
                gone.append(vid)
                del self.last_seen[vid]
                self.trips.pop(vid, None)
        # A bus with an assignment but no sighting on record (e.g. the poller was down
        # longer than a day) has no evidence of presence at all.
        for vid, trip in list(self.trips.items()):
            if vid not in self.last_seen and vid not in present and trip.rt in polled_routes:
                gone.append(vid)
                del self.trips[vid]
        return PresenceChanges(assigned=assigned, gone=gone)
