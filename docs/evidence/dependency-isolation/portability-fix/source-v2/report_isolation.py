"""Publish only explicitly selected sanitized JSON observations and derived arithmetic."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil

from check_isolation import check


def percentiles(values):
    values = sorted(values)
    return {
        f"p{p}": values[max(0, math.ceil(len(values) * p / 100) - 1)] if values else None
        for p in (50, 95, 99)
    }


def summarize(record):
    result = check(record)
    result["destinations"] = {}
    for name, lane in record["destinations"].items():
        jobs = [j for j in record["jobs"] if j["destination"] == name]
        offers = [a for a in record["admissions"] if a["destination"] == name]
        first = {}
        for req in lane["requests"]:
            if req["method"] == "POST":
                first[req["key"]] = min(first.get(req["key"], float("inf")), req["at"])
        terminal = [
            1000 * (float(j["completed_at"]) - float(j["created_at"])) for j in jobs if j["completed_at"]
        ]
        queue = [1000 * (first[j["id"]] - float(j["created_at"])) for j in jobs if j["id"] in first]
        outcomes = Counter(j["status"] for j in jobs)
        result["destinations"][name] = {
            "offered": len(offers),
            "accepted": len(jobs),
            "rejected": sum(a["status"] == "overloaded" for a in offers),
            "terminal_outcomes": dict(outcomes),
            "never_dispatched": sum(j["id"] not in first for j in jobs),
            "admission_ms_all_offers": percentiles([a["latency_ms"] for a in offers]),
            "time_to_first_dispatch_ms": percentiles(queue),
            "time_to_terminal_ms_all_admitted": percentiles(terminal),
            "unfinished": sum(j["status"] not in ("succeeded", "blocked", "dead") for j in jobs),
            "useful_effects_per_second": len(lane["effects"]) / record["protocol"]["duration_seconds"],
            "request_methods": dict(Counter(r["method"] for r in lane["requests"])),
            "http_status": dict(Counter(str(r["status"]) for r in lane["requests"])),
            "requests_per_effect": len(lane["requests"]) / len(lane["effects"]) if lane["effects"] else None,
            "observed_queue_age_seconds": percentiles(
                [
                    float(q["oldest_age"])
                    for s in record["samples"]
                    for q in s["queues"]
                    if q["destination"] == name
                ]
            ),
        }
    peaks = {}
    for sample in record["samples"]:
        for proc in sample["application_processes"]:
            peaks[proc["pid"]] = max(peaks.get(proc["pid"], 0), proc["cpu_seconds"])
    result["resources"] = {
        "application_cpu_seconds_sampled": sum(peaks.values()),
        "maximum_sampled_application_rss_bytes": max(
            sum(p["rss_bytes"] for p in s["application_processes"]) for s in record["samples"]
        ),
        "maximum_cluster_connections": max(s["database_connections"] for s in record["samples"]),
        "scope": "1-second samples; application harness/workers/receivers only; PostgreSQL CPU/memory excluded; "
        "cluster connections can include concurrent tests; unsampled peaks and final CPU tails excluded",
    }
    result["latency_definition"] = (
        "Nearest-rank percentiles. Terminal latency includes all accepted outcomes; "
    )
    result["latency_definition"] += (
        "queue latency counts dispatched jobs and separately reports never-dispatched jobs. "
    )
    result["latency_definition"] += (
        "Queue-age samples describe the oldest pending job, not per-person business latency."
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("fresh publication directory required")
    args.output.mkdir(parents=True)
    summaries = []
    for i, run in enumerate(args.runs):
        folder = args.output / f"run-{i}"
        folder.mkdir()
        raw = (run / "observations.json").read_bytes()
        record = json.loads(raw)
        summary = summarize(record)
        summaries.append(summary)
        (folder / "observations.json.gz").write_bytes(gzip.compress(raw, mtime=0))
        shutil.copyfile(run / "protocol.json", folder / "protocol.json")
        (folder / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    (args.output / "summary.json").write_text(
        json.dumps(summaries, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    rows = [
        "# Executed dependency-isolation acceptance",
        "",
        "Local synthetic PostgreSQL/HTTP execution. Each run offers 5,000 proposals over a declared five-minute window. "
        "Admission rejection is reported separately from accepted work. No AWS or native vendor execution.",
        "",
        "| Run | Accepted | Rejected | Effects | Healthy successes during fault window | Budget violations |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for i, s in enumerate(summaries):
        rows.append(
            f"| {i} | {s['accepted']} | {s['rejected']} | {s['effects']} | {s['healthy_successes_during_fault']} | {s['budget_violations']} |"
        )
    rows += [
        "",
        "Each run folder contains the pre-traffic source/protocol seal, independently checked summary and "
        "compressed raw observations. These are actual receiver requests, independent effect ledgers, queue samples, "
        "resource observations and alert receipts. No screenshots or model responses are generated by this report.",
        "",
        "The first run is healthy; subsequent runs exercise the declared 429/503 outage and recovery. "
        "Percentile populations, rejected/unfinished counts and resource exclusions are explicit in each summary. "
        "These observations do not establish production capacity or an enterprise SLA.",
    ]
    (args.output / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    manifest = {
        p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(args.output.rglob("*"))
        if p.is_file()
    }
    (args.output / "artifact-manifest.json").write_text(
        json.dumps({"format": "raw SHA-256", "files": manifest}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
