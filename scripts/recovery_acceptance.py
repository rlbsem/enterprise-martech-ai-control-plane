"""Actual isolated local recovery acceptance; never calls AWS or restores a user database."""

import argparse
import hashlib
import json
import os
import subprocess
import traceback
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from controlplane.recovery_lab import execute_local

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Fresh directory, never overwritten")
    parser.add_argument("--confirm-isolated-postgres", action="store_true", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Preserve the prior run; choose a fresh output directory")
    admin = os.environ.get("CONTROL_RECOVERY_ADMIN_DSN", os.environ.get("TEST_ADMIN_DSN"))
    if not admin:
        raise ValueError("Set CONTROL_RECOVERY_ADMIN_DSN for a disposable local database ending _test")
    args.output.mkdir(parents=True)
    private = ROOT / ".local" / "recovery-runtime" / uuid4().hex
    before = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("src", "scripts", "tests")
        for p in (ROOT / folder).rglob("*")
        if p.is_file() and p.suffix in {".py", ".sql"} and "__pycache__" not in p.parts
    }
    try:
        result = execute_local(admin, private)
        if any(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h for p, h in before.items()):
            raise ValueError("source_changed_during_acceptance")
        result["source"] = {
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, encoding="utf-8"
            ).strip(),
            "sha256": before,
            "note": "File hashes identify the exact tested source; Git revision alone does not identify uncommitted edits",
        }
        result["observed_at"] = datetime.now(UTC).isoformat()
        (args.output / "acceptance.json").write_text(
            json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
        )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "scenarios": len(result["scenarios"]),
                    "final_effects": result["independent_final_effects"],
                    "cleanup": result["cleanup"],
                }
            )
        )
    except Exception as exc:
        failure = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "sqlstate": getattr(exc, "sqlstate", None),
            "locations": [
                {"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                for f in traceback.extract_tb(exc.__traceback__)
            ],
            "aws_execution": False,
            "raw_logs": "Private .local runtime logs; not public evidence",
        }
        (args.output / "failure.json").write_text(
            json.dumps(failure, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        raise


if __name__ == "__main__":
    main()
