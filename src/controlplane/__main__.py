import argparse
import os

import psycopg
from psycopg import sql

from . import db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "bootstrap-local", "worker"])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.command == "bootstrap-local":
        # Explicit local setup only; deployment migrations should receive pre-provisioned roles.
        with psycopg.connect(os.environ["CONTROL_ADMIN_DSN"], autocommit=True) as c:
            for role, password in [("control_app", os.environ["APP_DB_PASSWORD"]),
                                   ("synthetic_remote", os.environ["REMOTE_DB_PASSWORD"])]:
                if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                    c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        db.migrate(os.environ["CONTROL_ADMIN_DSN"])
    elif args.command == "migrate":
        db.migrate(os.environ["CONTROL_ADMIN_DSN"])
    else:
        from .worker import once, run
        once() if args.once else run()


if __name__ == "__main__":
    main()
