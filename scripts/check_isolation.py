"""Independent result checker: receiver traffic and ledgers, not worker success counters."""

import argparse
import gzip
import hashlib
from collections import Counter
import json
import math
from pathlib import Path


def rate_observations(record):
    """Compare database-assigned permit windows with receiver UTC arrival buckets.

    Arrival counts are observations, not a second acceptance contract. A permit
    from an earlier window can arrive alongside the next window's requests.
    """
    limit = record["protocol"]["requests_per_second"]
    grants = {grant["token"]: grant for grant in record["grants"]}
    destinations = {}
    configured = set(record["destinations"]) | {grant["destination"] for grant in grants.values()}
    for destination in sorted(configured):
        lane = record["destinations"].get(destination, {"requests": []})
        issuance = Counter(
            grant["window_id"] for grant in grants.values() if grant["destination"] == destination
        )
        arrivals = {}
        for request in lane["requests"]:
            arrivals.setdefault(math.floor(request["at"]), []).append(request)
        over = []
        for second, requests in sorted(arrivals.items()):
            if len(requests) > limit:
                windows = Counter(grants[request["permit"]]["window_id"] for request in requests)
                over.append({
                    "receiver_utc_epoch_second": second,
                    "requests": len(requests),
                    "permit_windows": [
                        {"window_id": window, "requests": count}
                        for window, count in sorted(windows.items())
                    ],
                })
        destinations[destination] = {
            "permit_issuance_max_per_assigned_window": max(issuance.values(), default=0),
            "permit_issuance_buckets_above_limit": sum(count > limit for count in issuance.values()),
            "receiver_arrival_max_per_utc_second": max(map(len, arrivals.values()), default=0),
            "receiver_arrival_buckets_above_limit": len(over),
            "receiver_arrival_over_limit_buckets": over,
        }
    return {
        "limit_per_destination_per_second": limit,
        "permit_window_definition": "database-assigned grant.window_id; acceptance contract",
        "arrival_window_definition": "floor(receiver request.at), UTC epoch seconds; observational only",
        "permit_issuance_max_per_assigned_window": max(
            (lane["permit_issuance_max_per_assigned_window"] for lane in destinations.values()), default=0
        ),
        "permit_issuance_buckets_above_limit": sum(
            lane["permit_issuance_buckets_above_limit"] for lane in destinations.values()
        ),
        "receiver_arrival_max_per_utc_second": max(
            (lane["receiver_arrival_max_per_utc_second"] for lane in destinations.values()), default=0
        ),
        "receiver_arrival_buckets_above_limit": sum(
            lane["receiver_arrival_buckets_above_limit"] for lane in destinations.values()
        ),
        "destinations": destinations,
    }


