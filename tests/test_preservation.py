"""Historical execution records and diagrams retain their original bytes."""

import hashlib
import json
from pathlib import Path


def test_original_evidence_and_diagram_bytes_preserved():
    root = Path(__file__).resolve().parents[1]
    record = json.loads(
        (root / "docs/evidence/dependency-isolation/preserved-files.json").read_text(encoding="utf-8")
    )
    assert record["baseline_commit"] == "5af0642727b2f5c1c2d7759bcbfea7171ad131f6"
    assert record["files"]
    for name, expected in record["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name


def test_prior_acceptance_bytes_preserved_after_liveness_fix():
    root = Path(__file__).resolve().parents[1]
    record = json.loads(
        (root / "docs/evidence/dependency-isolation/portability-fix/preserved-prior-acceptance.json")
        .read_text(encoding="utf-8")
    )
    assert record["source_commit"] == "fdf631ccac9ae02064365befbb96640b69e4909c"
    assert len(record["files"]) == 32
    for name, expected in record["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
