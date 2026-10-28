"""Resolve old workload source bindings without relabeling old runs as new code."""

import hashlib
import json


def historical_source(root, name):
    for folder in ("prior-runtime", "diagnostic-source-archive"):
        manifest = json.loads((root / "docs/evidence/cloud-recovery" / folder / "manifest.json").read_bytes())
        assert manifest["source_revision"] == "0f934d6b14f2cb23fc88f4c33a65ba3034133847"
        entry = manifest["files"].get(name)
        if entry:
            raw = (root / entry["path"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"], name
            return raw
    return (root / name).read_bytes()
