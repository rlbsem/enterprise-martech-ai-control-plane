"""Finite owner-routed destinations and database-coordinated fixed-window request permits.

Configuration is migration-owner controlled; requests cannot supply an endpoint.
One request/second is reserved for receipt lookup. Permits live for 30 seconds,
longer than the enforced client timeout (at most five seconds). This bounds client
concurrency; a remote server can continue processing after client disconnection.
"""

import os
import httpx
from uuid import uuid4
from urllib.parse import urlsplit

from . import db


class Deferred(Exception):
    pass


class Client:
    """Each actual network call, including receipt lookup, needs its own permit."""

    def __init__(self, client, route):
        self.client, self.route = client, route

    def request(self, method, path, **kwargs):
        token = acquire(self.route, "receipt" if method == "GET" else "dispatch")
        if token is None:
            raise Deferred()
        failed = True
        cooldown = 2
        try:
            kwargs["headers"] = {**kwargs.get("headers", {}), "X-Capacity-Permit": str(token)}
            response = self.client.request(method, path, **kwargs)
            failed = response.status_code == 429 or response.status_code >= 500
            if response.status_code == 429:
                try:
                    cooldown = max(1, min(300, int(response.headers.get("Retry-After", "5"))))
                except ValueError:
                    cooldown = 5
            return response
        except httpx.TransportError:
            raise
        finally:
            release(self.route, token, failed, cooldown)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


def admission(c, owner):
    if not c.execute("SELECT 1 FROM cp.destinations LIMIT 1").fetchone():
        return None
    route = c.execute(
        """SELECT d.* FROM cp.destinations d JOIN cp.destination_routes r
        ON r.destination=d.id WHERE r.owner=%s""",
        (owner,),
    ).fetchone()
    if not route:
        raise ValueError("destination_not_authorized")
    check_backlog(c, route)
    return route["id"]


def check_backlog(c, route):
    # Serializes count + admission across API processes, per destination only.
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,721))", (route["id"],))
    n = c.execute(
        """SELECT count(*) AS n FROM cp.job_routes r JOIN cp.proposals p USING(id)
        LEFT JOIN cp.jobs j USING(id) WHERE r.destination=%s AND
        (j.status IN ('ready','retry','leased') OR (j.id IS NULL AND p.expires_at>clock_timestamp()))""",
        (route["id"],),
    ).fetchone()["n"]
    if n >= route["backlog_limit"]:
        raise ValueError("destination_overloaded")


def readmission(c, key):
    route = c.execute(
        """SELECT d.* FROM cp.job_routes r JOIN cp.destinations d
        ON d.id=r.destination WHERE r.id=%s""",
        (key,),
    ).fetchone()
    if route:
        check_backlog(c, route)


def metrics():
    """Bounded destination labels; no customer identities or payloads."""
    with db.connect() as c:
        return c.execute("""SELECT d.id AS destination,
            (SELECT count(*) FROM cp.jobs j JOIN cp.job_routes r USING(id)
             WHERE r.destination=d.id AND j.status IN ('ready','retry','leased')) AS backlog,
            (SELECT count(*) FROM cp.request_permits p WHERE p.destination=d.id
             AND p.expires>clock_timestamp()) AS active_client_permits,
            coalesce(s.next_probe>clock_timestamp(),false) AS request_deferred
            FROM cp.destinations d LEFT JOIN cp.capacity_state s ON s.destination=d.id ORDER BY d.id""").fetchall()


def bind(c, key, destination):
    if destination:
        c.execute("INSERT INTO cp.job_routes VALUES (%s,%s)", (key, destination))


def destination(job):
    with db.connect() as c:
        route = c.execute(
            """SELECT d.* FROM cp.job_routes r JOIN cp.destinations d
            ON d.id=r.destination WHERE r.id=%s""",
            (job["id"],),
        ).fetchone()
        if route is None and c.execute("SELECT 1 FROM cp.destinations LIMIT 1").fetchone():
            raise ValueError("legacy_route_requires_review")
    if route:
        url = urlsplit(route["url"])
        if url.scheme not in ("http", "https") or url.username or url.password or url.query or url.fragment:
            raise ValueError("invalid_destination_url")
        if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("plaintext_destination_not_loopback")
        if not os.environ.get(route["credential_env"]):
            raise ValueError("destination_credential_missing")
    return route


