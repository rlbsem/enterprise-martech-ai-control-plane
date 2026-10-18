"""Offline cleanup authority checks; never delete AWS resources."""

from copy import deepcopy
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

from controlplane.cleanup_check import check
from controlplane.teardown import validate_plan


def inputs():
    inventory = {
        "environment": "stage",
        "region": "ca-central-1",
        "disposition": "discard_isolated_synthetic_state",
        "resources": {
            'aws_db_instance.state["control"]': {"id": "control-stage-control", "type": "aws_db_instance"}
        },
    }
    plan = {
        "resource_changes": [
            {
                "address": 'aws_db_instance.state["control"]',
                "type": "aws_db_instance",
                "mode": "managed",
                "change": {"actions": ["delete"], "before": {"id": "control-stage-control"}},
            }
        ]
    }
    return plan, inventory


def test_exact_destroy_inventory():
    plan, inventory = inputs()
    assert validate_plan(plan, inventory) == 1


@pytest.mark.parametrize(
    "fault", ["update", "replace", "other_id", "extra", "missing", "prod", "retain", "incomplete"]
)
def test_cleanup_cannot_expand_or_hide_scope(fault):
    plan, inventory = inputs()
    change = plan["resource_changes"][0]
    if fault == "update":
        change["change"]["actions"] = ["update"]
    elif fault == "replace":
        change["change"]["actions"] = ["delete", "create"]
    elif fault == "other_id":
        change["change"]["before"]["id"] = "shared-production"
    elif fault == "extra":
        plan["resource_changes"].append(deepcopy(change))
    elif fault == "missing":
        plan["resource_changes"] = []
    elif fault == "prod":
        inventory["environment"] = "prod"
    elif fault == "retain":
        inventory["disposition"] = "retain"
    elif fault == "incomplete":
        plan["complete"] = False
    with pytest.raises(ValueError):
        validate_plan(plan, inventory)


@pytest.mark.parametrize(
    "code,expected",
    [("DBInstanceNotFound", "ABSENT"), ("AccessDenied", "READ_FAILED"), ("Throttling", "READ_FAILED")],
)
def test_absence_requires_independent_service_result(code, expected):
    rds = Mock()
    rds.describe_db_instances.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "private"}}, "DescribeDBInstances"
    )
    result = check([{"type": "aws_db_instance", "id": "control-stage-control"}], {"rds": rds})
    assert result["results"][0]["status"] == expected
    assert result["all_checked_resources_absent"] == (expected == "ABSENT")
    assert "control-stage-control" not in str(result)


def test_empty_or_unsupported_inventory_is_not_cleanup_success():
    assert not check([], {})["all_checked_resources_absent"]
    assert not check([{"type": "unknown", "id": "private"}], {})["all_checked_resources_absent"]


def test_paginated_or_matching_log_groups_are_not_absence():
    logs = Mock()
    logs.describe_log_groups.return_value = {"nextToken": "more", "logGroups": []}
    item = {"type": "aws_cloudwatch_log_group", "id": "/enterprise/control-stage/api"}
    assert not check([item], {"logs": logs})["all_checked_resources_absent"]
    logs.describe_log_groups.return_value = {"logGroups": [{"logGroupName": item["id"]}]}
    assert check([item], {"logs": logs})["results"][0]["status"] == "PRESENT"
