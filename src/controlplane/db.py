"""Explicit transaction scopes; migration ownership is separate from application login."""

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .policy import POLICY, POLICY_HASH, digest


def connect(dsn=None):
    return psycopg.connect(dsn or os.environ["CONTROL_DSN"], row_factory=dict_row,
                           connect_timeout=5, options="-c statement_timeout=10000 -c lock_timeout=5000")


def migrate(dsn):
    sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with connect(dsn) as c:
        c.execute("SELECT pg_advisory_xact_lock(381934)")
        roles = c.execute("""SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
            FROM pg_roles WHERE rolname IN ('control_app','synthetic_remote')""").fetchall()
        if len(roles) != 2 or any(any(v for k, v in role.items() if k != "rolname") for role in roles):
            raise RuntimeError("Pre-provision both application roles without administrative privileges")
        if c.execute("""SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
                        WHERE r.rolname IN ('control_app','synthetic_remote') LIMIT 1""").fetchone():
            raise RuntimeError("Application roles must not inherit other role memberships")
        c.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations(version int PRIMARY KEY, hash text NOT NULL)")
        row = c.execute("SELECT hash FROM public.schema_migrations WHERE version=1").fetchone()
        if row and row["hash"] != digest(sql):
            raise RuntimeError("Applied migration changed; create a new migration instead")
        if not row:
            c.execute(sql)
            c.execute("INSERT INTO public.schema_migrations VALUES (1,%s)", (digest(sql),))
        c.execute("INSERT INTO cp.policies VALUES (%s,%s) ON CONFLICT DO NOTHING", (POLICY_HASH, Jsonb(POLICY)))


def audit(c, actor, kind, subject, detail):
    c.execute("INSERT INTO cp.audit(actor,kind,subject,policy_hash,detail) VALUES (%s,%s,%s,%s,%s)",
              (actor, kind, str(subject), POLICY_HASH, Jsonb(detail)))


def exception(c, category, subject, detail):
    row = c.execute("INSERT INTO cp.exceptions(category,subject,detail) VALUES (%s,%s,%s) RETURNING id",
                    (category, str(subject), Jsonb(detail))).fetchone()
    return row["id"]


def customer(c, customer_id, lock=True):
    row = c.execute("SELECT * FROM cp.customers WHERE id=%s" + (" FOR UPDATE" if lock else ""),
                    (customer_id,)).fetchone()
    if row is None:
        raise LookupError("customer_not_found")
    return row