def acquire(route, kind):
    with db.connect() as c:
        c.execute(
            "INSERT INTO cp.capacity_state(destination) VALUES (%s) ON CONFLICT DO NOTHING", (route["id"],)
        )
        state = c.execute(
            """SELECT window_id,used,floor(extract(epoch FROM clock_timestamp()))::bigint AS now_window,
            next_probe<=clock_timestamp() AS eligible FROM cp.capacity_state
            WHERE destination=%s FOR UPDATE""",
            (route["id"],),
        ).fetchone()
        c.execute(
            "DELETE FROM cp.request_permits WHERE destination=%s AND expires<=clock_timestamp()",
            (route["id"],),
        )
        live = c.execute(
            "SELECT count(*) AS n FROM cp.request_permits WHERE destination=%s", (route["id"],)
        ).fetchone()["n"]
        # A backwards wall-clock adjustment cannot reopen a previously spent bucket.
        state["now_window"] = max(state["now_window"], state["window_id"])
        used = state["used"] if state["window_id"] == state["now_window"] else 0
        limit = route["requests_per_second"] - int(kind == "dispatch")
        if not state["eligible"] or used >= limit or live >= route["concurrency"]:
            return None
        token = uuid4()
        c.execute(
            "INSERT INTO cp.request_permits VALUES (%s,%s,clock_timestamp()+interval '30 seconds',%s)",
            (token, route["id"], kind),
        )
        c.execute(
            "INSERT INTO cp.capacity_requests(token,destination,window_id,kind) VALUES (%s,%s,%s,%s)",
            (token, route["id"], state["now_window"], kind),
        )
        c.execute(
            """UPDATE cp.capacity_state SET window_id=%s,used=%s,last_served=clock_timestamp()
            WHERE destination=%s""",
            (state["now_window"], used + 1, route["id"]),
        )
        return token


def release(route, token, failed, cooldown=2):
    with db.connect() as c:
        c.execute("DELETE FROM cp.request_permits WHERE token=%s", (token,))
        c.execute("INSERT INTO cp.capacity_completions(token,failed) VALUES (%s,%s)", (token, failed))
        if failed:
            c.execute(
                """UPDATE cp.capacity_state SET next_probe=greatest(next_probe,
                clock_timestamp()+%s * interval '1 second') WHERE destination=%s""",
                (cooldown, route["id"]),
            )


def defer(job, attempted):
    with db.connect() as c:
        c.execute(
            """UPDATE cp.jobs SET status='retry',lease_token=NULL,lease_until=NULL,
            attempts=attempts-%s,available_at=clock_timestamp()+interval '0.1 seconds'
            WHERE id=%s AND lease_token=%s AND status='leased' """,
            (int(not attempted), job["id"], job["lease_token"]),
        )


def grants(c):
    """Migration-owner operation; registry writes remain administrator-only."""
    existing = {
        row["rolname"]
        for row in c.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('control_api','control_worker')")
    }
    for role in sorted(existing):
        c.execute(f"GRANT SELECT ON cp.destinations,cp.destination_routes,cp.job_routes TO {role}")
    if "control_api" in existing:
        c.execute("GRANT INSERT ON cp.job_routes TO control_api")
    if "control_worker" in existing:
        c.execute("GRANT SELECT,INSERT,UPDATE ON cp.capacity_state TO control_worker")
        c.execute("GRANT SELECT,INSERT,DELETE ON cp.request_permits TO control_worker")
        c.execute("GRANT SELECT,INSERT ON cp.capacity_requests TO control_worker")
        c.execute("GRANT SELECT,INSERT ON cp.capacity_completions TO control_worker")
