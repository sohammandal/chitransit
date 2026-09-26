from datetime import datetime

import pytest

from cta_ghost_ledger.bustracker import BusTrackerClient, chunked, parse_tmstmp


def test_chunked_splits_routes_into_batches_of_ten():
    routes = [str(i) for i in range(125)]
    batches = list(chunked(routes, 10))
    assert len(batches) == 13
    assert all(len(b) == 10 for b in batches[:-1])
    assert batches[-1] == [str(i) for i in range(120, 125)]


def test_parse_tmstmp_seconds_and_minutes():
    assert parse_tmstmp("20260925 18:06:36") == datetime(2026, 9, 25, 18, 6, 36)
    assert parse_tmstmp("20260925 18:06") == datetime(2026, 9, 25, 18, 6)


def test_parse_tmstmp_is_naive_local_time():
    assert parse_tmstmp("20261101 01:30:00").tzinfo is None


def test_get_vehicles_rejects_more_than_ten_routes():
    client = BusTrackerClient(api_key="dummy")
    with pytest.raises(ValueError):
        client.get_vehicles([str(i) for i in range(11)])
    client.close()
