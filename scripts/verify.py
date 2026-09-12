"""Run the complete PostgreSQL/HTTP suite and publish evidence only after success."""

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import psycopg
import controlplane

ROOT = Path(__file__).resolve().parents[1]


def source_hashes():
    files = sorted(p for folder in ("src", "tests", "scripts", ".github") for p in (ROOT / folder).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".sql", ".yml"))
    files += [ROOT / "pyproject.toml", ROOT / "requirements.lock", ROOT / "compose.yaml", ROOT / "Dockerfile"]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def main():
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "docs/evidence"
    hashes = source_hashes()
    installed = Path(controlplane.__file__).parent
    for source in (ROOT / "src/controlplane").iterdir():
        if source.suffix in (".py", ".sql") and source.read_bytes() != (installed / source.name).read_bytes():
            raise RuntimeError("Installed package differs from repository source; reinstall before validation")
    with tempfile.TemporaryDirectory(prefix="control-proof-") as temporary:
        stage = Path(temporary)
        env = {**os.environ, "EVIDENCE_DIR": str(stage)}
        subprocess.run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"], cwd=ROOT, check=True)
        subprocess.run([sys.executable, "-m", "pytest", "-q", "--junitxml", str(stage / "tests.xml")],
                       cwd=ROOT, env=env, check=True)
        suite = ET.parse(stage / "tests.xml").getroot().find("testsuite")
        with psycopg.connect(os.environ["TEST_ADMIN_DSN"]) as c:
            postgres = c.execute("SELECT version()").fetchone()[0]
        if hashes != source_hashes():
            raise RuntimeError("Source changed during validation; evidence not published")
        summary = {"executed_at": datetime.now(UTC).isoformat(), "python": platform.python_version(),
                   "platform": platform.platform(), "postgres": postgres,
                   "installation": "source" if installed.resolve() == (ROOT / "src/controlplane").resolve() else "installed_wheel",
                   "packages": {p: version(p) for p in ["psycopg", "fastapi", "httpx", "pytest", "hypothesis", "ruff"]},
                   "tests": int(suite.attrib["tests"]), "failures": int(suite.attrib["failures"]),
                   "errors": int(suite.attrib["errors"]), "skipped": int(suite.attrib["skipped"]),
                   "seconds": float(suite.attrib["time"]),
                   "github_actions": ("running_in_actions_workflow_result_not_yet_known"
                                      if os.environ.get("GITHUB_ACTIONS") == "true" else "not_executed"),
                   "container_execution": "not_claimed_by_this_native_verifier", "source_sha256": hashes}
        (stage / "verification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        names = ["no-regress", "consent-race", "unknown-result", "competing-workers", "process-death"]
        cases = {n: json.loads((stage / f"{n}.json").read_text()) for n in names}
        report = ["# Executed control-plane proof", "",
                  f"{summary['tests']} tests; {summary['failures']} failures; {summary['skipped']} skipped. "
                  f"Real PostgreSQL and separate HTTP server processes. Generated {summary['executed_at']}.", "",
                  "| Scenario | Observed result |", "|---|---|",
                  f"| No-Regress | Opportunity -> MQL refused; final {cases['no-regress']['final']} |",
                  f"| Consent revoked after approval | {cases['consent-race']['execution']}; {cases['consent-race']['messages_sent']} messages |",
                  f"| Response lost after commit | {cases['unknown-result']['worker_attempts']} worker attempts; "
                  f"{cases['unknown-result']['remote_business_effects']} remote effect; reconciled success |",
                  f"| Eight competing workers | {cases['competing-workers']['claimed']} claim; 1 effect |",
                  f"| Worker process killed after remote commit | {cases['process-death']['final']}; 1 effect |", "",
                  "The JSON cases retain IDs, decisions and receipts. `verification.json` records source hashes and runtime versions. "
                  "`tests.xml` records every test. No GitHub Actions success, container execution, external SaaS or cloud deployment is claimed.", ""]
        (stage / "report.md").write_text("\n".join(report), encoding="utf-8")
        target.mkdir(parents=True, exist_ok=True)
        for p in stage.iterdir():
            (target / p.name).write_bytes(p.read_bytes())
        print("\n".join(report))


if __name__ == "__main__":
    main()
