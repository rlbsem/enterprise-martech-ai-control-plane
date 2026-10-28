"""Public operator report boundaries and bounded credit arithmetic."""

import json
from decimal import Decimal

import pytest

from controlplane.recovery_cost import estimate
from scripts.recovery_report import render


def test_credit_estimate_includes_residual_costs_without_free_allocation_assumptions():
    plan = estimate()
    assert sum(Decimal(r["planned_usd"]) for r in plan["lines"]) == Decimal(plan["planned_usd"])
    assert Decimal(plan["planned_usd"]) < Decimal(plan["authorized_planned_ceiling_usd"])
    assert plan["maximum_concurrent_rds_instances"] == 2
    assert all(Decimal(r["planned_usd"]) > 0 for r in plan["lines"])
    assert "Alerts and this estimate are not a hard billing cap" in plan["conditions"]


def test_report_refuses_to_relabel_native_or_incomplete_evidence_as_local():
    for record in [
        {"status": "failed"},
        {"schema": "cloud-recovery-local.v1", "status": "passed", "aws_execution": True},
    ]:
        with pytest.raises(ValueError):
            render(json.dumps(record).encode())


def test_report_escapes_evidence_and_displays_observed_counts():
    record = {
        "schema": "cloud-recovery-local.v1",
        "status": "passed",
        "aws_execution": False,
        "scenarios": {
            "A_remote_ahead": {
                "status": "passed",
                "missing_restored_intents": 2,
                "detail": "<script>alert(1)</script>",
            },
            **{str(i): {"status": "passed"} for i in range(7)},
        },
        "original_intents": 5,
        "original_independent_effects": 5,
        "independent_final_effects": 6,
        "source": {"commit": "a" * 40},
        "elapsed_seconds": 1.234,
        "cleanup": {"remaining": 0},
    }
    page = render(json.dumps(record).encode())
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "Missing intents after early restore" in page and "1.234 seconds" in page
    assert "does not claim native RDS PITR" in page
