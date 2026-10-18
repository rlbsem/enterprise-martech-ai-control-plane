"""Independent metadata checks, shared by the deployment gate and native controller."""

from datetime import UTC, datetime, timedelta
import re
import math


def account_gate(manifest, clients):
    if not re.fullmatch("[0-9]{12}", manifest["account"]):
        raise ValueError("Account pin required")
    if clients["sts"].get_caller_identity()["Account"] != manifest["account"]:
        raise RuntimeError("Wrong account")
    state = clients["freetier"].get_account_plan_state()
    if (
        state["accountPlanType"] != "FREE"
        or state["accountPlanStatus"] != "ACTIVE"
        or not math.isfinite(float(state["accountPlanRemainingCredits"]["amount"]))
        or float(state["accountPlanRemainingCredits"]["amount"]) < 5
    ):
        raise RuntimeError("Active Free plan with five-credit headroom required")


def armed_gate(manifest, clients):
    account_gate(manifest, clients)
    deadline = datetime.fromisoformat(manifest["cleanup_at"])
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    if deadline.tzinfo is None or not 0 < remaining <= 10800:
        raise RuntimeError("Cleanup deadline expired or exceeds three hours")
    prefix = "csa-native-" + manifest["run"]
    if not re.fullmatch("[a-z0-9]{8,16}", manifest["run"]) or manifest["region"] != "us-east-2":
        raise ValueError("Pinned Ohio run required")
    if manifest["schedule"] != prefix + "-cleanup" or manifest["watchdog"] != prefix + "-watchdog":
        raise ValueError("Exact guard identity required")
    schedule = clients["scheduler"].get_schedule(Name=manifest["schedule"], GroupName="default")
    function = clients["lambda"].get_function_configuration(FunctionName=manifest["watchdog"])
    env = function["Environment"]["Variables"]
    target = schedule["Target"]
    role_prefix = f"arn:aws:iam::{manifest['account']}:role/csa-native-guard/{manifest['run']}/{prefix}"
    arn = f"arn:aws:lambda:us-east-2:{manifest['account']}:function:{prefix}-watchdog"
    role = f"arn:aws:iam::{manifest['account']}:role/csa-native-guard/{manifest['run']}/{prefix}-schedule"
    if (
        schedule["State"] != "ENABLED"
        or schedule["ScheduleExpression"] != "rate(1 minute)"
        or schedule.get("ScheduleExpressionTimezone") != "UTC"
        or schedule["FlexibleTimeWindow"]["Mode"] != "OFF"
        or target["Arn"] != arn
        or target["RoleArn"] != role
        or function["FunctionArn"] != arn
        or target["RetryPolicy"] != {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 2}
        or target["DeadLetterConfig"]["Arn"]
        != f"arn:aws:sqs:us-east-2:{manifest['account']}:{prefix}-cleanup-failures"
        or schedule["StartDate"] != deadline
        or schedule["EndDate"] != deadline + timedelta(hours=1)
        or function["CodeSha256"] != manifest["guard_code_sha256"]
        or function["State"] != "Active"
        or function["Role"] != role_prefix + "-watchdog"
        or function.get("VpcConfig", {}).get("VpcId")
        or env["RUN_ID"] != manifest["run"]
        or env["ACCOUNT_ID"] != manifest["account"]
        or datetime.fromisoformat(env["CLEANUP_AT"]) != deadline
        or env["DISPOSITION"] != "discard_isolated_synthetic_state"
    ):
        raise RuntimeError("Independent armed-watchdog metadata check failed")
    # Describe the independent reader separately; writer assertions are not its evidence.
    checker = clients["lambda"].get_function_configuration(FunctionName=prefix + "-checker")
    if (
        checker["State"] != "Active"
        or checker["Role"] != role_prefix + "-checker"
        or checker["CodeSha256"] != manifest["guard_code_sha256"]
        or checker["Environment"]["Variables"]["RUN_ID"] != manifest["run"]
    ):
        raise RuntimeError("Independent checker identity not established")
    return {
        "free_plan": True,
        "account_pin": True,
        "watchdog_armed": True,
        "scope": "read_only_metadata_not_execution_or_permission_simulation",
    }
