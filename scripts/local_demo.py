"""Native alternative to Compose against an existing disposable PostgreSQL database."""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from psycopg.conninfo import make_conninfo


def main():
    admin = os.environ["CONTROL_ADMIN_DSN"]
    env = {**os.environ, "APP_DB_PASSWORD": "local-app-only", "REMOTE_DB_PASSWORD": "local-remote-only"}
    env["CONTROL_DSN"] = make_conninfo(admin, user="control_app", password="local-app-only")
    env["REMOTE_DSN"] = make_conninfo(admin, user="synthetic_remote", password="local-remote-only")
    env["API_TOKENS"] = json.dumps({r: secrets.token_urlsafe(24) for r in
                                    ["crm", "consent", "privacy", "scoring", "agent", "approver", "operator"]})
    env["EXECUTOR_TOKEN"], env["INJECTOR_TOKEN"] = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    subprocess.run([sys.executable, "-m", "controlplane", "bootstrap-local"], env=env, check=True)
    folder = Path("work/native-demo")
    folder.mkdir(parents=True, exist_ok=True)
    processes, logs = [], []
    try:
        for service, variable in [("mock", "DOWNSTREAM_URL"), ("api", "CONTROL_URL")]:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            env[variable] = f"http://127.0.0.1:{port}"
            service_env = {k: v for k, v in env.items() if k not in
                           ["CONTROL_ADMIN_DSN", "TEST_ADMIN_DSN", "APP_DB_PASSWORD", "REMOTE_DB_PASSWORD"]}
            remove = ["REMOTE_DSN", "EXECUTOR_TOKEN", "INJECTOR_TOKEN"] if service == "api" else ["CONTROL_DSN", "API_TOKENS"]
            for key in remove:
                service_env.pop(key, None)
            log = (folder / f"{service}.log").open("w")
            logs.append(log)
            p = subprocess.Popen([sys.executable, "-m", "uvicorn", f"controlplane.{service}:app", "--host", "127.0.0.1",
                                  "--port", str(port), "--no-access-log"], stdout=log, stderr=log, env=service_env,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append(p)
            for _ in range(100):
                try:
                    if httpx.get(env[variable] + "/health", trust_env=False).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if p.poll() is not None:
                    raise RuntimeError(f"{service} exited; inspect work/native-demo/{service}.log")
                time.sleep(0.05)
            else:
                raise RuntimeError(f"{service} health check failed")
        subprocess.run([sys.executable, str(Path(__file__).with_name("demo.py"))], env=env, check=True)
    finally:
        for p in processes:
            p.terminate()
            p.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
