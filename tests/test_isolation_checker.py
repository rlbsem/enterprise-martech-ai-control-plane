"""Deliberately tiny unit fixtures test the independent checker, not executed acceptance evidence."""

from copy import deepcopy
import pytest
from scripts.check_isolation import check, rate_observations


def fixture():
    return {
        "protocol": {
            "requests_per_second": 2,
            "fault_start": 0,
            "fault_end": 10,
            "minimum_healthy_during_fault": 1,
            "proposals": 1,
        },
        "grants": [{"token": "p", "destination": "healthy", "window_id": 1, "kind": "dispatch"}],
        "destinations": {
            "healthy": {
                "requests": [{"permit": "p", "method": "POST", "at": 5, "status": 201}],
                "effects": [{"key": "j", "hash": "h", "receipt": "r"}],
            },
            "affected": {"requests": [], "effects": []},
        },
        "jobs": [{"id": "j", "destination": "healthy", "status": "succeeded", "hash": "h", "receipt": "r"}],
        "proposed": 1,
        "accepted": 1,
        "rejected": 0,
    }


def test_complete_fixture():
    assert check(fixture())["status"] == "passed"


@pytest.mark.parametrize(
    "fault", ["permit", "destination", "hash", "receipt", "unfinished", "duplicate", "quota", "claims"]
)
def test_checker_rejects_corrupted_execution(fault):
    record = deepcopy(fixture())
    if fault == "permit":
        record["grants"] = []
    if fault == "destination":
        record["jobs"][0]["destination"] = "affected"
    if fault == "hash":
        record["jobs"][0]["hash"] = "changed"
    if fault == "receipt":
        record["jobs"][0]["receipt"] = "changed"
    if fault == "unfinished":
        record["jobs"][0]["status"] = "retry"
    if fault == "duplicate":
        record["destinations"]["healthy"]["requests"] *= 2
    if fault == "claims":
        record["jobs"][0].update(claims=0, attempts=1)
    if fault == "quota":
        record["grants"] += [
            {"token": str(i), "destination": "healthy", "window_id": 1, "kind": "dispatch"} for i in range(2)
        ]
    with pytest.raises(AssertionError):
        check(record)


def delayed_arrival_fixture():
    record = fixture()
    record["grants"][0]["window_id"] = 5
    record["grants"] += [
        {"token": "prior", "destination": "healthy", "window_id": 4, "kind": "receipt"},
        {"token": "current", "destination": "healthy", "window_id": 5, "kind": "receipt"},
    ]
    record["destinations"]["healthy"]["requests"] += [
        {"permit": "prior", "method": "GET", "at": 5.01, "status": 404},
        {"permit": "current", "method": "GET", "at": 5.02, "status": 404},
    ]
    return record


def test_delayed_arrivals_do_not_relabel_permit_contract():
    result = check(delayed_arrival_fixture())
    assert result["status"] == "passed" and result["permit_issuance_violations"] == 0
    assert "budget_violations" not in result
    rates = result["rate_observations"]
    assert rates["permit_issuance_max_per_assigned_window"] == 2
    assert rates["receiver_arrival_max_per_utc_second"] == 3
    assert rates["receiver_arrival_buckets_above_limit"] == 1
    assert rates["destinations"]["healthy"]["receiver_arrival_over_limit_buckets"] == [{
        "receiver_utc_epoch_second": 5, "requests": 3,
        "permit_windows": [{"window_id": 4, "requests": 1}, {"window_id": 5, "requests": 2}],
    }]


def test_permit_violation_still_fails_with_dispersed_arrivals():
    record = delayed_arrival_fixture()
    for grant in record["grants"]:
        grant["window_id"] = 5
    for index, request in enumerate(record["destinations"]["healthy"]["requests"]):
        request["at"] = 5.1 + index
    assert rate_observations(record)["receiver_arrival_max_per_utc_second"] == 1
    with pytest.raises(AssertionError, match="permit issuance budget exceeded"):
        check(record)


def test_receiver_buckets_floor_boundaries_and_separate_destinations():
    record = fixture()
    record["grants"][0]["window_id"] = 4
    record["destinations"]["healthy"]["requests"][0]["at"] = 4.999999
    for destination in ("healthy", "affected"):
        record["grants"].append({
            "token": destination, "destination": destination, "window_id": 5, "kind": "receipt",
        })
        record["destinations"][destination]["requests"].append({
            "permit": destination, "method": "GET", "at": 5.0, "status": 404,
        })
    rates = rate_observations(record)
    assert rates["receiver_arrival_max_per_utc_second"] == 1
    assert rates["receiver_arrival_buckets_above_limit"] == 0
    assert all(lane["receiver_arrival_max_per_utc_second"] == 1 for lane in rates["destinations"].values())


def test_empty_receiver_reports_zero_arrivals_not_zero_permits():
    record = fixture()
    record["destinations"]["healthy"]["requests"] = []
    rates = rate_observations(record)
    assert rates["permit_issuance_max_per_assigned_window"] == 1
    assert rates["receiver_arrival_max_per_utc_second"] == 0
