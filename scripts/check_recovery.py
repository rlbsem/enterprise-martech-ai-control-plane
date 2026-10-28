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
    return {
        "status": "passed",
        "artifact_hashes": len(manifest["files"]),
        "executed_source_files": len(record["source"]["sha256"]),
        "source_commit": commit,
        "local_scenarios": 8,
        "native_execution_claimed": False,
    }


if __name__ == "__main__":
    print(json.dumps(check()))
