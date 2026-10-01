"""The site's data contract (transform/site_data.py check): catches a broken nightly before it
overwrites the files the live site reads."""

import importlib.util
from datetime import date
from pathlib import Path

SRC = Path(__file__).parents[1] / "transform/site_data.py"
spec = importlib.util.spec_from_file_location("site_data", SRC)
site_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_data)

TODAY = date(2026, 9, 27)
ROUTES = {"66": "Chicago"}
HEADWAYS = {"2026-09-27": {"66|East": {"14": 9.0}}}
COUNTS = {
    "scheduled": 373, "ran": 346, "announced": 6,
    "silent_proven": 2, "silent_possible": 11, "not_judged": 10,
}  # fmt: skip


def summary(**change):
    return {"date": "2026-09-26", "routes": {"66": COUNTS | change}}


def test_good_files_pass_and_each_break_is_caught():
    assert site_data.check(ROUTES, HEADWAYS, summary(), TODAY) == []
    cases = [
        ({}, HEADWAYS, summary()),  # no routes
        (ROUTES, {"2026-09-26": HEADWAYS["2026-09-27"]}, summary()),  # no schedule for today
        (ROUTES, {"2026-09-27": {"66": {"14": 9.0}}}, summary()),  # key without direction
        (ROUTES, {"2026-09-27": {"66|East": {"14": 0}}}, summary()),  # zero gap
        (ROUTES, HEADWAYS, {"date": "2026-09-26", "routes": {}}),  # empty summary
        (ROUTES, HEADWAYS, summary() | {"date": "2026-09-20"}),  # stale summary
        (ROUTES, HEADWAYS, summary(ran=345)),  # doesn't add up
        (ROUTES, HEADWAYS, summary(silent_proven=12)),  # proven > possible
        (ROUTES, HEADWAYS, {"date": "2026-09-26", "routes": {"66": {"ran": 1}}}),  # renamed fields
    ]
    for routes, headways, s in cases:
        assert site_data.check(routes, headways, s, TODAY), (routes, headways, s)


def test_report_card_contract():
    row = [10, 7, 1, 1, 1]  # scheduled, ran, announced, proven, couldn't tell
    good = {"days": ["2026-09-26"], "city": [row], "routes": {"66": [row]}}
    assert site_data.check_report(good) == []
    broken = [
        {"days": [], "city": [], "routes": {}},  # no watched days
        good | {"days": ["2026-09-27", "2026-09-26"]},  # out of order (and misaligned)
        good | {"routes": {"66": [[10, 7, 1, 1, 0]]}},  # doesn't add up
        good | {"routes": {"66": []}},  # missing a day
        good | {"city": [[10, 7, 1, 1]]},  # short row
    ]
    for report in broken:
        assert site_data.check_report(report), report


def test_stops_index_groups_a_corner_and_skips_last_stops():
    patterns = {
        1: {
            "s": [
                [-87.65, 41.94, "Clark & Belmont", 0, 2],
                [-87.65, 41.95, "Clark & Addison", 900, 1],
            ]
        },
        2: {
            "s": [
                [-87.6502, 41.9401, "Clark & Belmont", 0, 2],
                [-87.66, 41.94, "Belmont & X", 800, 1],
            ]
        },
        3: {"s": [[-87.70, 41.94, "Clark & Belmont", 0, 1], [-87.71, 41.94, "End", 500, 1]]},
        4: {"s": [[-87.6501, 41.9399, "Belmont & Clark", 0, 1], [-87.64, 41.94, "End", 900, 1]]},
    }
    stops = site_data.stops_index(patterns)
    assert [s[2] for s in stops].count("Clark & Belmont") == 2  # 1+2 are one corner; 3 is 4 km away
    # 1, 2 and 4 are one corner, though 4 spells it the other way round
    corner = next(s for s in stops if s[2] == "Clark & Belmont" and s[3] == [1, 2, 4])
    assert corner[:2] == [-87.6501, 41.94]  # the middle of its stops
    assert not any(s[2] in ("Clark & Addison", "Belmont & X", "End") for s in stops)  # last stops
