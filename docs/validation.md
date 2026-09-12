# Validation and skeptical review

## Executed evidence boundary

The local suite passed **68 tests, zero failures, zero skips**, against real PostgreSQL 17.11 on Windows with Python 3.12.14. One Hypothesis test additionally exercises 100 generated lifecycle cases. The tests launch independent FastAPI and synthetic downstream HTTP processes, use separate PostgreSQL logins, and kill a real worker child process after a remote commit. Network calls are actual loopback HTTP; the database is not mocked.

The exact runtime, timings, dependency versions, installation layout and source SHA-256 values are in [verification.json](evidence/verification.json). Individual test results are in [tests.xml](evidence/tests.xml). The [readable proof](evidence/report.md) is generated from asserted scenarios, not manually invented output.

`scripts/verify.py` checks that the installed package matches the repository source, executes lint and the complete suite, refuses to publish if source changes during validation, and only then copies staged evidence into the requested directory. The final clean-install check uses a built wheel in a second Python environment, rather than depending on an editable checkout. SQL migration package data is exercised by that installed package. `pip check` found no dependency conflicts. The native demonstration also executes against a separate retained demo database and can be repeated with new synthetic IDs.

PostgreSQL was an isolated temporary local runtime downloaded from the official EDB binary distribution linked by PostgreSQL's Windows downloads page. Its loopback-only bootstrap used trust authentication on the disposable build host; application schema/role permissions were still enforced by PostgreSQL. Compose uses explicit development passwords. This local bootstrap is not a production authentication example.

| Category | Status |
|---|---|
| Governance, API, PostgreSQL integration, remote HTTP, concurrency, killed-worker recovery | Executed locally |
| Fresh dependency installation, built-wheel execution, packaged SQL, native demo | Executed locally |
| Compose topology, health checks, credentials and startup commands | `config --quiet` passed using official checksum-verified Compose v2.39.4; no container runtime execution claimed |
| GitHub Actions integration and container demo jobs | Workflow supplied; not pushed or observed running for this repository |
| Real CRM/email/LLM provider integrations | Not implemented; synthetic downstream only |
| Cloud deployment, scale benchmarks, failover, identity provider, immutable external audit archive | Reference/known-limit material only |

## Review findings and corrections

| Skeptical review question | Decision or correction | Proof |
|---|---|---|
| Could equal email silently merge customers? | Exact email only produces review candidates; even a single match is insufficient | Identity review and ambiguity tests |
| Are unrelated upstream sequence clocks compared? | Enforce one authoritative subject per source per customer; forbid rebinds | Uniqueness constraint and API rejection tests |
| Can null input be interpreted as a privacy reset? | Reject explicit nulls, empty patches and type coercion | Durable malformed-contract exceptions |
| Can a caller claim to be the consent source? | Authority derives from a configured credential; extra source/actor fields are refused | Spoofed-source and role matrix tests |
| Can lower authority win by being newer? | Ownership precedes sequence; generic lifecycle order precedes acceptance | Concurrent source update, no-regress and source-authority tests |
| Can approval become permanent permission? | Exact revision, policy hash and two expiries; duplicate approval cannot extend validity | Stale and expired approval tests |
| Can replay bypass later revocation? | Reconcile existing effects first; new dispatch rechecks state, policy and approval | Both committed and uncommitted timeout cases after revocation |
| Can a lease stop an old HTTP request? | No such claim; fence local completion and deduplicate effects remotely | Expired token test and eight simultaneous remote retransmissions |
| Can process death lose the work or receipt? | Persist intent before HTTP; replacement reconciles original key | Actual process kill after remote commit |
| Is a server error proof that nothing happened? | 5xx/transport failures remain uncertain; lookup precedes resend | Failure injection and retry-exhaustion tests |
| Can retry exhaustion hide uncertainty? | Preserve uncertainty in dead work; reviewed requeue preserves the exact intent | Dead-letter recovery and stale-context requeue tests |
| Could application SQL rewrite audit history? | Revoke mutation rights, add immutable triggers, separate migration owner; reject privileged runtime roles during migration | PostgreSQL permission and accidental owner-update tests |
| Could a source ID or policy be rewritten to erase the explanation? | Immutable event history, immutable policy snapshots, content-checked migration | Conflict, policy snapshot/change and migration mutation tests |
| Does a fresh package omit SQL or use different code? | Package SQL; compare installed bytes before verification; test a wheel in a fresh environment | Final installed-wheel evidence |
| Does the documentation overstate privacy or delivery guarantees? | Explicit dispatch cutoff, in-flight limitation and provider idempotency contract | Failure and known-limits documents |

The review also found a Windows console encoding failure in the evidence reporter after the tests had passed. The report now uses portable ASCII for the console table, and the entire verification command was rerun successfully. This was a delivery defect, not a failed governance assertion.

## Benchmark inspected before implementation

The public [game telemetry repository](https://github.com/rlbsem/game-telemetry-analytics-engineering) was fetched and inspected before building this system. Public main resolved during inspection to `205491319fd25c0ad107e25e0e25d2003cb2a5d6`; the downloaded main archive SHA-256 was `d3110427797253f54b67a12ba0018afa8747576db34ec279867eb4951d3e9b97`.

The inspection covered its README, architecture/validation documents, Python admission implementation and negative tests, executable dbt incremental model, generated proof and GitHub Actions workflow. Its credibility comes from explicit grains and transaction boundaries, exercised replay/conflict cases, inspectable generated evidence and honest separation of local execution from cloud/orchestrator references. This project applies that evidentiary standard to transactional customer governance; it does not copy the warehouse/dbt architecture.

No artificial commit history, CI badge, vendor deployment, client endorsement or production experience has been added. The supplied source and evidence are the basis of the portfolio claim.

The auxiliary [build checks](evidence/build-checks.json) record dependency consistency, the tested wheel hash, Compose configuration validation and the retained native demo receipt. These complement the full-suite source manifest; they do not claim that a Docker engine ran.
