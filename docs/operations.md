# Operator runbook

## Local execution

The easiest configured path is `docker compose run --build --rm demo`. It runs assertions, prints the three scenarios and leaves the API, downstream and PostgreSQL dependencies running. `docker compose down` stops them and preserves the project volume. To operate the queue continuously, without the deterministic demonstration harness:

```bash
docker compose --profile serve up --build -d api remote worker
```

Do not run a continuous worker concurrently with `demo`: the harness deliberately controls when approval, revocation and execution occur. The automated suite uses an isolated database and its own HTTP subprocesses.

The synthetic API schema is available at `http://127.0.0.1:8000/docs`. All business routes require a role token; the read-only health endpoint is public. Compose token values are inspectable sample configuration. The API process does not receive the executor token or remote database login.

### Executed native alternative

Install the locked dependencies and editable package as shown in the README. Provide an existing PostgreSQL 17 instance and create two **disposable** databases owned by the administrator you will use: `control_demo` and `control_test`. The supplied bootstrap creates the `control_app` and `synthetic_remote` roles if missing. Use a dedicated local instance because these role names and local passwords are shared across its databases. Do not point this setup at a shared production cluster.

PowerShell, with example local-only credentials:

```powershell
$env:CONTROL_ADMIN_DSN='postgresql://postgres:local-admin-only@127.0.0.1:5432/control_demo'
python scripts/local_demo.py
$env:TEST_ADMIN_DSN='postgresql://postgres:local-admin-only@127.0.0.1:5432/control_test'
python scripts/verify.py
```

macOS/Linux:

```bash
export CONTROL_ADMIN_DSN='postgresql://postgres:local-admin-only@127.0.0.1:5432/control_demo'
python scripts/local_demo.py
export TEST_ADMIN_DSN='postgresql://postgres:local-admin-only@127.0.0.1:5432/control_test'
python scripts/verify.py
```

Replace the connection details with your local instance. The launcher bootstraps the demo schema, generates temporary API credentials, starts separate loopback HTTP processes, runs the simulator and stops those processes. It leaves the PostgreSQL database intact. Server logs go under ignored `work/native-demo/`. Repeating the demo adds a new synthetic customer and independent request IDs.

Verification refuses database names that do not end in `_test`, then deliberately drops/recreates its `cp` and `remote` schemas and migration ledger. It never silently skips missing PostgreSQL. Do not run verification concurrently against the same test database. It executes real child-process death and actual socket timeouts, not mocked HTTP clients.

## Investigate an exception

Use `GET /exceptions?after=0` with the operator credential. Results are ordered in pages of at most 100. Record the exception ID, category, subject, status and details. Use `GET /proposals/{id}` for the saved proposal, approval, work status, reason, uncertainty, mutation and receipt. `GET /audit?after=0` provides the corresponding decision history with actor and policy hash. Advance `after` to the last ID to retrieve later pages; do not assume the first page is the entire history.

For identity cases, check source subject and candidate UUIDs against independent evidence. Submit `/identity-links` with source, subject, target UUID, current revision and evidence. Existing identities cannot be rebound. Request a new upstream event ID after successful linking, then acknowledge the original exception with a note. Acknowledgement is administrative closure, not a data mutation or retry.

For dead execution work, establish whether the remote outcome is known before replay. For a retryable recovered adapter, use:

```json
{"disposition": "requeue", "note": "Adapter recovered; reconcile original intent and recheck current permission"}
```

POST this to `/exceptions/{exception_id}/resolve` as operator. Requeue is permitted only for a dead job's retry-exhausted or remote-rejected exception. All other categories require acknowledgement or a new governed event/proposal. A resolved exception cannot be resolved again. A changed customer revision or expired approval will block this same intent; submit a new proposal if a new action is intended. Never change the stored mutation or create a new key just to hide an uncertain result.

## Operational visibility

The example provides queryable exception/audit APIs and structured database state. It does not deploy a metrics or paging service. Useful operator queries through an administrator's local SQL session include:

```sql
SELECT status, uncertain, count(*) FROM cp.jobs GROUP BY status, uncertain;
SELECT id, attempts, reason, available_at, lease_until FROM cp.jobs
WHERE status IN ('retry','leased','dead','blocked') ORDER BY available_at;
SELECT category, count(*) FROM cp.exceptions WHERE status='open' GROUP BY category;
```

Watch oldest ready work, expired leases, unknown outcomes, rejection categories and retry-budget exhaustion. At production scale, convert these to measured service indicators with ownership and thresholds established from real traffic. Avoid publishing customer payloads as metric labels.

The SQL migration is transactional and checksum checked. Application logins never run migrations. Backups, point-in-time restore drills, key rotation, data retention and audit export are production requirements, not automated by this repository. An owner can disable triggers or change grants, so audit protection must be supplemented by independently controlled exports for stronger tamper evidence.