def check(record):
    if not __debug__:
        raise RuntimeError("optimized Python cannot validate acceptance")
    protocol = record["protocol"]
    grants = {p["token"]: p for p in record["grants"]}
    assert len(grants) == len(record["grants"]), "duplicate permit"
    buckets = Counter((p["destination"], p["window_id"]) for p in grants.values())
    assert max(buckets.values(), default=0) <= protocol["requests_per_second"], "permit issuance budget exceeded"
    sent = []
    receipts = {}
    for destination, lane in record["destinations"].items():
        for request in lane["requests"]:
            assert request["permit"] in grants, "unpermitted request"
            assert grants[request["permit"]]["destination"] == destination, "destination substitution"
            assert grants[request["permit"]]["kind"] == (
                "receipt" if request["method"] == "GET" else "dispatch"
            )
            sent.append(request["permit"])
        for receipt in lane["effects"]:
            assert receipt["key"] not in receipts, "duplicate business identity across receivers"
            receipts[receipt["key"]] = (destination, receipt)
    assert len(sent) == len(set(sent)), "permit replay"
    for job in record["jobs"]:
        if "claims" in job:
            assert job["claims"] >= job["attempts"] >= 1, "invalid scheduler deferral accounting"
        remote = receipts.pop(job["id"], None)
        if job["status"] == "succeeded":
            assert remote and remote[0] == job["destination"], "wrong/missing destination receipt"
            assert remote[1]["hash"] == job["hash"], "payload mismatch"
            assert remote[1]["receipt"] == job["receipt"], "receipt mismatch"
        else:
            assert remote is None, "unreconciled remote effect"
        assert job["status"] in ("succeeded", "blocked", "dead"), "unfinished accepted work"
    assert not receipts, "remote ahead"
    healthy = record["destinations"]["healthy"]
    during = sum(
        r["method"] == "POST"
        and r["status"] in (200, 201)
        and protocol["fault_start"] <= r["at"] < protocol["fault_end"]
        for r in healthy["requests"]
    )
    assert during >= protocol["minimum_healthy_during_fault"], "healthy destination starved"
    assert record["proposed"] == protocol["proposals"]
    assert record["accepted"] + record["rejected"] == record["proposed"]
    assert record["accepted"] == len(record["jobs"])
    if protocol.get("duration_seconds", 0) >= 300:
        assert record["reconciliation"]["safe_to_resume"]
        assert record["samples"] and all(s["application_processes"] for s in record["samples"])
        events = {}
        for permit in grants.values():
            events.setdefault(permit["destination"], []).extend(
                [(float(permit["issued_at"]), 1), (float(permit["finished_at"]), -1)]
            )
        for lane_events in events.values():
            active = 0
            for _, delta in sorted(lane_events):
                active += delta
                assert active <= protocol["client_concurrency"], "client concurrency exceeded"
        assert all(
            q["pending"] <= protocol["backlog_limit"] for s in record["samples"] for q in s["queues"]
        ), "backlog bound exceeded"
        if protocol["fault"] != "healthy":
            delivered = record["destinations"]["healthy"]["alerts"]
            assert delivered == record["alerts"], "alert readback mismatch"
            assert any(a["state"] == "firing" for a in delivered), "no actual alert"
            assert delivered[-1]["state"] == "cleared", "alert never cleared"
    rates = rate_observations(record)
    return {
        "status": "passed",
        "rate_acceptance_contract": "database_assigned_fixed_window_permit_issuance",
        "accepted": record["accepted"],
        "rejected": record["rejected"],
        "effects": sum(j["status"] == "succeeded" for j in record["jobs"]),
        "healthy_successes_during_fault": during,
        "permit_issuance_violations": rates["permit_issuance_buckets_above_limit"],
        "rate_observations": rates,
        "duplicate_effects": 0,
        "unaccounted_jobs": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    parser.add_argument(
        "--source-map",
        type=Path,
        help="Explicit archived-source mapping; never substitutes execution results",
    )
    args = parser.parse_args()
    source_map = json.loads(args.source_map.read_text(encoding="utf-8"))["files"] if args.source_map else {}
    if args.record.is_dir():
        manifest = json.loads((args.record / "artifact-manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest["files"].items():
            path = (args.record / name).resolve()
            if not path.is_relative_to(args.record.resolve()):
                raise ValueError("unsafe manifest path")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("artifact digest mismatch: " + name)
        results = []
        for path in sorted(args.record.glob("run-*/observations.json.gz")):
            record = json.loads(gzip.decompress(path.read_bytes()))
            for name, expected in record["protocol"]["source_sha256"].items():
                source = Path(source_map.get(name, name)).resolve()
                if not source.is_relative_to(Path.cwd().resolve()):
                    raise ValueError("unsafe source path")
                if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                    raise ValueError("execution source mismatch: " + name)
            results.append(check(record))
        if not results:
            raise ValueError("no recorded runs")
        print(json.dumps(results, indent=2))
    else:
        raw = (
            gzip.decompress(args.record.read_bytes())
            if args.record.suffix == ".gz"
            else args.record.read_bytes()
        )
        print(json.dumps(check(json.loads(raw)), indent=2))
