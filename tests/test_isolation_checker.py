"""Deliberately tiny unit fixtures test the independent checker, not executed acceptance evidence."""

from copy import deepcopy
import pytest
from scripts.check_isolation import check


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
    "fault", ["permit", "destination", "hash", "receipt", "unfinished", "duplicate", "quota"]
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
    if fault == "quota":
        record["grants"] += [
            {"token": str(i), "destination": "healthy", "window_id": 1, "kind": "dispatch"} for i in range(2)
        ]
    with pytest.raises(AssertionError):
        check(record)
