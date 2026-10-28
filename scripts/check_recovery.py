"""Verify immutable recovery artifacts and the exact Git source actually executed."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs/evidence/cloud-recovery"


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
    return {
        "status": "passed",
        "artifact_hashes": len(manifest["files"]),
        "executed_source_files": len(record["source"]["sha256"]),
        "total_pinned_source_bindings": source_bindings,
        "regression_tests_recorded": regression["tests"],
        "source_commit": commit,
        "local_scenarios": 8,
        "native_execution_claimed": False,
    }


if __name__ == "__main__":
    print(json.dumps(check()))
