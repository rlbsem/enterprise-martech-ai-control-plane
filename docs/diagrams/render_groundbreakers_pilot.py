"""Re-render the first Groundbreakers diagram from its Mermaid source.

Run after npm ci. This pilot leaves the other five SVGs untouched.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
folder = ROOT / "docs/diagrams"
source = folder / "01-control-plane.mmd"
svg = source.with_suffix(".svg")
cli = ROOT / "node_modules/@mermaid-js/mermaid-cli/src/cli.js"
args = [
    "node",
    str(cli),
    "-i",
    str(source),
    "-o",
    str(svg),
    "-c",
    str(folder / "groundbreakers-mermaid.json"),
    "-t",
    "base",
    "-b",
    "#10101D",
]
if os.environ.get("PUPPETEER_CONFIG"):
    args += ["-p", os.environ["PUPPETEER_CONFIG"]]
subprocess.run(args, check=True)
manifest = folder / "manifest.json"
record = json.loads(manifest.read_text(encoding="utf-8"))
record["files"]["01-control-plane.mmd"] = {
    p.suffix.removeprefix("."): hashlib.sha256(
        p.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    for p in (source, svg)
}
manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print("Rendered Groundbreakers control-plane pilot from Mermaid source.")
