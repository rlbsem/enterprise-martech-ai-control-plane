"""Bounded local multi-process workload. Never uses AWS, models or vendor credentials."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import time
import threading
from uuid import uuid4, uuid5, NAMESPACE_URL

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from controlplane import db, service
from controlplane.contracts import Proposal, SourceEvent
from controlplane.policy import digest
from controlplane.operations import recovery_snapshot
from check_isolation import check
from isolation_resources import observe


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--admin", required=True, help="Local disposable PostgreSQL cluster only")
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--proposals", type=int, default=5000)
    parser.add_argument("--request-limit", type=int, default=10)
    parser.add_argument("--fault", choices=["429", "503", "mixed", "healthy"], default="429")
    args = parser.parse_args()
    from psycopg.conninfo import conninfo_to_dict

    if conninfo_to_dict(args.admin).get("host") not in ("localhost", "127.0.0.1"):
        raise ValueError("Loopback PostgreSQL only")
    if (
        args.output.exists()
        or not 20 <= args.duration <= 600
        or not 20 <= args.proposals <= 10000
        or not 2 <= args.request_limit <= 1000
    ):
        raise ValueError("Fresh output and bounded workload required")
    args.output.mkdir(parents=True)
    prefix = "isolation_" + uuid4().hex[:10]
    dsns = {}
    processes = []
    monitor = None
    stop = threading.Event()
    samples = []
    alerts = []
    monitor_errors = []
    logs = []
    try:
        with psycopg.connect(args.admin, autocommit=True) as c:
            for name in ("control", "healthy", "affected"):
                database = prefix + "_" + name
                c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                dsns[name] = make_conninfo(args.admin, dbname=database)
        for dsn in dsns.values():
            db.migrate(dsn)
        os.environ["CONTROL_DSN"] = make_conninfo(
            dsns["control"], user="control_app", password="local-app-only"
        )
        os.environ["EXECUTOR_TOKEN"] = "synthetic-isolation-only"
        os.environ["REMOTE_TIMEOUT"] = "0.5"
        urls = {name: f"http://127.0.0.1:{port()}" for name in ("healthy", "affected")}
        start = time.time() + 5
        protocol = {
            "version": 2,
            "duration_seconds": args.duration,
            "proposals": args.proposals,
            "workers": 4,
            "requests_per_second": args.request_limit,
            "dispatch_limit": args.request_limit - 1,
            "client_concurrency": 2,
            "backlog_limit": 128,
            "permit_ttl_seconds": 30,
            "fault_start": start + args.duration * 0.1,
            "fault_end": start + args.duration * 0.3,
            "fault": args.fault,
            "minimum_healthy_during_fault": max(1, args.proposals // 100),
            "input_interval_seconds": args.duration * 0.8 / args.proposals,
            "clock": "same-host UTC wall clock for arrivals; monotonic for elapsed duration",
            "scope": "local PostgreSQL, real HTTP/processes, synthetic effects; no cloud acceptance",
        }
        protocol["source_sha256"] = {
            p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("src", "scripts")
            for p in sorted(Path(folder).rglob("*"))
            if p.suffix in (".py", ".sql")
        }
        (args.output / "protocol.json").write_text(
            json.dumps(protocol, indent=2), encoding="utf-8", newline="\n"
        )

        def spawn(command, env, name):
            log = (args.output / (name + ".log")).open("w", encoding="utf-8", newline="\n")
            logs.append(log)
            p = subprocess.Popen(
                command,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            processes.append(p)
            return p

        for name in urls:
            env = {
                **os.environ,
                "REMOTE_DSN": make_conninfo(
                    dsns[name], user="synthetic_remote", password="local-remote-only"
                ),
                "INJECTOR_TOKEN": "synthetic-isolation-injector",
                "RECEIVER_JOURNAL": str((args.output / (name + ".sqlite")).resolve()),
                "FAULT_START": str(protocol["fault_start"]),
                "FAULT_END": str(protocol["fault_end"]),
                "FAULT_MODE": args.fault if name == "affected" else "healthy",
            }
            for key in ("CONTROL_DSN", "CONTROL_ADMIN_DSN", "TEST_ADMIN_DSN"):
                env.pop(key, None)
            spawn(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "scripts.isolation_receiver:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    urls[name].split(":")[-1],
                    "--no-access-log",
                ],
                env,
                name,
            )
            for _ in range(100):
                try:
                    if httpx.get(urls[name] + "/health", timeout=1, trust_env=False).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError("receiver startup failed")
        with db.connect(dsns["control"]) as c:
            for name in urls:
                c.execute(
                    "INSERT INTO cp.destinations VALUES (%s,%s,%s,%s,2,128)",
                    (name, urls[name], "EXECUTOR_TOKEN", args.request_limit),
                )
                c.execute("INSERT INTO cp.destination_routes VALUES (%s,%s)", (name, name))
        customers = []
        for i in range(120):
            customers.append(
                service.ingest(
                    "crm",
                    SourceEvent(
                        event_id=f"account-{i}",
                        subject=f"account-{i}",
                        sequence=1,
                        attributes={
                            "email": f"account-{i}@example.test",
                            "owner": "healthy" if i % 2 else "affected",
                        },
                    ),
                )["customer_id"]
            )
        # Seal the actual start after setup; no traffic or qualifying observations precede it.
        # Receiver fault times above are fixed, so setup overruns invalidate the run rather than move the fault.
        if time.time() >= protocol["fault_start"]:
            raise RuntimeError("setup consumed predeclared fault start; preserve and use a fresh run")
        workers = []
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CONTROL_ADMIN_DSN", "TEST_ADMIN_DSN", "REMOTE_DSN")
        }
        for i in range(4):
            workers.append(spawn([sys.executable, "-m", "controlplane", "worker"], env, f"worker-{i}"))

        def watch():
            firing = False
            try:
                while not stop.is_set():
                    with db.connect(dsns["control"]) as c:
                        queues = c.execute("""SELECT r.destination,count(*) AS pending,
                            max(extract(epoch FROM clock_timestamp()-p.created_at)) AS oldest_age
                            FROM cp.jobs j JOIN cp.job_routes r USING(id) JOIN cp.proposals p USING(id)
                            WHERE j.status IN ('ready','retry','leased') GROUP BY r.destination""").fetchall()
                        connections = c.execute("SELECT count(*) AS n FROM pg_stat_activity").fetchone()["n"]
                    resources = []
                    for process_id in [os.getpid()] + [p.pid for p in processes if p.poll() is None]:
                        resources.append({"pid": process_id, **observe(process_id)})
                    samples.append(
                        {
                            "at": time.time(),
                            "queues": queues,
                            "database_connections": connections,
                            "application_processes": resources,
                        }
                    )
                    affected = next((q["pending"] for q in queues if q["destination"] == "affected"), 0)
                    state = affected >= 10
                    if state != firing:
                        event = {
                            "destination": "affected",
                            "state": "firing" if state else "cleared",
                            "at": time.time(),
                        }
                        response = httpx.post(
                            urls["healthy"] + "/local-alerts",
                            json=event,
                            headers={"Authorization": "Bearer synthetic-isolation-injector"},
                            trust_env=False,
                            timeout=2,
                        )
                        response.raise_for_status()
                        alerts.append(event)
                        firing = state
                    stop.wait(1)
            except Exception as exc:
                monitor_errors.append(type(exc).__name__)

        monitor = threading.Thread(target=watch)
        monitor.start()
        accepted = rejected = 0
        admissions = []
        tick = time.monotonic()
        for i in range(args.proposals):
            due = start + i * protocol["input_interval_seconds"]
            if due > time.time():
                time.sleep(due - time.time())
            command = Proposal(
                request_id=uuid5(NAMESPACE_URL, f"isolation/v1/{i}"),
                customer_id=customers[i % 120],
                expected_revision=1,
                action="sync_profile",
                rationale="Synthetic isolation workload",
            )
            began = time.monotonic()
            try:
                service.propose("agent", command)
                accepted += 1
                status = "accepted"
            except ValueError as exc:
                if str(exc) != "destination_overloaded":
                    raise
                rejected += 1
                status = "overloaded"
            admissions.append(
                {
                    "at": time.time(),
                    "destination": "healthy" if i % 2 else "affected",
                    "status": status,
                    "latency_ms": (time.monotonic() - began) * 1000,
                }
            )
            if any(p.poll() is not None for p in workers):
                raise RuntimeError("worker exited")
        while time.time() < start + args.duration:
            time.sleep(0.2)
        stop.set()
        monitor.join(timeout=10)
        if monitor_errors:
            raise RuntimeError("observation failed: " + str(monitor_errors))
        for p in workers:
            p.terminate()
            p.wait(timeout=10)
        with db.connect(dsns["control"]) as c:
            jobs = c.execute("""SELECT j.id,j.status,j.attempts,j.reason,r.destination,
                j.remote_result->>'receipt' AS receipt,j.mutation,
                (SELECT count(*) FROM cp.audit a WHERE a.subject=j.id::text
                 AND a.kind='claim') AS claims,
                extract(epoch FROM p.created_at) AS created_at,
                (SELECT extract(epoch FROM max(a.at)) FROM cp.audit a WHERE a.subject=j.id::text
                 AND a.kind='execution_result') AS completed_at
                FROM cp.jobs j JOIN cp.job_routes r USING(id) JOIN cp.proposals p USING(id)""").fetchall()
            grants = c.execute("""SELECT r.token,r.destination,r.window_id,r.kind,
                    extract(epoch FROM r.issued_at) AS issued_at,
                    extract(epoch FROM coalesce(c.finished_at,r.issued_at+interval '30 seconds')) AS finished_at
                    FROM cp.capacity_requests r LEFT JOIN cp.capacity_completions c USING(token)""").fetchall()
        reconciliation = recovery_snapshot(
            dsns["control"],
            {"healthy": dsns["healthy"], "affected": dsns["affected"], "__legacy__": dsns["control"]},
        )
        if not reconciliation["safe_to_resume"]:
            raise RuntimeError("independent destination reconciliation blocked")
        for job in jobs:
            job["hash"] = digest(job.pop("mutation"))
        destinations = {}
        for name in urls:
            with db.connect(dsns[name]) as c:
                effects = c.execute("SELECT key,hash,receipt FROM remote.effects").fetchall()
            with sqlite3.connect(args.output / (name + ".sqlite")) as c:
                requests = [json.loads(row[0]) for row in c.execute("SELECT record FROM observations")]
            with sqlite3.connect(args.output / (name + ".sqlite")) as c:
                has_alerts = c.execute("SELECT 1 FROM sqlite_master WHERE name='alerts'").fetchone()
                read_alerts = (
                    [json.loads(row[0]) for row in c.execute("SELECT record FROM alerts")]
                    if has_alerts
                    else []
                )
            destinations[name] = {"effects": effects, "requests": requests, "alerts": read_alerts}
        record = {
            "protocol": protocol,
            "accepted": accepted,
            "rejected": rejected,
            "proposed": args.proposals,
            "admissions": admissions,
            "samples": samples,
            "alerts": alerts,
            "jobs": jobs,
            "reconciliation": reconciliation,
            "grants": grants,
            "destinations": destinations,
            "elapsed_seconds": time.monotonic() - tick,
            "observed_at": datetime.now(UTC).isoformat(),
            "machine": {
                "os": platform.platform(),
                "python": platform.python_version(),
                "cpus": os.cpu_count(),
            },
        }
        # Normalize UUID values before an independent check or publication.
        record = json.loads(json.dumps(record, default=str))
        (args.output / "observations.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8", newline="\n"
        )
        current = {
            p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("src", "scripts")
            for p in sorted(Path(folder).rglob("*"))
            if p.suffix in (".py", ".sql")
        }
        if current != protocol["source_sha256"]:
            raise RuntimeError("source changed during acceptance; observations retained but not qualified")
        result = check(record)
        (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8", newline="\n")
        print(json.dumps(result))
    finally:
        stop.set()
        if monitor is not None:
            monitor.join(timeout=10)
        for p in processes:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)
        for log in logs:
            log.close()
        # Only this run's exact generated databases; no external inventory or cloud calls.
        with psycopg.connect(args.admin, autocommit=True) as c:
            for dsn in dsns.values():
                name = conninfo_to_dict(dsn)["dbname"]
                c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        with psycopg.connect(args.admin) as c:
            names = [conninfo_to_dict(dsn)["dbname"] for dsn in dsns.values()]
            remaining = c.execute(
                "SELECT count(*) FROM pg_database WHERE datname=ANY(%s)", (names,)
            ).fetchone()[0]
        (args.output / "cleanup.json").write_text(
            json.dumps(
                {
                    "created_databases": len(dsns),
                    "remaining_generated_databases": remaining,
                    "method": "separate post-drop catalog connection; exact generated names",
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        if remaining:
            raise RuntimeError("local generated database cleanup incomplete")


if __name__ == "__main__":
    main()
