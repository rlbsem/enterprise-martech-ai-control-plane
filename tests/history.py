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
    # The Ohio deployment configuration changes the live release manifest validator.
    # Historical dependency-isolation workloads must still resolve the exact
    # original release.py bytes captured at their source revision, not current
    # code. The stored SHA-256 comes from the preserved original run record.
    if name == "src/controlplane/release.py":
        archive = (root / "docs/evidence/dependency-isolation/arrival-review/source/release.py").read_bytes()
        assert hashlib.sha256(archive).hexdigest() == (
            "1dfd2b86c27e119c3a655c0286eeed43af0825f64c60eaf24690e0306c1a2e84"
        ), name
        return archive
    return (root / name).read_bytes()
