"""Offline postprocessing of recorded observations; no workflow or service calls."""

from collections import Counter
from datetime import UTC, datetime
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    rows = [
        "# Recorded incident timelines",
        "",
        "Protocol times identify scheduled injection boundaries. Receiver journals identify actual "
        "service behavior; queue samples and independent ledger comparisons identify drain and reconciliation. "
        "The backlog alert can already be firing before the provider fault because offered load exceeds capacity. "
        "All effects are local and synthetic.",
        "",
    ]
    folders = sorted((HERE / "runs").glob("run-*"))
    if not folders:
        raise ValueError("No recorded runs")
    for folder in folders:
        record = json.loads(gzip.decompress((folder / "observations.json.gz").read_bytes()))
        p = record["protocol"]
        start = p["fault_start"] - 0.1 * p["duration_seconds"]
        events = []
        counts = Counter((r["destination"], r["window_id"]) for r in record["grants"])
        full = [key for key, n in counts.items() if n == p["requests_per_second"]]
        if full:
            reached = min(
                max(
                    float(g["issued_at"])
                    for g in record["grants"]
                    if (g["destination"], g["window_id"]) == key
                )
                for key in full
            )
            events.append(
                (
                    reached,
                    "Shared request budget reached",
                    f"{p['requests_per_second']} issued permits in a fixed epoch-second bucket",
                )
            )
        affected = record["destinations"]["affected"]["requests"]
        failures = [r for r in affected if r["status"] in (429, 503)]
        if failures:
            events.append((p["fault_start"], "Fault scheduled", p["fault"]))
            events.append(
                (
                    min(r["at"] for r in failures),
                    "Affected request rejected",
                    f"{sum(r['status'] == 429 for r in failures)} HTTP 429 and "
                    f"{sum(r['status'] == 503 for r in failures)} HTTP 503 responses",
                )
            )
            healthy = [
                r
                for r in record["destinations"]["healthy"]["requests"]
                if r["method"] == "POST"
                and r["status"] in (200, 201)
                and p["fault_start"] <= r["at"] < p["fault_end"]
            ]
            if healthy:
                events.append(
                    (
                        healthy[0]["at"],
                        "Healthy work continues",
                        f"{len(healthy)} successful healthy POSTs during the provider fault",
                    )
                )
            events.append(
                (
                    p["fault_end"],
                    "Fault injection ends",
                    "Scheduled boundary; recovery still needs observation",
                )
            )
            successes = [
                r
                for r in affected
                if r["method"] == "POST" and r["status"] in (200, 201) and r["at"] >= p["fault_end"]
            ]
            if successes:
                events.append(
                    (
                        min(r["at"] for r in successes),
                        "Affected service accepts work again",
                        "Independently journaled successful POST",
                    )
                )
        for alert in record["destinations"]["healthy"].get("alerts", []):
            events.append(
                (
                    alert["at"],
                    "Backlog alert " + alert["state"],
                    "Authenticated local receiver; exact content independently read back",
                )
            )
        last_offer = max(a["at"] for a in record["admissions"])
        drained = [s["at"] for s in record["samples"] if s["at"] >= last_offer and not s["queues"]]
        if drained:
            events.append((min(drained), "Admitted backlog drained", "No pending jobs in the queue sample"))
        verified = datetime.fromisoformat(record["reconciliation"]["observed_at"]).timestamp()
        events.append(
            (
                verified,
                "Independent ledgers reconciled",
                f"safe_to_resume={str(record['reconciliation']['safe_to_resume']).lower()}; "
                "exact healthy, affected and legacy reader scope",
            )
        )
        rows += [
            f"## {folder.name}: {p['fault']}",
            "",
            "| UTC | Seconds from start | Event | Evidence |",
            "|---|---:|---|---|",
        ]
        for at, event, detail in sorted(events):
            stamp = datetime.fromtimestamp(at, UTC).isoformat(timespec="milliseconds")
            rows.append(f"| {stamp} | {at - start:.3f} | {event} | {detail} |")
        relative = folder.relative_to(HERE).as_posix()
        rows += [
            "",
            f"[Observations]({relative}/observations.json.gz) · "
            f"[Protocol]({relative}/protocol.json) · [Results]({relative}/summary.json)",
            "",
        ]
    target = HERE / "timeline.md"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    files = [Path(__file__), target] + list((HERE / "runs").glob("run-*/observations.json.gz"))
    manifest = {p.relative_to(HERE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (HERE / "timeline-manifest.json").write_text(
        json.dumps({"format": "raw SHA-256", "files": manifest}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
