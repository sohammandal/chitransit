from datetime import UTC, date, datetime, timedelta

from ghost_ledger.ingest.presence import PresenceTracker, Sighting, TripKey

T0 = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
GRACE = timedelta(minutes=10)
EAST = TripKey("79", "East", date(2026, 9, 25), 64890)
WEST = TripKey("79", "West", date(2026, 9, 25), 69390)


def seen(vid: str, trip: TripKey | None, at: datetime, rt: str = "79") -> Sighting:
    return Sighting(vid, rt, trip, at)


def test_first_sighting_on_a_trip_is_assigned_and_repeats_are_not():
    t = PresenceTracker(GRACE)
    assert [s.vid for s in t.observe([seen("1", EAST, T0)], {"79"}, T0).assigned] == ["1"]
    later = T0 + timedelta(seconds=30)
    assert t.observe([seen("1", EAST, later)], {"79"}, later).assigned == []
    # its next trip is a new assignment
    next_trip = T0 + timedelta(minutes=40)
    assert [
        s.trip for s in t.observe([seen("1", WEST, next_trip)], {"79"}, next_trip).assigned
    ] == [WEST]


def test_a_sighting_without_a_trip_changes_nothing_but_counts_as_present():
    t = PresenceTracker(GRACE, trips={"1": EAST}, last_seen={"1": ("79", T0)})
    late = T0 + timedelta(minutes=30)
    changes = t.observe([seen("1", None, late)], {"79"}, late)
    assert changes.assigned == [] and changes.gone == []
    assert t.trips["1"] == EAST


def test_a_bus_is_gone_only_after_the_grace_period():
    t = PresenceTracker(GRACE, trips={"1": EAST}, last_seen={"1": ("79", T0)})
    nine = T0 + timedelta(minutes=9)
    assert t.observe([], {"79"}, nine).gone == []
    ten = T0 + timedelta(minutes=10)
    assert t.observe([], {"79"}, ten).gone == ["1"]
    assert "1" not in t.trips and "1" not in t.last_seen


def test_a_failed_route_call_is_not_evidence_that_its_buses_left():
    t = PresenceTracker(GRACE, trips={"1": EAST}, last_seen={"1": ("79", T0)})
    much_later = T0 + timedelta(hours=1)
    assert t.observe([], {"9"}, much_later).gone == []  # route 79's call failed
    assert t.observe([], {"79", "9"}, much_later).gone == ["1"]


def test_an_assignment_with_no_recent_sighting_is_gone_once_its_route_is_polled():
    # e.g. the poller was down for hours: the stored assignment has no evidence of presence
    t = PresenceTracker(GRACE, trips={"1": EAST})
    assert t.observe([], {"79"}, T0).gone == ["1"]


def test_buses_without_trips_are_still_tracked_for_leaving_the_feed():
    t = PresenceTracker(GRACE)
    t.observe([seen("2", None, T0)], {"79"}, T0)
    assert t.observe([], {"79"}, T0 + GRACE).gone == ["2"]
