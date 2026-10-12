import httpx
import pytest

from ghost_ledger.traintracker import (
    LINES,
    TrainTrackerClient,
    as_list,
    count_etas,
    count_trains,
)

KEY = "trainkey0123456789abcdefghijklmn"  # 32 characters, like CTA's


def client(handler) -> TrainTrackerClient:
    return TrainTrackerClient(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def ctatt(**body) -> dict:
    return {"ctatt": {"tmst": "2026-10-11T19:48:13", "errCd": "0", "errNm": None, **body}}


def test_one_item_comes_as_an_object_several_as_a_list_none_as_null():
    assert as_list(None) == []
    assert as_list({"rn": "1"}) == [{"rn": "1"}]
    assert as_list([1, 2]) == [1, 2]
    body = {"route": [
        {"@name": "red", "train": [{"rn": "1"}, {"rn": "2"}]},
        {"@name": "y", "train": {"rn": "3"}},  # one train: an object
        {"@name": "p"},  # none
    ]}  # fmt: skip
    assert count_trains(body) == 3
    assert count_trains({"route": {"@name": "y", "train": {"rn": "3"}}}) == 1
    assert count_etas({"eta": {"rn": "1"}}) == 1 and count_etas({}) == 0


def test_positions_ask_for_all_eight_lines_as_json_and_keep_the_reply_raw():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ctatt(route=[{"@name": "red", "train": [{"rn": "812"}]}]))

    with client(handler) as c:
        r = c.positions()
    assert r.ok and r.http_status == 200 and r.attempts == 1
    assert count_trains(r.body) == 1
    url = seen[0].url
    assert url.path == "/api/1.0/ttpositions.aspx"
    assert url.params["rt"] == ",".join(LINES) and url.params["outputType"] == "JSON"
    assert '"rn": "812"' in r.raw or '"rn":"812"' in r.raw


def test_a_refusal_is_http_200_with_an_error_code_and_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200, json={"ctatt": {"errCd": "102", "errNm": "Daily limit exceeded"}}
        )

    with client(handler) as c:
        r = c.positions()
    assert not r.ok and r.error == "API error 102: Daily limit exceeded"
    assert len(calls) == 1  # a refusal won't change on retry


def test_server_errors_and_garbage_are_retried_then_reported():
    replies = iter(
        [
            httpx.Response(503),
            httpx.Response(200, text="<html>oops"),
            httpx.Response(200, json=ctatt(route=[])),
        ]
    )
    with client(lambda request: next(replies)) as c:
        r = c.positions()
    assert r.ok and r.attempts == 3


def test_the_key_never_lands_in_the_stored_reply_or_the_error():
    def echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"error for {request.url}")  # not JSON: echoes the URL

    with client(echo) as c:
        r = c.positions()
    assert not r.ok
    assert KEY not in (r.raw or "") and KEY not in (r.error or "")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with client(boom) as c:
        r = c.positions()
    assert r.raw is None and KEY not in r.error and r.attempts == 3


def test_arrivals_take_one_to_four_stations_in_one_call():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["mapid"])
        return httpx.Response(200, json=ctatt(eta=[{"staId": "40380", "isSch": "1"}]))

    with client(handler) as c:
        assert c.arrivals(["40380", "41450", "40890", "40450"]).ok
        assert c.arrivals(["40380"]).ok
        with pytest.raises(ValueError):
            c.arrivals(["40380", "41450", "40890", "40450", "41400"])
        with pytest.raises(ValueError):
            c.arrivals([])
    assert seen == ["40380,41450,40890,40450", "40380"]


def test_line_status_is_keyless_and_checks_its_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "key" not in request.url.params  # CTA's Customer Alerts API needs none
        assert request.url.host == "www.transitchicago.com"
        assert request.url.params["type"] == "rail"
        info = [{"Route": "Green Line", "RouteStatus": "Planned Work w/Station(s) Bypassed"}]
        return httpx.Response(200, json={"CTARoutes": {"ErrorCode": "0", "RouteInfo": info}})

    with client(handler) as c:
        r = c.line_status()
    assert r.ok and r.body["RouteInfo"][0]["Route"] == "Green Line"

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"CTARoutes": {"ErrorCode": "1", "ErrorMessage": "bad"}})

    with client(failing) as c:
        assert c.line_status().error == "API error: bad"
