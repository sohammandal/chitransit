from datetime import UTC, datetime

import httpx
import pytest

from ghost_ledger.bustracker import (
    MAX_ATTEMPTS,
    BusTrackerClient,
    chunked,
    parse_tmstmp,
    resolve_tmstmp,
)

KEY = "sekret-key-123"


def make_client(handler) -> BusTrackerClient:
    transport = httpx.MockTransport(handler)
    return BusTrackerClient(api_key=KEY, transport=transport, sleep=lambda s: None)


def ok_body(**payload) -> dict:
    return {"bustime-response": payload}


def test_chunked_splits_routes_into_batches_of_ten():
    routes = [str(i) for i in range(125)]
    batches = list(chunked(routes, 10))
    assert len(batches) == 13
    assert all(len(b) == 10 for b in batches[:-1])
    assert batches[-1] == [str(i) for i in range(120, 125)]


def test_parse_tmstmp_seconds_and_minutes():
    assert parse_tmstmp("20260925 18:06:36") == datetime(2026, 9, 25, 18, 6, 36)
    assert parse_tmstmp("20260925 18:06") == datetime(2026, 9, 25, 18, 6)


def test_resolve_tmstmp_in_the_dst_fall_back_hour_follows_the_fetch_time():
    # 2026-11-01 01:30 happens twice in Chicago: 06:30Z (CDT) and 07:30Z (CST).
    local = datetime(2026, 11, 1, 1, 30)
    first = datetime(2026, 11, 1, 6, 30, 20, tzinfo=UTC)
    second = datetime(2026, 11, 1, 7, 30, 20, tzinfo=UTC)
    assert resolve_tmstmp(local, first) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    assert resolve_tmstmp(local, second) == datetime(2026, 11, 1, 7, 30, tzinfo=UTC)


def test_get_vehicles_rejects_more_than_ten_routes():
    client = make_client(lambda request: httpx.Response(200, json=ok_body()))
    with pytest.raises(ValueError):
        client.get_vehicles([str(i) for i in range(11)])
    client.close()


def test_retries_timeout_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("[Errno 60] Operation timed out")
        return httpx.Response(200, json=ok_body(vehicle=[{"vid": "1"}]))

    with make_client(handler) as client:
        result = client.get_vehicles(["79"])
    assert result.ok and result.attempts == 2
    assert result.body["vehicle"] == [{"vid": "1"}]


def test_retries_server_errors_then_reports_failure_without_raising():
    with make_client(lambda request: httpx.Response(503, text="busy")) as client:
        result = client.get_vehicles(["79"])
    assert not result.ok
    assert result.attempts == MAX_ATTEMPTS
    assert result.error == "HTTP 503"
    assert result.raw == "busy"  # a failure's reply is kept too


def test_routes_with_no_buses_are_not_a_failure():
    # Late at night a batch of 10 routes can return only per-route errors.
    body = ok_body(error=[{"rt": "4", "msg": "No data found for parameter"}])
    with make_client(lambda request: httpx.Response(200, json=body)) as client:
        result = client.get_vehicles(["4"])
    assert result.ok
    assert result.body.get("vehicle", []) == []
    assert result.api_errors == [{"rt": "4", "msg": "No data found for parameter"}]


def test_error_text_never_contains_the_key():
    def handler(request):
        raise httpx.ConnectError(f"failed to reach {request.url}")

    with make_client(handler) as client:
        result = client.get_routes()
    assert KEY not in result.error
    assert "***" in result.error
    assert result.raw is None  # no reply at all


def test_get_pattern_fetches_by_pid():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=ok_body(ptr=[{"pid": 3637}]))

    with make_client(handler) as client:
        client.get_pattern(3637)
    assert seen["pid"] == "3637" and "rt" not in seen


@pytest.mark.parametrize(
    "msg",
    ["Invalid API access key supplied", "Transaction limit for current day has been exceeded."],
)
def test_a_refusal_is_a_failure_even_though_cta_answers_http_200(msg):
    body = ok_body(error=[{"msg": msg}])
    with make_client(lambda request: httpx.Response(200, json=body)) as client:
        result = client.get_vehicles(["79"])
    assert not result.ok
    assert result.error == f"API error: {msg}"
    assert result.attempts == 1  # refusals are not retried


def test_one_bad_route_among_real_data_keeps_the_data():
    body = ok_body(vehicle=[{"vid": "1"}], error=[{"rt": "X9", "msg": "Invalid route"}])
    with make_client(lambda request: httpx.Response(200, json=body)) as client:
        result = client.get_vehicles(["79", "X9"])
    assert result.ok and result.body["vehicle"] == [{"vid": "1"}]
    assert result.api_errors == [{"rt": "X9", "msg": "Invalid route"}]


def test_raw_keeps_the_exact_response_text():
    text = '{"bustime-response": {"vehicle": [{"vid": "1", "zone": ""}]}}'
    with make_client(lambda request: httpx.Response(200, text=text)) as client:
        result = client.get_vehicles(["79"])
    assert result.raw == text  # byte for byte, including fields we don't parse


def test_raw_never_holds_the_key_or_a_nul():
    text = f'{{"echo": "{KEY}", "nul": "a\x00b"}}'
    with make_client(lambda request: httpx.Response(200, text=text)) as client:
        raw = client.get_routes().raw
    assert KEY not in raw and "\x00" not in raw and "a\\u0000b" in raw


@pytest.mark.parametrize("reply", ["<html>oops</html>", '["not", "an", "object"]'])
def test_a_reply_we_cannot_read_is_retried_then_a_failure_not_a_crash(reply):
    with make_client(lambda request: httpx.Response(200, text=reply)) as client:
        result = client.get_vehicles(["79"])
    assert not result.ok and result.attempts == MAX_ATTEMPTS and result.body == {}


def test_an_oversized_reply_is_refused_and_not_stored():
    client = make_client(lambda request: httpx.Response(200, content=b"x" * 5_000_001))
    result = client.get_vehicles(["79"])
    assert not result.ok and "too large" in result.error and result.raw is None
