"""Read-only checks over executed observations; no new workload, vendor or cloud execution."""

import gzip
import hashlib
import json
from pathlib import Path

from scripts.check_isolation import check


def test_recorded_runs_and_recovered_alerts():
    root = Path(__file__).resolve().parents[1]
    evidence = root / "docs/evidence/dependency-isolation"
    for manifest_name in ("runs/artifact-manifest.json", "timeline-manifest.json"):
        manifest_path = evidence / manifest_name
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, expected in manifest["files"].items():
            assert hashlib.sha256((manifest_path.parent / name).read_bytes()).hexdigest() == expected
    source_map = json.loads(
        (evidence / "portability-fix/historical-source-map.json").read_text(encoding="utf-8")
    )["files"]
    paths = sorted((evidence / "runs").glob("run-*/observations.json.gz"))
    assert len(paths) == 4
    for path in paths:
        record = json.loads(gzip.decompress(path.read_bytes()))
        assert check(record)["status"] == "passed"
        protocol = record["protocol"]
        assert protocol["duration_seconds"] == 300 and protocol["proposals"] == 5000
        for name, expected in protocol["source_sha256"].items():
            assert hashlib.sha256((root / source_map.get(name, name)).read_bytes()).hexdigest() == expected, (
                name
            )
        if protocol["fault"] == "healthy":
            continue
        lane = record["destinations"]["affected"]
        assert {429, 503}.issubset({r["status"] for r in lane["requests"]})
        alerts = record["destinations"]["healthy"]["alerts"]
        clear = alerts[-1]["at"]
        assert alerts[0]["state"] == "firing" and alerts[0]["at"] < protocol["fault_end"]
        assert alerts[-1]["state"] == "cleared" and clear >= protocol["fault_end"]
        # The service actually accepted work again before the alarm cleared;
        # the complete ledger oracle above independently verifies those effects.
        assert any(
            r["method"] == "POST" and r["status"] in (200, 201) and protocol["fault_end"] <= r["at"] <= clear
            for r in lane["requests"]
        )
