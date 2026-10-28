"""Validate the actual recorded acceptance; these tests never execute AWS calls."""

import json
from pathlib import Path

import pytest

from scripts.check_recovery import native_application_verdict


def recorded_bundle():
    folder = Path(__file__).resolve().parents[1] / "docs/evidence/cloud-recovery/native-application-2026-10-28"
    return {p.stem: json.loads(p.read_bytes()) for p in folder.glob("*.json")}


def test_complete_native_record_has_two_restores_and_nine_distinct_tasks():
    result = native_application_verdict(recorded_bundle())
    assert result["application_acceptance"] and result["restores_recorded"] == 2
    assert result["tasks_recorded"] == 9


@pytest.mark.parametrize("fault", ["blocked", "duplicate", "generation", "restore", "image"])
def test_native_record_rejects_altered_acceptance_observations(fault):
    bundle = recorded_bundle()
    result = bundle["execution"]["result"]
    if fault == "blocked":
        result["blocked"]["verification"]["safe_to_resume"] = True
    elif fault == "duplicate":
        result["resumed"]["duplicate_business_effects"] = 1
    elif fault == "generation":
        refs = bundle["database-generations"]["references"]
        refs["matching_control"] = refs["behind_control"]
    elif fault == "restore":
        bundle["aws-request-receipts"] = [r for r in bundle["aws-request-receipts"]
                                          if r["operation"] != "RestoreDBInstanceToPointInTime"]
    else:
        stopped = next(r["task_observation"] for r in result["records"]
                       if r.get("task_observation", {}).get("last_status") == "STOPPED")
        stopped["containers"][0]["imageDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="native_"):
        native_application_verdict(bundle)
