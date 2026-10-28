"""Verify immutable recovery artifacts and the exact Git source actually executed."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs/evidence/cloud-recovery"


def check_native_attempt():
    """Validate the recorded failed attempt without turning it into acceptance."""
    folder = PACKAGE / "native-2026-10-28"
    record = json.loads((folder / "execution.json").read_bytes())
    result = record["result"]
    if record["schema"] != "cloud-recovery-native.v1" or result["status"] != "FAILED_NATIVE_APPLICATION_ACCEPTANCE":
        raise ValueError("failed_native_attempt_must_stay_failed")
    if result["runtime_reason"] != "Stopped task identity/image/exit mismatch":
        raise ValueError("native_failure_observation_changed")
    if [r["task"] for r in result["records"]] != ["bootstrap", "baseline", "control", "verify"]:
        raise ValueError("native_task_sequence_changed")
    original = result["records"][-1]["result"]
    if original["safe_to_resume"] is not True or original["local_intents"] != 5 or original["remote_effects"] != 5 or original["write_denials"] != 2:
        raise ValueError("native_source_comparison_changed")
    requests = json.loads((folder / "aws-request-receipts.json").read_bytes())
    restores = [r for r in requests if r["operation"] == "RestoreDBInstanceToPointInTime"]
    if len(restores) != 1 or restores[0]["http_status"] != 200 or not restores[0]["request_id"]:
        raise ValueError("native_restore_request_observation_changed")
    checkpoints = json.loads((folder / "checkpoints.json").read_bytes())
    available = [r for r in checkpoints if r["phase"] == "behind-rds-available"]
    if len(available) != 1 or available[0]["request_id"] != restores[0]["request_id"] or available[0]["application_recovery"] != "not_yet_verified":
        raise ValueError("database_availability_is_not_application_acceptance")
    binding = result["execution_binding"]
    source = subprocess.check_output(["git", "show", binding["controller_source_commit"] + ":src/controlplane/native_application.py"], cwd=ROOT)
    if hashlib.sha256(source).hexdigest() != binding["controller_sha256"]:
        raise ValueError("native_controller_source_binding_failure")
    image = json.loads((folder / "image-verification.json").read_bytes())
    if image["source_commit"] != binding["application_image_source_commit"] or image["image_manifest_digest"] != binding["application_image_manifest_digest"] or image["all_oci_blobs_verified"] is not True:
        raise ValueError("native_image_source_binding_failure")
    teardown = json.loads((folder / "teardown.json").read_bytes())
    if teardown["runtime_active_resources_absent"] is not True or teardown["guard_active_resources_absent"] is not True or teardown["billing_clearance"] is not False:
        raise ValueError("native_cleanup_boundary_changed")
    return {"status": result["status"], "restores_recorded": len(restores), "application_acceptance": False}


def native_application_verdict(bundle):
    """Check the recorded run, not AWS access or a new cloud execution."""
    execution = bundle["execution"]
    r = execution["result"]
    if (execution["schema"] != "cloud-recovery-native-application.v1"
            or r["status"] != "NATIVE_APPLICATION_RECOVERY_ACCEPTANCE_PASSED"
            or r["synthetic"] is not True or r["full_production_application_acceptance"] is not False
            or r["maximum_concurrent_instances"] != 2):
        raise ValueError("native_application_scope_or_verdict_invalid")
    blocked, resumed, final = r["blocked"], r["resumed"], r["independent_final"]
    if (blocked["stage"] != "behind_blocked" or blocked["worker_claimed"] is not False
            or blocked["verification"]["local_intents"] != 3
            or blocked["verification"]["remote_effects"] != 5
            or blocked["verification"]["safe_to_resume"] is not False
            or [f["reason"] for f in blocked["verification"]["findings"]].count("remote_ahead_missing_intent") != 2):
        raise ValueError("native_blocked_recovery_observation_invalid")
    if (resumed["stage"] != "matching_resumed"
            or resumed["stale_approval_refusal"] != "recovery_evidence_changed"
            or resumed["old_completion_accepted"] is not False
            or type(resumed["duplicate_business_effects"]) is not int
            or resumed["duplicate_business_effects"] != 0
            or resumed["independent_database_identity_unchanged"] is not True
            or resumed["revoked_action"]["decision"] != "deny"
            or resumed["revoked_action"]["reason"] != "consent_missing_or_revoked"
            or final["local_intents"] != 6 or final["remote_effects"] != 6
            or final["safe_to_resume"] is not True or final["findings"] or final["write_denials"] != 2):
        raise ValueError("native_safe_resumption_observation_invalid")
    tasks = [v for v in r["records"] if "result" in v]
    if [(t["task"], t["exit_code"]) for t in tasks] != [
        ("bootstrap", 0), ("receipt", 0), ("baseline", 0), ("control", 0), ("verify", 0),
        ("receipt", 0), ("verify", 2), ("receipt", 0), ("verify", 0),
    ] or len({t["task_reference"] for t in tasks}) != 9:
        raise ValueError("native_single_experiment_task_sequence_invalid")
    preflight = tasks[1]["result"]
    if (preflight["stage"] != "source_preflight_passed"
            or preflight["recovery_operations_rolled_back"] is not True
            or preflight["write_denials"] != 2
            or preflight["comparison"]["local_intents"] != 0
            or preflight["comparison"]["remote_effects"] != 0):
        raise ValueError("native_source_preflight_invalid")
    binding, image = r["execution_binding"], bundle["image-verification"]
    if (not re.fullmatch("[a-f0-9]{40}", binding["controller_source_commit"])
            or binding["controller_source_commit"] != image["source_commit"]
            or binding["application_image_source_commit"] != image["source_commit"]
            or binding["application_image_manifest_digest"] != image["image_manifest_digest"]
            or image["all_oci_blobs_verified"] is not True
            or bundle["image-upload"]["registry_exact_manifest_readback"] is not True
            or bundle["image-upload"]["ecr_describe_digest_verified"] is not True):
        raise ValueError("native_application_image_binding_invalid")
    stopped = [t["task_observation"] for t in r["records"]
               if t.get("task_observation", {}).get("last_status") == "STOPPED"]
    if len(stopped) != 9 or {t["task_reference"] for t in stopped} != {t["task_reference"] for t in tasks}:
        raise ValueError("native_stopped_task_observations_incomplete")
    for t in stopped:
        if (len(t["containers"]) != 1 or t["containers"][0]["exitCode"] != t["expected_exit"]
                or t["containers"][0]["imageDigest"] != image["image_manifest_digest"]
                or t["log_read_error"] or t["logs_truncated"]):
            raise ValueError("native_stopped_task_image_or_exit_invalid")
    api = bundle["aws-request-receipts"]
    restores = [a for a in api if a["operation"] == "RestoreDBInstanceToPointInTime"]
    available = [c for c in bundle["checkpoints"] if c["phase"].endswith("rds-available")]
    if (len(restores) != 2 or len(available) != 2
            or any(a["http_status"] != 200 or not a["request_id"] for a in restores)
            or {a["request_id"] for a in restores} != {a["request_id"] for a in available}
            or {a["restore_point"] for a in available} != {r["first_recovery_point"], r["matching_recovery_point"]}):
        raise ValueError("native_two_restore_request_bindings_invalid")
    refs = bundle["database-generations"]["references"]
    if len(refs) != 4 or len(set(refs.values())) != 4:
        raise ValueError("native_database_generations_not_distinct")
    for observation in bundle["database-generations"]["observations"]:
        receipt = [d for d in observation["instances"] if d["role"] == "independent_receipts"]
        if (observation["concurrent_instances"] != 2 or len(receipt) != 1
                or receipt[0]["physical_reference_sha256"] != refs["independent_receipts"]):
            raise ValueError("native_independent_receipt_generation_changed")
    if {a["database_generation_reference"] for a in available} != {refs["behind_control"], refs["matching_control"]}:
        raise ValueError("native_restore_generation_binding_invalid")
    return {"application_acceptance": True, "restores_recorded": 2, "tasks_recorded": 9,
            "controller_commit": binding["controller_source_commit"], "controller_sha256": binding["controller_sha256"]}


def check():
    manifest = json.loads((PACKAGE / "artifact-manifest.json").read_bytes())
    actual_files = {
        p.relative_to(PACKAGE).as_posix()
        for p in PACKAGE.rglob("*")
        if p.is_file() and p.name != "artifact-manifest.json"
    }
    if actual_files != set(manifest["files"]):
        raise ValueError("recovery_manifest_file_set_mismatch")
    for name, expected in manifest["files"].items():
        path = (PACKAGE / name).resolve()
        if (
            not path.is_relative_to(PACKAGE.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("recovery_artifact_integrity_failure: " + name)
    record = json.loads((PACKAGE / "local-2026-10-27/acceptance.json").read_bytes())
    if (
        record["schema"] != "cloud-recovery-local.v1"
        or record["status"] != "passed"
        or record["aws_execution"] is not False
        or len(record["scenarios"]) != 8
    ):
        raise ValueError("recovery_execution_boundary_failure")
    commit = record["source"]["commit"]
    if not re.fullmatch("[a-f0-9]{40}", commit):
        raise ValueError("exact_recovery_source_required")
    for name, expected in record["source"]["sha256"].items():
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError("source_path_escape")
        raw = subprocess.check_output(["git", "show", commit + ":" + name], cwd=ROOT)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("executed_source_binding_failure: " + name)
    for provenance in PACKAGE.glob("local-*/browser-*/provenance.json"):
        observed = json.loads(provenance.read_bytes())
        for key in ("source_report", "source_record"):
            raw = (ROOT / observed[key]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != observed[key + "_sha256"]:
                raise ValueError("browser_source_binding_failure")
        for observation in observed["observations"]:
            raw = (provenance.parent / observation["screenshot"]).read_bytes()
            if (
                observation["status"] != "passed"
                or hashlib.sha256(raw).hexdigest() != observation["screenshot_sha256"]
            ):
                raise ValueError("browser_capture_integrity_failure")
    source_bindings = len(record["source"]["sha256"])
    for path in PACKAGE.glob("*/execution-binding.json"):
        binding = json.loads(path.read_bytes())
        raw = (path.parent / "verification.json").read_bytes()
        verification = json.loads(raw)
        if hashlib.sha256(raw).hexdigest() != binding["verification_sha256"]:
            raise ValueError("verification_record_integrity_failure")
        if not re.fullmatch("[a-f0-9]{40}", binding["source_commit"]):
            raise ValueError("verification_source_commit_required")
        for name, expected in verification["source_sha256"].items():
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("verification_source_path_escape")
            source = subprocess.check_output(["git", "show", binding["source_commit"] + ":" + name], cwd=ROOT)
            if hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest() != expected:
                raise ValueError("verification_source_binding_failure: " + name)
        source_bindings += len(verification["source_sha256"])
    regression = json.loads((PACKAGE / "regression-2026-10-27/verification.json").read_bytes())
    if regression["tests"] != 368 or any(regression[k] for k in ("failures", "errors", "skipped")):
        raise ValueError("complete_regression_record_required")
    for folder in ("diagnostic-source-archive", "native-controller-source-archive"):
        archive = json.loads((PACKAGE / folder / "manifest.json").read_bytes())
        for name, entry in archive["files"].items():
            source = subprocess.check_output(["git", "show", archive["source_revision"] + ":" + name], cwd=ROOT)
            if hashlib.sha256(source).hexdigest() != entry["sha256"] or (ROOT / entry["path"]).read_bytes() != source:
                raise ValueError("historical_diagnostic_source_binding_failure")
            source_bindings += 1
    native = check_native_attempt()
    latest = None
    folder = PACKAGE / "native-application-2026-10-28"
    if folder.exists():
        bundle = {p.stem: json.loads(p.read_bytes()) for p in folder.glob("*.json")}
        latest = native_application_verdict(bundle)
        teardown = bundle.get("teardown", {})
        if (teardown.get("runtime_active_resources_absent") is not True
                or teardown.get("guard_active_resources_absent") is not True
                or teardown.get("billing_clearance") is not False):
            raise ValueError("native_application_cleanup_observation_required")
        source = subprocess.check_output(
            ["git", "show", latest["controller_commit"] + ":src/controlplane/native_application.py"], cwd=ROOT
        )
        if hashlib.sha256(source).hexdigest() != latest["controller_sha256"]:
            raise ValueError("native_application_controller_source_binding_failure")
        source_bindings += 1
    return {
        "status": "passed",
        "artifact_hashes": len(manifest["files"]),
        "executed_source_files": len(record["source"]["sha256"]),
        "total_pinned_source_bindings": source_bindings,
        "regression_tests_recorded": regression["tests"],
        "source_commit": commit,
        "local_scenarios": 8,
        "native_execution_recorded": True,
        "native_acceptance_passed": bool(latest and latest["application_acceptance"]),
        "native_restores_recorded": latest["restores_recorded"] if latest else native["restores_recorded"],
        "historical_native_attempt_acceptance_passed": native["application_acceptance"],
        "historical_native_attempt_restores_recorded": native["restores_recorded"],
    }


if __name__ == "__main__":
    print(json.dumps(check()))
