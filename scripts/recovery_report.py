"""Render an actual local recovery record as a standalone, read-only operator report."""

import argparse
import hashlib
import html
import json
from pathlib import Path


def render(raw):
    record = json.loads(raw)
    if record.get("schema") != "cloud-recovery-local.v1" or record.get("status") != "passed":
        raise ValueError("Executed, successful local acceptance record required")
    if record.get("aws_execution") is not False or len(record["scenarios"]) != 8:
        raise ValueError("Exact local evidence boundary required")
    escape = html.escape
    cards = []
    titles = {
        "A_remote_ahead": "Historical restore stays closed",
        "B_lost_acknowledgment": "Lost acknowledgment reconciled",
        "C_killed_worker": "Terminated worker safely replaced",
        "D_mutation_conflict": "Conflicting content rejected",
        "E_missing_acknowledged_receipt": "Missing receipt blocks resumption",
        "F_current_authority": "Current permission required",
        "G_safe_resumption": "Matching state resumes safely",
        "H_stale_approval": "Stale approval rejected",
    }
    for key, row in sorted(record["scenarios"].items()):
        cards.append(
            '<article><p class="eyebrow">Executed scenario '
            + escape(key.split("_")[0])
            + "</p><h2>"
            + escape(titles.get(key, key))
            + '</h2><p class="status">'
            + escape(row["status"].upper())
            + "</p><details><summary>Inspect persisted evidence</summary><pre>"
            + escape(json.dumps(row, indent=2, default=str))
            + "</pre></details></article>"
        )
    metrics = [
        ("Original control intents", record["original_intents"]),
        ("Independent effects before recovery", record["original_independent_effects"]),
        (
            "Missing intents after early restore",
            record["scenarios"]["A_remote_ahead"]["missing_restored_intents"],
        ),
        ("Effects after one authorized new action", record["independent_final_effects"]),
    ]
    tiles = "".join(
        "<div><strong>" + escape(str(v)) + "</strong><span>" + escape(k) + "</span></div>" for k, v in metrics
    )
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Control Plane | Recovery acceptance</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;color:#e8eef7;background:#0b1220;line-height:1.55}
*{box-sizing:border-box}body{margin:0}main{max-width:1200px;margin:auto;padding:38px 24px 64px}
.eyebrow{color:#9bbacb;text-transform:uppercase;letter-spacing:.12em;font-size:.76rem;margin:0 0 8px}
h1{font-size:clamp(2rem,5vw,3.4rem);line-height:1.12;max-width:850px;margin:12px 0 20px}h2{font-size:1.15rem;margin:6px 0}
.intro{color:#c1ccda;max-width:850px;font-size:1.1rem}.boundary{border-left:3px solid #60c5bd;background:#142331;padding:16px 20px;margin:28px 0}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}.metrics div,article{border:1px solid #33445b;border-radius:12px;background:#142033;padding:20px}
.metrics strong{display:block;font-size:2rem;color:#8ee0d7}.metrics span{display:block;color:#c1ccda;font-size:.88rem}
.cases{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.status{display:inline-block;background:#183a3c;color:#a4ece1;border-radius:4px;padding:3px 9px;font-size:.75rem;font-weight:700}
summary{cursor:pointer;color:#c1d9ed;padding:8px 0}summary:focus-visible{outline:2px solid #8ee0d7;outline-offset:4px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1220;padding:14px;font-size:.78rem;max-height:480px;overflow:auto}
footer{color:#a9b9ca;font-size:.85rem;margin-top:30px;overflow-wrap:anywhere}@media(max-width:720px){.metrics,.cases{grid-template-columns:1fr 1fr}.cases{grid-template-columns:1fr}main{padding:24px 16px}}
</style><main><p class="eyebrow">Enterprise control plane · executed operator proof</p>
<h1>A restored database is not permission to resume.</h1>
<p class="intro">An early control snapshot misses independently committed effects and stays closed. A later matching snapshot requires fresh authority, an exact approval and another live comparison before work can resume.</p>
<div class="boundary"><strong>Recorded local PostgreSQL / HTTP execution. Synthetic records only.</strong><br>
Actual database copies, separate receiver storage and terminated worker processes. This report is read-only. It does not claim native RDS PITR, a production deployment, or guaranteed RPO/RTO.</div>
<section class="metrics" aria-label="Observed counts">"""
        + tiles
        + """</section>
<section class="cases" aria-label="Executed recovery scenarios">"""
        + "".join(cards)
        + """</section><footer>
Evidence SHA-256: """
        + hashlib.sha256(raw).hexdigest()
        + """<br>Source commit: """
        + escape(record["source"]["commit"])
        + """<br>
Measured harness duration: """
        + escape(f"{record['elapsed_seconds']:.3f}")
        + """ seconds. This includes isolated fixture preparation and negative scenarios; it is not a production recovery objective.<br>
Cleanup: """
        + escape(json.dumps(record["cleanup"]))
        + """</footer></main></html>"""
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Preserve existing reports; select a fresh output")
    args.output.write_text(render(args.record.read_bytes()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
