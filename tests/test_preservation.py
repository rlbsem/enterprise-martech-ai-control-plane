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
