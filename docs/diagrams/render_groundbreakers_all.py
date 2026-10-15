"""Render six Groundbreakers-branded diagrams from authoritative Mermaid sources.

This documentation-only renderer updates the checked-in SHA-256 manifest.
Use with the repository's pinned Mermaid CLI; never edit SVG output alone.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "docs/diagrams"
CLI = ROOT / "node_modules/@mermaid-js/mermaid-cli/src/cli.js"
if not CLI.is_file():
    raise SystemExit("Install pinned Mermaid CLI with npm ci before rendering.")

records = {}
sources = sorted(FOLDER.glob("*.mmd"))
if len(sources) != 6:
    raise SystemExit(f"Expected exactly 6 architecture diagram sources; found {len(sources)}")
for source in sources:
    svg = source.with_suffix(".svg")
    args = [
        "node", str(CLI), "-i", str(source), "-o", str(svg),
        "-c", str(FOLDER / "groundbreakers-mermaid.json"),
        "-b", "#10101D",
    ]
    if os.environ.get("PUPPETEER_CONFIG"):
        args += ["-p", os.environ["PUPPETEER_CONFIG"]]
    subprocess.run(args, check=True)
    records[source.name] = {
        p.suffix.removeprefix("."): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in (source, svg)
    }
manifest_path = FOLDER / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["files"] = records
manifest["renderer"] = "@mermaid-js/mermaid-cli 11.12.0"
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Regenerated {len(sources)} Groundbreakers-branded SVGs and checksums.")
