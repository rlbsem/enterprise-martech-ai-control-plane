"""Bounded child-process diagnostics without credentials or private addresses."""

import hashlib
import json
import re

from psycopg import Error
from psycopg.conninfo import conninfo_to_dict


def sanitized(text, credentials=()):
    for value in sorted(set(credentials), key=len, reverse=True):
        if value:
            text = text.replace(value, "[redacted-credential]")
    patterns = [
        r"postgres(?:ql)?://[^\s]+", r"(?:AKIA|ASIA)[A-Z0-9]{16}",
        r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+", r"arn:aws:[^\s\"',}]+",
        r"[a-z0-9.-]+\.rds\.amazonaws\.com", r"\b\d{12}\b",
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[redacted-private-value]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)((?:password|authorization|access_token|secret_access_key)\s*[=:]\s*)[^\s,;]+",
                  r"\1[redacted-credential]", text)
    return text


def process_observation(runtime):
    credentials = [*runtime.tokens.values(), runtime.executor, runtime.injector]
    for dsn in runtime.dsns.values():
        credentials.append(dsn)
        try:
            credentials.extend(conninfo_to_dict(dsn).get(k, "") for k in ("password", "host", "hostaddr"))
        except (Error, ValueError):
            pass  # The full malformed string is still redacted; never mask its startup failure.
    records, used = [], 0
    files = sorted(runtime.folder.glob("*.log"), key=lambda p: int(p.name.split("-", 1)[0]))
    for path in files[-12:]:
        size = path.stat().st_size
        with path.open("rb") as stream:
            raw = stream.read(32_769)
        truncated = size > 32_768
        raw = raw[:32_768]
        safe = sanitized(raw.decode("utf-8", errors="replace"), credentials)
        row = {"file": path.name, "bytes_observed": size, "truncated": truncated,
               "captured_prefix_sha256": hashlib.sha256(raw).hexdigest(), "sanitized_log": safe}
        used += len(json.dumps(row).encode())
        if used > 100_000:
            break
        records.append(row)
    return {
        "schema": "native-process-diagnostics.v1",
        "processes": [{"service": role, "return_code": process.poll()} for role, process in runtime.processes],
        "logs": records,
        "bounds": {"maximum_files": 12, "prefix_bytes_per_file": 32_768, "maximum_log_json_bytes": 100_000},
        "all_files_captured": len(records) == len(files),
        "raw_logs_published": False,
    }
